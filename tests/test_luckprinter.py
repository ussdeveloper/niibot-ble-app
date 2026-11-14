"""Byte-level tests for the LuckPrinter (Fichero / AiYin) protocol."""

from __future__ import annotations

import unittest

from PIL import Image

from niibot import luckprinter as lp
from niibot.bitmap import encode_image
from niibot.fichero import FicheroPrinter


class FakeLink:
    """Records what would go on the wire and replays plausible replies."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.connected = False
        self.address = None
        self.name = "FICHERO_5836"
        self.last_error = None
        self.replies = {
            lp.REQUESTS["model"]: b"D11s",
            lp.REQUESTS["firmware"]: b"2.4.6",
            lp.REQUESTS["serial"]: b"SN42",
            lp.REQUESTS["status"]: b"\x00",
            lp.REQUESTS["battery"]: bytes.fromhex("0056"),
        }

    async def connect(self, address: str) -> None:
        self.connected = True
        self.address = address

    async def disconnect(self) -> None:
        self.connected = False

    async def write(self, payload: bytes) -> None:
        self.writes.append(bytes(payload))

    async def request(self, payload: bytes, timeout: float = 2.0) -> bytes:
        self.writes.append(bytes(payload))
        return self.replies.get(bytes(payload), b"OK")

    async def wait_for_finish(self, timeout: float = 60.0) -> bool:
        return True

    fail = False

    @property
    def alive(self) -> bool:
        return self.connected

    async def request_keep_alive(self, payload: bytes, timeout: float = 2.0) -> bytes:
        if self.fail:
            raise RuntimeError("link lost")
        return await self.request(payload, timeout)


class CommandTests(unittest.TestCase):
    def test_info_queries_match_the_captured_bytes(self) -> None:
        self.assertEqual(lp.REQUESTS["model"], bytes.fromhex("10ff20f0"))
        self.assertEqual(lp.REQUESTS["firmware"], bytes.fromhex("10ff20f1"))
        self.assertEqual(lp.REQUESTS["serial"], bytes.fromhex("10ff20f2"))
        self.assertEqual(lp.REQUESTS["battery"], bytes.fromhex("10ff50f1"))
        self.assertEqual(lp.REQUESTS["status"], bytes.fromhex("10ff40"))
        self.assertEqual(lp.REQUESTS["info"], bytes.fromhex("10ff70"))

    def test_settings_commands(self) -> None:
        self.assertEqual(lp.set_density(2), bytes.fromhex("10ff100002"))
        self.assertEqual(lp.set_density(9), bytes.fromhex("10ff100002"))  # clamped to 2
        self.assertEqual(lp.set_paper_type(1), bytes.fromhex("10ff8401"))
        self.assertEqual(lp.set_shutdown(20), bytes.fromhex("10ff120014"))

    def test_enable_and_stop_are_family_specific(self) -> None:
        self.assertEqual(lp.enable_command("aiyin"), bytes.fromhex("10fffe01"))
        self.assertEqual(lp.stop_command("aiyin"), bytes.fromhex("10fffe45"))
        self.assertEqual(lp.enable_command("lujiang"), bytes.fromhex("10fff103"))
        self.assertEqual(lp.stop_command("lujiang"), bytes.fromhex("10fff145"))
        self.assertEqual(lp.enable_command("nonsense"), lp.enable_command("aiyin"))


class RasterTests(unittest.TestCase):
    def test_header_layout(self) -> None:
        frame = lp.build_raster(b"\xff" * 36, width_px=96, height=3)
        self.assertEqual(frame[:3], b"\x1d\x76\x30")
        self.assertEqual(frame[3], 0)                 # mode
        self.assertEqual(int.from_bytes(frame[4:6], "little"), 12)   # 96 px = 12 bytes
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 3)    # 3 rows
        self.assertEqual(len(frame), 8 + 36)

    def test_short_payload_is_padded(self) -> None:
        frame = lp.build_raster(b"\x00", width_px=96, height=2)
        self.assertEqual(len(frame), 8 + 24)          # 12 bytes * 2 rows

    def test_encoded_bitmap_feeds_the_raster(self) -> None:
        image = Image.new("1", (96, 4), 1)            # all white
        image.putpixel((0, 0), 0)                     # one black dot, top-left
        encoded = encode_image(image)
        self.assertEqual((encoded.cols, encoded.rows), (96, 4))
        self.assertEqual(encoded.stride, 12)
        self.assertEqual(len(encoded.raw), 48)
        # 1 = black in the printer's convention, MSB first.
        self.assertEqual(encoded.raw[0], 0x80)
        self.assertEqual(encoded.raw[12], 0x00)
        frame = lp.build_raster(encoded.raw, encoded.cols, encoded.rows)
        self.assertEqual(frame[8], 0x80)


class ParsingTests(unittest.TestCase):
    def test_status_bits(self) -> None:
        self.assertTrue(lp.parse_status(b"\x00")["ready"])
        flags = lp.parse_status(b"\x06")
        self.assertTrue(flags["cover_open"])
        self.assertTrue(flags["out_of_paper"])
        self.assertFalse(flags["low_battery"])

    def test_battery(self) -> None:
        self.assertEqual(lp.parse_battery(bytes.fromhex("0056"))["percent"], 86)
        self.assertIsNone(lp.parse_battery(b"")["percent"])

    def test_all_info_is_pipe_delimited(self) -> None:
        info = lp.parse_info("FICHERO_5836|AA:BB:CC:DD:EE:FF|11:22:33:44:55:66|2.4.6|SN123|86")
        self.assertEqual(info["name"], "FICHERO_5836")
        self.assertEqual(info["firmware"], "2.4.6")
        self.assertEqual(info["serial"], "SN123")
        self.assertEqual(info["battery"], 86)

    def test_text_reply(self) -> None:
        self.assertEqual(lp.decode_text(b"D11s\x00\r\n"), "D11s")


class PrinterFlowTests(unittest.TestCase):
    """The print sequence has to match the captured vendor sequence exactly."""

    def setUp(self) -> None:
        self.printer = FicheroPrinter()
        self.link = FakeLink()
        self.printer.link = self.link  # type: ignore[assignment]

    def test_identify_and_refresh(self) -> None:
        import asyncio

        asyncio.run(self.printer.connect("C9:48:8A:00:00:01"))
        self.assertIn("D11s", self.printer.info.model_name)
        self.assertEqual(self.printer.info.firmware, "2.4.6")
        self.assertEqual(self.printer.info.serial, "SN42")
        self.assertEqual(self.printer.info.printhead_px, 96)
        self.assertEqual(self.printer.info.dpi, 203)
        self.assertEqual(self.printer.paper.battery, 86)
        self.assertTrue(self.printer.paper.paper_inserted)
        self.assertTrue(self.printer.paper.lid_closed)
        self.assertTrue(self.printer.connected)

    def test_print_sequence(self) -> None:
        import asyncio

        image = Image.new("1", (96, 10), 1)
        encoded = encode_image(image)

        async def run() -> None:
            await self.printer.connect("C9:48:8A:00:00:01")
            self.link.writes.clear()
            await self.printer.print_bitmap(encoded, copies=1, density=3, label_type=0)

        asyncio.run(run())
        payloads = self.link.writes
        self.assertIn(lp.set_density(1), payloads)      # panel 3 of 5 -> middle step
        self.assertIn(lp.set_paper_type(0), payloads)
        self.assertIn(lp.WAKE, payloads)
        self.assertIn(lp.enable_command("aiyin"), payloads)
        self.assertIn(lp.FORM_FEED, payloads)
        self.assertEqual(payloads[-1], lp.stop_command("aiyin"))
        raster = next(payload for payload in payloads if payload.startswith(b"\x1d\x76\x30"))
        self.assertEqual(int.from_bytes(raster[4:6], "little"), 12)
        self.assertEqual(int.from_bytes(raster[6:8], "little"), 10)

    def test_two_copies_repeat_the_sequence(self) -> None:
        import asyncio

        encoded = encode_image(Image.new("1", (96, 5), 1))

        async def run() -> None:
            await self.printer.connect("C9:48:8A:00:00:01")
            self.link.writes.clear()
            await self.printer.print_bitmap(encoded, copies=2, density=None, label_type=None)

        asyncio.run(run())
        self.assertEqual(self.link.writes.count(lp.enable_command("aiyin")), 2)
        self.assertEqual(self.link.writes.count(lp.stop_command("aiyin")), 2)

    def test_keep_alive_polls_status_and_updates_paper(self) -> None:
        import asyncio

        self.link.replies[lp.REQUESTS["status"]] = b"\x06"   # cover open + out of paper

        async def run() -> bool:
            await self.printer.connect("C9:48:8A:00:00:01")
            self.link.writes.clear()
            alive = await self.printer.keep_alive()
            return alive

        self.assertTrue(asyncio.run(run()))
        self.assertIn(lp.REQUESTS["status"], self.link.writes)
        self.assertFalse(self.printer.paper.lid_closed)
        self.assertFalse(self.printer.paper.paper_inserted)

    def test_keep_alive_reports_a_dead_link(self) -> None:
        import asyncio

        async def run() -> bool:
            await self.printer.connect("C9:48:8A:00:00:01")
            self.link.writes.clear()

            async def boom(payload, timeout=2.0):
                raise RuntimeError("link lost")

            self.link.request = boom  # type: ignore[assignment]
            return await self.printer.keep_alive()

        self.assertFalse(asyncio.run(run()))

    def test_geometry_override_wins(self) -> None:
        self.printer.set_geometry_override(203, 142)
        self.assertEqual(self.printer.info.printhead_px, 142)


if __name__ == "__main__":
    unittest.main()
