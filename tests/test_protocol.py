"""Protocol level tests that run without a real printer.

The fake link mimics the B1 firmware answers, so the whole connect/print flow
can be exercised and the produced frames verified byte by byte.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from niibot.bitmap import encode_image
from niibot.printer import NiimbotPrinter
from niibot.protocol import Cmd, Frame, FrameReader, Resp, build_frame, build_connect_frame, checksum
from niibot.render import render_label
from niibot.spec import Element, LabelSpec

RESPONSE_FOR = {
    Cmd.SET_DENSITY: Resp.SET_DENSITY,
    Cmd.SET_LABEL_TYPE: Resp.SET_LABEL_TYPE,
    Cmd.PRINT_START: Resp.PRINT_START,
    Cmd.PAGE_START: Resp.PAGE_START,
    Cmd.SET_PAGE_SIZE: Resp.SET_PAGE_SIZE,
    Cmd.PAGE_END: Resp.PAGE_END,
    Cmd.PRINT_END: Resp.PRINT_END,
}


class FakeLink:
    """Minimal stand-in for :class:`niibot.ble.BleLink`."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, bytes]] = []
        self.written: list[bytes] = []
        self.connected = False
        self.address = "AA:BB:CC:DD:EE:FF"
        self.name = "B1-TEST"
        self.write_delay = 0.0
        self.max_write = 240
        self.last_error = None
        self.status_calls = 0
        self.copies = 1
        self.rfid_payload: bytes | None = None
        self.paper_info_payload: bytes | None = None
        self.model_id = 4096
        self.protocol = (0, 3)

    async def connect(self, address: str, timeout: float = 25.0) -> None:
        self.connected = True
        self.address = address

    async def disconnect(self) -> None:
        self.connected = False

    async def send_raw(self, payload: bytes, timeout: float = 1.5):
        self.written.append(payload)
        return Frame(Resp.CONNECT, b"\x01")

    async def write_frames(self, frames: list[bytes]) -> None:
        self.written.extend(frames)

    async def send(self, cmd, data=b"", expect=None, timeout=1.5, wait=True):
        self.sent.append((int(cmd), bytes(data)))
        if int(cmd) == Cmd.PRINTER_STATUS_DATA:
            # protocol version is encoded as data[11] * 100 + data[12]
            return Frame(Resp.PRINTER_STATUS_DATA, bytes(11) + bytes(self.protocol))
        if int(cmd) == Cmd.PRINTER_INFO:
            sub = data[0]
            if sub == 0x08:
                return Frame(0x48, self.model_id.to_bytes(2, "big"))
            if sub == 0x0B:
                return Frame(0x4B, b"SN123456")
            return Frame(0x40 + sub, b"\x01")
        if int(cmd) == Cmd.HEARTBEAT:
            payload = bytes((0x1F, 0x50, 0x50, 0x48, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00))
            return Frame(Resp.HEARTBEAT_ADVANCED2, payload)
        if int(cmd) == Cmd.RFID_INFO:
            if self.rfid_payload is None:
                return Frame(Resp.RFID_INFO, b"\x00")
            return Frame(Resp.RFID_INFO, self.rfid_payload)
        if int(cmd) == Cmd.GET_PAPER_INFO:
            if self.paper_info_payload == b"\x01":
                return Frame(Resp.NOT_SUPPORTED, b"\x01")
            if self.paper_info_payload is None:
                return None
            return Frame(Resp.GET_PAPER_INFO, self.paper_info_payload)
        if int(cmd) == Cmd.PRINT_STATUS:
            if not wait:
                return None
            self.status_calls += 1
            page = self.copies if self.status_calls > 1 else 1
            return Frame(Resp.PRINT_STATUS, page.to_bytes(2, "big") + b"\x64\x64")
        if int(cmd) in (Cmd.PRINT_BITMAP_ROW, Cmd.PRINT_EMPTY_ROW):
            return None
        response = RESPONSE_FOR.get(cmd)
        return Frame(int(response), b"\x01") if response else None

    def traffic(self, limit: int = 200):
        return []


def sample_rfid_payload() -> bytes:
    barcode = b"11262111"
    serial = b"PC0G229321004571"
    return (
        bytes.fromhex("41b8881d15a4e197")
        + bytes((len(barcode),))
        + barcode
        + bytes((len(serial),))
        + serial
        + b"\x01\x14"  # print limit 276
        + b"\x00\x06"  # used 6
        + b"\x01"  # consumable type: with gaps
        + b"\x00\xe6"  # capacity 230
    )


def expected_row(image, row: int, cols: int) -> bytes:
    """Pack one row MSB-first (1 = black), independent of PIL's bit packing."""
    packed = bytearray(cols // 8)
    for x in range(cols):
        if x < image.width and image.getpixel((x, row)) == 0:
            packed[x // 8] |= 0x80 >> (x % 8)
    return bytes(packed)


def decode_rows(packets: list[bytes], stride: int) -> dict[int, bytes]:
    rows: dict[int, bytes] = {}
    for packet in packets:
        frame = FrameReader().feed(packet)[0]
        row = int.from_bytes(frame.data[0:2], "big")
        if frame.cmd == Cmd.PRINT_EMPTY_ROW:
            repeat = frame.data[2]
            for offset in range(repeat):
                rows[row + offset] = bytes(stride)
            continue
        assert frame.cmd == Cmd.PRINT_BITMAP_ROW, hex(frame.cmd)
        total = int.from_bytes(frame.data[3:5], "little")
        repeat = frame.data[5]
        data = frame.data[6:]
        assert len(data) == stride
        assert total == sum(bin(byte).count("1") for byte in data)
        for offset in range(repeat):
            rows[row + offset] = data
    return rows


class ProtocolTests(unittest.TestCase):
    def test_frame_roundtrip(self) -> None:
        raw = build_frame(Cmd.SET_DENSITY, b"\x03")
        expected = bytes.fromhex("5555") + bytes((0x21, 1, 3)) + bytes((checksum(0x21, b"\x03"),)) + bytes.fromhex("aaaa")
        self.assertEqual(raw, expected)

        reader = FrameReader()
        frames = reader.feed(b"\x00\x01garbage" + raw)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, Cmd.SET_DENSITY)
        self.assertEqual(frames[0].data, b"\x03")

    def test_frame_reader_handles_split_and_bad_crc(self) -> None:
        raw = build_frame(Cmd.HEARTBEAT, b"\x04")
        reader = FrameReader()
        self.assertEqual(reader.feed(raw[:4]), [])
        self.assertEqual(len(reader.feed(raw[4:])), 1)

        broken = bytearray(raw)
        broken[-3] ^= 0xFF
        reader = FrameReader()
        frames = reader.feed(bytes(broken) + raw)
        self.assertEqual([frame.cmd for frame in frames], [Cmd.HEARTBEAT])

    def test_connect_frame(self) -> None:
        self.assertEqual(build_connect_frame().hex(), "035555c10101c1aaaa")

    def test_bitmap_rows_roundtrip(self) -> None:
        spec = LabelSpec(
            width_mm=12.5,
            height_mm=10,
            elements=[Element(type="text", x=1, y=1, w=10.5, h=8, text="OK", font_size_pt=8, align="center")],
        )
        rendered = render_label(spec, 203, 384)
        encoded = encode_image(rendered.image)

        self.assertEqual(encoded.rows, rendered.height_px)
        self.assertEqual(encoded.cols % 8, 0)
        stride = encoded.cols // 8
        rows = decode_rows(encoded.packets, stride)

        self.assertEqual(sorted(rows), list(range(encoded.rows)))
        for index in range(encoded.rows):
            self.assertEqual(rows[index], expected_row(rendered.image, index, encoded.cols))

    def test_print_flow_b1(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        spec = LabelSpec(
            width_mm=48,
            height_mm=20,
            elements=[Element(type="text", x=2, y=2, w=44, h=16, text="TEST", font_size_pt=12)],
        )
        rendered = render_label(spec, 203, 384)
        encoded = encode_image(rendered.image)

        async def scenario() -> None:
            await printer.connect(link.address)
            link.copies = 2
            link.status_calls = 0
            link.sent.clear()
            await printer.print_bitmap(encoded, copies=2, density=3, label_type=1)

        asyncio.run(scenario())

        self.assertEqual(printer.info.model_id, 4096)
        self.assertEqual(printer.info.model_name, "B1")
        self.assertEqual(printer.info.print_task, "b1")
        self.assertEqual(printer.info.protocol_version, 3)
        self.assertEqual(printer.info.firmware, "0.01")

        self.assertEqual(
            [cmd for cmd, _ in link.sent],
            [Cmd.SET_DENSITY, Cmd.PRINT_START, Cmd.PAGE_START, Cmd.SET_PAGE_SIZE, Cmd.PAGE_END, Cmd.PRINT_STATUS, Cmd.PRINT_STATUS, Cmd.PRINT_END],
        )
        start_data = [data for cmd, data in link.sent if cmd == Cmd.PRINT_START][0]
        self.assertEqual(start_data, bytes((0, 2, 0, 0, 0, 0, 0)))
        page_size = [data for cmd, data in link.sent if cmd == Cmd.SET_PAGE_SIZE][0]
        self.assertEqual(int.from_bytes(page_size[0:2], "big"), encoded.rows)
        self.assertEqual(int.from_bytes(page_size[2:4], "big"), encoded.cols)
        self.assertEqual(int.from_bytes(page_size[4:6], "big"), 2)
        # one raw write for the connect packet, then one write per row packet
        self.assertEqual(len(link.written), encoded.packet_count + 1)

    def test_print_flow_v4(self) -> None:
        link = FakeLink()
        link.model_id = 4097  # B1 Pro
        link.protocol = (3, 5)  # protocol 305 -> v4 task
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        spec = LabelSpec(
            width_mm=48,
            height_mm=20,
            elements=[Element(type="text", x=2, y=2, w=44, h=16, text="PRO", font_size_pt=12)],
        )
        rendered = render_label(spec, 300, 567)
        encoded = encode_image(rendered.image)

        async def scenario() -> None:
            await printer.connect(link.address)
            link.copies = 1
            link.status_calls = 0
            link.sent.clear()
            await printer.print_bitmap(encoded, copies=1, density=3, label_type=1)

        asyncio.run(scenario())

        self.assertEqual(printer.info.model_name, "B1 Pro")
        self.assertEqual(printer.info.print_task, "v4")
        self.assertEqual(
            [cmd for cmd, _ in link.sent],
            [Cmd.SET_DENSITY, Cmd.PRINT_START, Cmd.PRINT_STATUS, Cmd.SET_PAGE_SIZE, Cmd.PAGE_END, Cmd.PRINT_STATUS, Cmd.PRINT_END],
        )
        start_data = [data for cmd, data in link.sent if cmd == Cmd.PRINT_START][0]
        self.assertEqual(len(start_data), 9)
        page_size = [data for cmd, data in link.sent if cmd == Cmd.SET_PAGE_SIZE][0]
        self.assertEqual(len(page_size), 13)
        self.assertEqual(int.from_bytes(page_size[0:2], "big"), encoded.rows)
        self.assertEqual(int.from_bytes(page_size[2:4], "big"), encoded.cols)
        self.assertEqual(int.from_bytes(page_size[4:6], "big"), 1)

    def test_paper_detection(self) -> None:
        link = FakeLink()
        link.rfid_payload = sample_rfid_payload()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario():
            await printer.connect(link.address)
            return await printer.refresh()

        paper = asyncio.run(scenario())
        self.assertTrue(paper.lid_closed)
        self.assertTrue(paper.paper_inserted)
        self.assertTrue(paper.rfid_ok)
        self.assertEqual(paper.barcode, "11262111")
        self.assertEqual(paper.serial, "PC0G229321004571")
        self.assertEqual(paper.capacity, 230)
        self.assertEqual(paper.used, 6)
        self.assertEqual(paper.remaining, 224)
        self.assertEqual(paper.label_type, 1)
        self.assertEqual(paper.label_type_name, "etykiety z odstępem")
        self.assertEqual(paper.battery, 0x50)

    def test_no_rfid_tag_is_not_an_error(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario():
            await printer.connect(link.address)
            return await printer.refresh()

        paper = asyncio.run(scenario())
        self.assertIsNone(paper.barcode)
        self.assertEqual(paper.label_type, 1)  # falls back to the printer setting
        self.assertEqual(paper.errors, [])

    def test_paper_info_geometry(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        # gap 20 px / 2.0 mm, total 240 px / 30.0 mm, type 1, width 400 px / 50.0 mm
        payload = (
            (20).to_bytes(2, "big")
            + (240).to_bytes(2, "big")
            + bytes((1,))
            + (20).to_bytes(2, "big")
            + (300).to_bytes(2, "big")
            + (400).to_bytes(2, "big")
            + (500).to_bytes(2, "big")
            + bytes((0,))
            + (0).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
        )
        link.paper_info_payload = payload

        async def scenario():
            await printer.connect(link.address)
            return await printer.read_paper_info()

        geometry = asyncio.run(scenario())
        self.assertTrue(geometry.valid)
        self.assertEqual(geometry.width_mm, 50.0)
        self.assertEqual(geometry.height_mm, 30.0)
        self.assertEqual(geometry.gap_mm, 2.0)
        self.assertEqual(geometry.label_type, 1)
        self.assertEqual(geometry.width_px, 400)
        self.assertEqual(geometry.height_px, 240)

    def test_paper_info_unknown_length_is_not_trusted(self) -> None:
        link = FakeLink()
        link.paper_info_payload = b"\x00\x00\x00\x00"
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario():
            await printer.connect(link.address)
            return await printer.read_paper_info()

        geometry = asyncio.run(scenario())
        self.assertFalse(geometry.valid)
        self.assertIsNone(geometry.width_mm)
        self.assertEqual(geometry.raw, "00000000")

    def test_not_supported_answer_is_reported(self) -> None:
        link = FakeLink()
        link.paper_info_payload = b"\x01"  # B1 answers 0x00 with payload 0x01
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario():
            await printer.connect(link.address)
            return await printer.refresh()

        paper = asyncio.run(scenario())
        self.assertFalse(paper.geometry_supported)
        self.assertIsNone(paper.detected_width_mm)
        # the rest of the status must still work
        self.assertTrue(paper.lid_closed)
        self.assertTrue(paper.paper_inserted)

    def test_vendor_catalog_import(self) -> None:
        import json
        import tempfile

        from niibot.consumables import parse_sku_name, read_records, template_to_consumable

        self.assertEqual(
            parse_sku_name("T40*15/2R-460WHITE"),
            {"width_mm": 40.0, "height_mm": 15.0, "rows": 2, "capacity": 460, "color": "WHITE"},
        )
        self.assertEqual(parse_sku_name("T50*30")["width_mm"], 50.0)
        self.assertEqual(parse_sku_name("bez sensu"), {})

        template = {
            "id": 80029083,
            "name": "T40*15/2R-460WHITE",
            "names": [{"languageCode": "en", "name": "T40*15/2R-460WHITE"}],
            "paperType": 1,
            "width": 40,
            "height": 30,
        }
        value = json.dumps(template, ensure_ascii=False)
        # Chromium stores this record as UTF-16LE because it holds CJK names.
        blob = (
            b"ConsumablesTemplate-6971501227736\xab\x62\x00"
            + value.encode("utf-16-le")
            + b"\x00\x00"
            + b"ConsumablesInfo-881d9bb7ef960000\x01"
            + b'{"actualNum":460,"serialNumber":"881d9bb7ef960000"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "000003.log").write_bytes(blob)
            templates, infos, scanned = read_records(Path(tmp))

        self.assertEqual(scanned, ["000003.log"])
        self.assertIn("6971501227736", templates)
        self.assertEqual(templates["6971501227736"]["height"], 30)

        consumable = template_to_consumable("6971501227736", templates["6971501227736"], {"actualNum": 460})
        self.assertTrue(consumable.is_usable)
        self.assertEqual((consumable.width_mm, consumable.height_mm), (40.0, 30.0))
        self.assertEqual(consumable.rows, 2)
        self.assertEqual(consumable.capacity, 460)
        self.assertEqual(consumable.paper_type, 1)

    def test_handshake_order(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario():
            await printer.connect(link.address)

        asyncio.run(scenario())
        info_subs = [data[0] for cmd, data in link.sent if cmd == Cmd.PRINTER_INFO]
        self.assertEqual(info_subs[:9], [0x08, 0x08, 0x0B, 0x0D, 0x0A, 0x07, 0x03, 0x0C, 0x09])
        self.assertIn((Cmd.HEARTBEAT, b"\x04"), link.sent)


if __name__ == "__main__":
    unittest.main()
