"""Convert 1-bit dot planes into Niimbot row packets."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .protocol import BitmapColorMode, Cmd, PageColor, build_frame

MAX_RUN = 255

#: Ink colours an item may ask for. Red needs red/black two-colour stock.
INK_BLACK = "black"
INK_RED = "red"


@dataclass
class EncodedBitmap:
    rows: int
    cols: int
    packets: list[bytes]
    black_pixels: int
    #: The 1-bit rows themselves (8 px per byte, MSB first, 1 = dot). The LuckPrinter family
    #: (Fichero) sends these verbatim inside a ``GS v 0`` raster transfer.
    raw: bytes = b""
    stride: int = 0
    #: ``PageColor`` value: single ink, or the two-colour page mode that develops red.
    page_color: int = int(PageColor.SINGLE)

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def payload_bytes(self) -> int:
        return sum(len(packet) for packet in self.packets)


def _row_bytes(image: Image.Image) -> tuple[int, int, bytes]:
    """Return ``(cols, stride, raw)`` with rows padded to whole bytes.

    PIL packs white as a ``1`` bit; the printer expects ``1`` = dot, so the
    bytes are inverted here (padding becomes ``0`` = no dot, as it should).
    """
    if image.mode != "1":
        image = image.convert("L").point(lambda value: 0 if value < 128 else 255).convert("1", dither=Image.NONE)
    width, height = image.size
    cols = (width + 7) // 8 * 8
    if cols != width:
        padded = Image.new("1", (cols, height), 1)
        padded.paste(image, (0, 0))
        image = padded
    stride = cols // 8
    return cols, stride, bytes(byte ^ 0xFF for byte in image.tobytes())


def _rows(raw: bytes, stride: int, height: int, cols: int) -> list[bytes]:
    """One padded byte string per row, so both inks can be compared row by row."""
    width = cols // 8
    if not stride:
        return [bytes(width)] * height
    out = []
    for index in range(height):
        row = raw[index * stride : (index + 1) * stride]
        out.append(row if width == stride else row.ljust(width, b"\x00"))
    return out


def encode_image(image: Image.Image, ink: str = INK_BLACK) -> EncodedBitmap:
    """Turn a one-ink bitmap into row packets."""
    if str(ink or INK_BLACK).lower() == INK_RED:
        return encode_planes(red=image)
    return encode_planes(black=image)


def encode_planes(black: Image.Image | None = None, red: Image.Image | None = None) -> EncodedBitmap:
    """Turn the black and red dot planes of one label into row packets.

    A page holding any red dot switches to the two-colour wrapper (``0x8A``), which names the
    ink of every row; the firmware then fires the head at the temperature that develops red.
    A row with both inks goes out in the *mixed* form - a presence mask followed by one colour
    bit per dot - so a red heading above a black barcode costs one row, not two. A page with no
    red keeps the plain ``0x85``/``0x84`` rows byte for byte.
    """
    reference = black if black is not None else red
    if reference is None:
        raise ValueError("encode_planes needs at least one ink")

    black_cols, black_stride, black_raw = _row_bytes(black) if black is not None else (0, 0, b"")
    red_cols, red_stride, red_raw = _row_bytes(red) if red is not None else (0, 0, b"")
    cols = max(black_cols, red_cols)
    stride = cols // 8
    height = reference.height

    black_rows = _rows(black_raw, black_stride, height, cols)
    red_rows = _rows(red_raw, red_stride, height, cols)
    has_red = any(any(row) for row in red_rows)

    packets: list[bytes] = []
    black_pixels = 0
    row = 0
    while row < height:
        black_row = black_rows[row]
        red_row = red_rows[row]
        run = 1
        while row + run < height and run < MAX_RUN:
            if black_rows[row + run] != black_row or red_rows[row + run] != red_row:
                break
            run += 1

        position = bytes((row >> 8, row & 0xFF))
        mask = bytes(left | right for left, right in zip(black_row, red_row))
        if not any(mask):
            if has_red:
                packets.append(build_frame(
                    Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR,
                    bytes((BitmapColorMode.EMPTY, Cmd.PRINT_EMPTY_ROW)) + position + bytes((run,)),
                ))
            else:
                packets.append(build_frame(Cmd.PRINT_EMPTY_ROW, position + bytes((run,))))
        elif not has_red:
            count = sum(bin(byte).count("1") for byte in black_row)
            black_pixels += count * run
            header = position + bytes((0x00, count & 0xFF, (count >> 8) & 0xFF, run))
            packets.append(build_frame(Cmd.PRINT_BITMAP_ROW, header + black_row))
        elif not any(red_row):
            black_pixels += sum(bin(byte).count("1") for byte in black_row) * run
            packets.append(build_frame(
                Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR,
                bytes((BitmapColorMode.BLACK, Cmd.PRINT_BITMAP_ROW)) + position + bytes((run,)) + black_row,
            ))
        elif not any(black_row):
            packets.append(build_frame(
                Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR,
                bytes((BitmapColorMode.RED, Cmd.PRINT_BITMAP_ROW)) + position + bytes((run,)) + red_row,
            ))
        else:
            black_pixels += sum(bin(byte).count("1") for byte in black_row) * run
            # One colour byte per non-blank mask byte: 1 = red, 0 = black.
            colours = bytes(red_byte for mask_byte, red_byte in zip(mask, red_row) if mask_byte)
            packets.append(build_frame(
                Cmd.PRINT_BITMAP_ROW_DOUBLE_COLOR,
                bytes((BitmapColorMode.MIXED,)) + position + bytes((run,)) + mask + colours,
            ))
        row += run

    union = b"".join(
        bytes(left | right for left, right in zip(black_row, red_row))
        for black_row, red_row in zip(black_rows, red_rows)
    )
    return EncodedBitmap(
        rows=height,
        cols=cols,
        packets=packets,
        black_pixels=black_pixels,
        raw=union,
        stride=stride,
        page_color=int(PageColor.DOUBLE if has_red else PageColor.SINGLE),
    )
