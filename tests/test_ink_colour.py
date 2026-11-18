"""Printing in red: the two-colour row wrapper, the page colour and the coloured preview."""

from __future__ import annotations

import asyncio
import unittest

from PIL import Image

from niibot.bitmap import encode_image, encode_planes
from niibot.printer import NiimbotPrinter
from niibot.protocol import BitmapColorMode, Cmd, FrameReader, PageColor
from niibot.render import colourise, render_label
from niibot.spec import Element, LabelSpec

from test_protocol import FakeLink


def _spec(*inks: str) -> LabelSpec:
    elements = [
        Element(type="text", x=2, y=2 + index * 7, w=36, h=6, text=f"ITEM {index}", font_size_pt=10, ink=ink)
        for index, ink in enumerate(inks or ("black",))
    ]
    return LabelSpec(width_mm=40, height_mm=20, elements=elements)


def _render(*inks: str, dpi: int = 203, printhead: int = 384):
    return render_label(_spec(*inks), dpi, printhead)


def _frames(packets: list[bytes]):
    return [FrameReader().feed(packet)[0] for packet in packets]


def _planes(width: int, height: int, black_rows: list[bytes], red_rows: list[bytes]):
    """Build 1-bit ink planes from the dot rows the encoder should see (``1`` = a dot)."""
    black = Image.new("1", (width, height), 1)
    red = Image.new("1", (width, height), 1)
    for row, (black_row, red_row) in enumerate(zip(black_rows, red_rows)):
        for x in range(width):
            if black_row[x // 8] & (0x80 >> (x % 8)):
                black.putpixel((x, row), 0)
            if red_row[x // 8] & (0x80 >> (x % 8)):
                red.putpixel((x, row), 0)
    return black, red


def _expected_row(plane: Image.Image, row: int, cols: int) -> bytes:
    packed = bytearray(cols // 8)
    for x in range(cols):
        if x < plane.width and plane.getpixel((x, row)) == 0:
            packed[x // 8] |= 0x80 >> (x % 8)
    return bytes(packed)


def _dots(plane: Image.Image) -> int:
    return 8 * len(plane.tobytes()) - sum(bin(byte).count("1") for byte in plane.tobytes())


class MonoEncodingTests(unittest.TestCase):
    def test_a_label_without_red_matches_the_single_ink_encoder(self) -> None:
        rendered = _render()
        legacy = encode_image(rendered.image)
        planes = encode_planes(rendered.black, rendered.red)

        self.assertEqual(planes.packets, legacy.packets)
        self.assertEqual(planes.raw, legacy.raw)
        self.assertEqual(planes.black_pixels, legacy.black_pixels)
        self.assertEqual(planes.page_color, int(PageColor.SINGLE))

    def test_black_only_rows_keep_the_plain_commands(self) -> None:
        encoded = encode_planes(_render().black)
        self.assertEqual(
            {frame.cmd for frame in _frames(encoded.packets)},
            {Cmd.PRINT_BITMAP_ROW, Cmd.PRINT_EMPTY_ROW},
        )


class TwoColourEncodingTests(unittest.TestCase):
    def test_a_red_label_wraps_every_row(self) -> None:
        rendered = _render("red")
        encoded = encode_planes(rendered.black, rendered.red)
        frames = _frames(encoded.packets)

        self.assertEqual(encoded.page_color, int(PageColor.DOUBLE))
        self.assertEqual({frame.cmd for frame in frames}, {Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR})
        self.assertNotIn(BitmapColorMode.MIXED, {frame.data[0] for frame in frames})
        self.assertTrue(any(frame.data[1] == Cmd.PRINT_BITMAP_ROW for frame in frames))

    def test_red_rows_carry_their_own_row_command_and_dots(self) -> None:
        rendered = _render("red")
        encoded = encode_planes(rendered.black, rendered.red)
        stride = encoded.cols // 8

        seen = 0
        for frame in _frames(encoded.packets):
            if frame.data[1] != Cmd.PRINT_BITMAP_ROW:
                continue
            seen += 1
            row = int.from_bytes(frame.data[2:4], "big")
            self.assertEqual(frame.data[0], BitmapColorMode.RED)
            self.assertEqual(frame.data[4], 1)
            self.assertEqual(len(frame.data), 5 + stride)
            self.assertEqual(frame.data[5:], _expected_row(rendered.red, row, encoded.cols))
        self.assertGreater(seen, 0)

    def test_an_empty_row_on_a_colour_page_uses_the_colour_wrapper(self) -> None:
        encoded = encode_planes(*_planes(16, 3, [b"\x00\x00"] * 3, [b"\x00\x00", b"\x80\x00", b"\x00\x00"]))
        frames = _frames(encoded.packets)

        self.assertEqual(encoded.page_color, int(PageColor.DOUBLE))
        self.assertEqual([frame.cmd for frame in frames], [Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR] * 3)
        self.assertEqual(frames[0].data, bytes((BitmapColorMode.EMPTY, Cmd.PRINT_EMPTY_ROW, 0, 0, 1)))
        self.assertEqual(frames[2].data, bytes((BitmapColorMode.EMPTY, Cmd.PRINT_EMPTY_ROW, 0, 2, 1)))

    def test_a_row_holding_both_inks_sends_a_mask_and_one_colour_bit_per_dot(self) -> None:
        black, red = _planes(16, 1, [b"\xff\x00"], [b"\x00\xff"])
        frame = _frames(encode_planes(black, red).packets)[0]

        self.assertEqual(frame.cmd, Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR)
        self.assertEqual(frame.data[0], BitmapColorMode.MIXED)
        # MIXED has no 0x85 sub-command: mode, position, repeats, mask, then the colours.
        self.assertEqual(frame.data[1:4], bytes((0, 0, 1)))
        self.assertEqual(frame.data[4:6], b"\xff\xff")
        self.assertEqual(frame.data[6:], b"\x00\xff")

    def test_two_colour_pages_keep_the_union_for_one_ink_printers(self) -> None:
        black, red = _planes(
            16,
            4,
            [b"\x80\x00", b"\xc0\x00", b"\x00\x00", b"\xf0\x00"],
            [b"\x08\x00", b"\x00\x00", b"\x00\x00", b"\x0f\x00"],
        )
        encoded = encode_planes(black, red)

        self.assertEqual(encoded.page_color, int(PageColor.DOUBLE))
        self.assertEqual(encoded.raw, b"\x88\x00\xc0\x00\x00\x00\xff\x00")


class PageColourTests(unittest.TestCase):
    def _start(self, link: FakeLink, rendered) -> bytes:
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]

        async def scenario() -> None:
            await printer.connect(link.address)
            link.copies = 1
            link.status_calls = 0
            link.sent.clear()
            await printer.print_bitmap(encode_planes(rendered.black, rendered.red), copies=1)

        asyncio.run(scenario())
        return [data for cmd, data in link.sent if cmd == Cmd.PRINT_START][0]

    def test_page_colour_is_the_last_byte_of_the_b1_print_start(self) -> None:
        start = self._start(FakeLink(), _render("red"))
        self.assertEqual(len(start), 7)
        self.assertEqual(start[-1], PageColor.DOUBLE)

    def test_page_colour_is_the_seventh_byte_of_the_v4_print_start(self) -> None:
        link = FakeLink()
        link.model_id = 4097  # B1 Pro
        link.protocol = (3, 5)  # protocol 305 -> v4 task
        start = self._start(link, _render("red", dpi=300, printhead=567))
        self.assertEqual(len(start), 9)
        self.assertEqual(start[6], PageColor.DOUBLE)

    def test_a_black_label_stays_a_single_colour_page(self) -> None:
        self.assertEqual(self._start(FakeLink(), _render())[-1], PageColor.SINGLE)


class InkSpecTests(unittest.TestCase):
    def test_unknown_ink_falls_back_to_black(self) -> None:
        self.assertEqual(Element(ink="RED").ink, "red")
        self.assertEqual(Element(ink=" Red ").ink, "red")
        self.assertEqual(Element(ink="blue").ink, "black")
        self.assertEqual(Element(ink=None).ink, "black")
        self.assertEqual(Element().ink, "black")

    def test_ink_belongs_to_the_item_not_the_label(self) -> None:
        self.assertFalse(LabelSpec().uses_red)
        self.assertFalse(_spec("black", "black").uses_red)
        self.assertTrue(_spec("black", "red").uses_red)

    def test_hidden_red_items_do_not_make_the_page_two_colour(self) -> None:
        spec = _spec("red")
        spec.elements[0].visible = False
        self.assertFalse(spec.uses_red)


class InkRenderTests(unittest.TestCase):
    def test_switching_an_item_to_red_keeps_every_dot(self) -> None:
        black_label = _render("black")
        red_label = _render("red")

        self.assertEqual(_dots(black_label.black), _dots(red_label.red))
        self.assertEqual(_dots(black_label.red), 0)
        self.assertEqual(_dots(red_label.black), 0)
        self.assertFalse(black_label.uses_red)
        self.assertTrue(red_label.uses_red)

    def test_a_label_may_mix_the_two_inks(self) -> None:
        rendered = _render("black", "red")

        self.assertTrue(rendered.uses_red)
        self.assertGreater(_dots(rendered.black), 0)
        self.assertGreater(_dots(rendered.red), 0)

    def test_colourise_draws_both_planes_in_paper_colours(self) -> None:
        image = colourise(*_planes(8, 1, [b"\x80"], [b"\x01"]))

        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((7, 0)), (198, 32, 38))
        self.assertEqual(image.getpixel((3, 0)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
