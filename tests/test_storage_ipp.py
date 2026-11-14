"""Tests for the SQLite store, the IPP layer and the fit engine."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

import io
from PIL import Image


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["NIIBOT_DATA"] = self.tmp.name
        # Reload the store so DATA_DIR/DB_PATH point at the temporary folder.
        import importlib

        from niibot import models

        import niibot.store as store_module

        self.store = importlib.reload(store_module)
        self.models = models
        self.store.ensure_dirs()

    def tearDown(self) -> None:
        os.environ.pop("NIIBOT_DATA", None)
        self.tmp.cleanup()

    def test_database_file_and_schema(self) -> None:
        self.assertTrue(self.store.DB_PATH.is_file())
        with self.store.connect() as db:
            tables = {
                row["name"]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        for expected in ("consumables", "labels", "settings", "uploads", "print_jobs", "profiles", "ui_state"):
            self.assertIn(expected, tables)

    def test_ui_state_round_trip(self) -> None:
        """Everything the front-end remembers lives here, not in the browser."""
        draft = {"spec": {"width_mm": 40, "height_mm": 30, "elements": []}, "view": "design"}
        self.store.save_ui_state({"draft": draft, "panelWidth": 320, "language": "pl"})
        state = self.store.load_ui_state()
        self.assertEqual(state["draft"], draft)
        self.assertEqual(state["panelWidth"], 320)
        self.assertEqual(state["language"], "pl")

        # a second write merges instead of replacing the whole set
        self.store.save_ui_state({"panelWidth": 360})
        state = self.store.load_ui_state()
        self.assertEqual(state["panelWidth"], 360)
        self.assertEqual(state["draft"], draft)

        # None removes a single key
        self.store.save_ui_state({"language": None})
        self.assertNotIn("language", self.store.load_ui_state())

    def test_ui_state_rejects_junk(self) -> None:
        self.store.save_ui_state({"ok": {"a": 1}, "": "empty key", "x" * 80: "too long"})
        self.assertEqual(set(self.store.load_ui_state()), {"ok"})
        with self.assertRaises(ValueError):
            self.store.save_ui_state({"huge": "x" * (self.store.UI_STATE_VALUE_LIMIT + 10)})

    def test_consumable_upsert_is_idempotent(self) -> None:
        entry = {
            "barcode": "6971501227736",
            "sku": "T40*15/2R-460WHITE",
            "name": "T40*15/2R-460WHITE",
            "width_mm": 40,
            "height_mm": 30,
            "rows": 2,
            "capacity": 460,
            "source": "vendor-cache",
        }
        self.store.upsert_consumable(entry)
        self.store.upsert_consumable({**entry, "capacity": 461})
        self.assertEqual(len(self.store.list_consumables()), 1)
        found = self.store.get_consumable_by_barcode("6971501227736")
        self.assertEqual(found["capacity"], 461)
        self.assertEqual(found["rows"], 2)

    def test_barcode_less_formats_keyed_by_sku(self) -> None:
        self.store.upsert_consumable({"sku": "T50*30", "name": "T50*30", "width_mm": 50, "height_mm": 30, "source": "builtin"})
        self.store.upsert_consumable({"sku": "T50*30", "name": "T50*30", "width_mm": 50, "height_mm": 30, "source": "builtin"})
        self.assertEqual(len(self.store.list_consumables()), 1)
        self.assertIsNone(self.store.get_consumable_by_barcode(None))

    def test_settings_and_labels_roundtrip(self) -> None:
        self.store.save_settings({"fit": {"mode": "cover"}})
        self.store.save_settings({"last_model": "B1"})
        self.assertEqual(self.store.load_settings()["fit"]["mode"], "cover")
        self.assertEqual(self.store.load_settings()["last_model"], "B1")

        spec = {"name": "Test", "width_mm": 40, "height_mm": 30, "elements": []}
        identifier = self.store.save_label(None, spec)
        self.assertEqual(self.store.load_label(identifier)["name"], "Test")
        self.assertEqual(len(self.store.list_labels()), 1)
        self.store.delete_label(identifier)
        self.assertEqual(self.store.list_labels(), [])

    def test_uploads_are_stored_as_blobs(self) -> None:
        buffer = io.BytesIO()
        Image.new("1", (8, 8), 1).save(buffer, format="PNG")
        image_id = self.store.save_upload("logo.png", buffer.getvalue())
        found = self.store.upload_bytes(image_id)
        self.assertIsNotNone(found)
        self.assertEqual(found[1], "image/png")
        self.assertTrue(self.store.upload_path(image_id).is_file())
        self.assertIsNone(self.store.upload_bytes("missing.png"))

    def test_statistics_count_copies(self) -> None:
        for copies, state in ((2, "done"), (1, "done"), (5, "error")):
            self.store.record_print_job(
                job_id="abc",
                state=state,
                started_at=0.0,
                finished_at=1.0,
                copies=copies,
                sku="T40*15/2R-460WHITE",
                paper_barcode="6971501227736",
            )
        stats = self.store.print_statistics()
        self.assertEqual(stats["jobs"], 3)
        self.assertEqual(stats["labels"], 3)  # only successful jobs count labels
        self.assertEqual(stats["done"], 2)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["by_sku"][0]["sku"], "T40*15/2R-460WHITE")


class IppTests(unittest.TestCase):
    def _request(self, operation: int, document: bytes = b"") -> bytes:
        body = struct.pack(">BBHI", 2, 0, operation, 7)
        body += b"\x01"
        for tag, name, value in (
            (0x47, b"attributes-charset", b"utf-8"),
            (0x48, b"attributes-natural-language", b"en"),
            (0x45, b"printer-uri", b"ipp://127.0.0.1:8765/ipp/print"),
            (0x49, b"document-format", b"image/png"),
        ):
            body += bytes([tag]) + struct.pack(">H", len(name)) + name + struct.pack(">H", len(value)) + value
        return body + b"\x03" + document

    def test_parse_print_job(self) -> None:
        from niibot import ipp

        document = b"\x89PNG\r\n\x1a\npayload"
        request = ipp.parse_request(self._request(ipp.OP_PRINT_JOB, document))
        self.assertEqual(request.operation, ipp.OP_PRINT_JOB)
        self.assertEqual(request.request_id, 7)
        self.assertEqual(request.get("document-format"), "image/png")
        self.assertEqual(request.get("printer-uri"), "ipp://127.0.0.1:8765/ipp/print")
        self.assertEqual(request.document, document)

    def test_printer_attributes_response(self) -> None:
        from niibot import ipp

        state = ipp.IppPrinterState(name="Niimbot B1", make_and_model="Niimbot B1", dpi=203, width_mm=40, height_mm=30)
        payload = ipp.build_printer_attributes(state, "ipp://127.0.0.1:8765/ipp/print")
        status = struct.unpack(">H", payload[2:4])[0]
        self.assertEqual(status, ipp.STATUS_OK)
        text = payload.decode("utf-8", "replace")
        for attribute in (
            "printer-name",
            "printer-state",
            "document-format-supported",
            "printer-resolution-supported",
            "media-default",
            "media-supported",
            "printer-is-accepting-jobs",
        ):
            self.assertIn(attribute, text)
        self.assertIn("custom_40x30mm", text)

    def test_error_response_has_no_job_group(self) -> None:
        from niibot import ipp

        request = ipp.parse_request(self._request(ipp.OP_VALIDATE_JOB))
        payload = ipp.build_simple_response(request, ipp.STATUS_NOT_POSSIBLE, message="no printer")
        self.assertEqual(struct.unpack(">H", payload[2:4])[0], ipp.STATUS_NOT_POSSIBLE)
        self.assertIn(b"status-message", payload)


class PwgRasterTests(unittest.TestCase):
    @staticmethod
    def _build(width: int, height: int, rows: list[bytes], compression: int = 0) -> bytes:
        header = bytearray(1796)
        header[0:4] = b"RaS2"
        struct.pack_into(">III", header, 8, width, height, 1)
        struct.pack_into(">II", header, 20, 1, (width + 7) // 8)  # bitsPerPixel, bytesPerLine
        struct.pack_into(">I", header, 32, 3)  # CUPS_CSPACE_K
        struct.pack_into(">I", header, 36, compression)
        payload = bytes(header)
        for row in rows:
            payload += bytes((1,)) + row  # repeat once, then the row bytes
        payload += b"\x00"
        return payload

    def test_one_bit_rows(self) -> None:
        from niibot import ipp

        # 8 px wide: 0b10000000 = first pixel black (1 = black in PWG black_1)
        raster = self._build(8, 2, [b"\x80", b"\x00"])
        image = ipp.decode_pwg_raster(raster)
        self.assertIsNotNone(image)
        self.assertEqual(image.size, (8, 2))
        self.assertFalse(bool(image.getpixel((0, 0))), "the first pixel is black")
        self.assertTrue(bool(image.getpixel((1, 0))), "the rest of the row is white")
        self.assertTrue(bool(image.getpixel((0, 1))))

    def test_repeat_count_expands_rows(self) -> None:
        from niibot import ipp

        header = bytearray(1796)
        header[0:4] = b"RaS2"
        struct.pack_into(">III", header, 8, 8, 3, 1)
        struct.pack_into(">II", header, 20, 1, 1)
        struct.pack_into(">I", header, 32, 3)
        struct.pack_into(">I", header, 36, 0)
        raster = bytes(header) + bytes((3,)) + b"\x80" + b"\x00"
        image = ipp.decode_pwg_raster(raster)
        self.assertEqual(image.size, (8, 3))
        for y in range(3):
            self.assertFalse(bool(image.getpixel((0, y))), f"row {y} is black at x=0")


class FitTests(unittest.TestCase):
    def test_contain_keeps_aspect_and_centres(self) -> None:
        from niibot.fit import FitOptions, fit_to_label

        source = Image.new("RGB", (400, 200), "black")
        result = fit_to_label(source, 320, 240, 203, FitOptions(mode="contain"))
        self.assertEqual(result.size, (320, 240))
        # A 2:1 source in a 4:3 box → 320×160 centred → top and bottom rows stay white.
        self.assertEqual(result.getpixel((160, 2)), 255)
        self.assertEqual(result.getpixel((160, 120)), 0)

    def test_cover_fills_whole_label(self) -> None:
        from niibot.fit import FitOptions, fit_to_label

        source = Image.new("RGB", (400, 200), "black")
        result = fit_to_label(source, 320, 240, 203, FitOptions(mode="cover"))
        for point in ((0, 0), (319, 239), (160, 120)):
            self.assertEqual(result.getpixel(point), 0)

    def test_offset_shifts_content(self) -> None:
        from niibot.fit import FitOptions, fit_to_label

        source = Image.new("RGB", (320, 240), "white")
        source.paste(Image.new("RGB", (40, 40), "black"), (140, 100))
        centred = fit_to_label(source, 320, 240, 203, FitOptions(mode="stretch"))
        shifted = fit_to_label(source, 320, 240, 203, FitOptions(mode="stretch", offset_x_mm=2))
        # 2 mm at 203 dpi ≈ 16 px to the right.
        self.assertFalse(bool(centred.getpixel((150, 120))))
        self.assertTrue(bool(shifted.getpixel((150, 120))))
        self.assertFalse(bool(shifted.getpixel((170, 120))))

    def test_stretch_uses_full_box(self) -> None:
        from niibot.fit import FitOptions, fit_to_label

        source = Image.new("RGB", (10, 200), "black")
        result = fit_to_label(source, 320, 240, 203, FitOptions(mode="stretch"))
        for point in ((0, 0), (319, 239)):
            self.assertEqual(result.getpixel(point), 0)

    def test_dither_and_invert(self) -> None:
        from niibot.fit import FitOptions, fit_to_label

        gradient = Image.linear_gradient("L").resize((200, 200))
        plain = fit_to_label(gradient, 320, 240, 203, FitOptions(mode="stretch"))
        inverted = fit_to_label(gradient, 320, 240, 203, FitOptions(mode="stretch", invert=True))
        self.assertNotEqual(
            plain.convert("L").tobytes()[:100],
            inverted.convert("L").tobytes()[:100],
        )
        dithered = fit_to_label(gradient, 320, 240, 203, FitOptions(mode="stretch", dither=True))
        self.assertEqual(dithered.mode, "1")


if __name__ == "__main__":
    unittest.main()
