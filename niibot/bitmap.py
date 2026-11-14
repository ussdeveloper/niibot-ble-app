"""Convert a 1-bit PIL image into Niimbot row packets."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .protocol import Cmd, build_frame

MAX_RUN = 255


@dataclass
class EncodedBitmap:
    rows: int
    cols: int
    packets: list[bytes]
    black_pixels: int
    #: The 1-bit rows themselves (8 px per byte, MSB first, 1 = black). The LuckPrinter
    #: family (Fichero) sends these verbatim inside a ``GS v 0`` raster transfer.
    raw: bytes = b""
    stride: int = 0

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def payload_bytes(self) -> int:
        return sum(len(packet) for packet in self.packets)


def _row_bytes(image: Image.Image) -> tuple[int, int, bytes]:
    """Return ``(cols, stride, raw)`` with rows padded to whole bytes.

    PIL packs white as a ``1`` bit; the printer expects ``1`` = black, so the
    bytes are inverted here (padding becomes ``0`` = no dot, as it should).
    """
    width, height = image.size
    cols = (width + 7) // 8 * 8
    if cols != width:
        padded = Image.new("1", (cols, height), 1)
        padded.paste(image, (0, 0))
        image = padded
    stride = cols // 8
    return cols, stride, bytes(byte ^ 0xFF for byte in image.tobytes())


def encode_image(image: Image.Image) -> EncodedBitmap:
    if image.mode != "1":
        image = image.convert("L").point(lambda value: 0 if value < 128 else 255).convert("1", dither=Image.NONE)

    cols, stride, raw = _row_bytes(image)
    height = image.height
    packets: list[bytes] = []
    black_pixels = 0

    row = 0
    while row < height:
        data = raw[row * stride : (row + 1) * stride]
        black = sum(bin(byte).count("1") for byte in data)
        is_empty = black == 0

        run = 1
        while row + run < height and run < MAX_RUN:
            nxt = raw[(row + run) * stride : (row + run + 1) * stride]
            if nxt != data:
                break
            run += 1

        if is_empty:
            packets.append(build_frame(Cmd.PRINT_EMPTY_ROW, bytes((row >> 8, row & 0xFF, run))))
        else:
            black_pixels += black * run
            header = bytes((row >> 8, row & 0xFF, 0x00, black & 0xFF, (black >> 8) & 0xFF, run))
            packets.append(build_frame(Cmd.PRINT_BITMAP_ROW, header + data))
        row += run

    return EncodedBitmap(
        rows=height,
        cols=cols,
        packets=packets,
        black_pixels=black_pixels,
        raw=raw,
        stride=stride,
    )
