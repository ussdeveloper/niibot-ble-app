"""Niimbot BLE protocol frame format and opcodes.

Frame layout (as used by the B1 / B21 / D11 family):

    55 55 <cmd> <len> <data...> <checksum> AA AA

``checksum`` is ``cmd XOR len XOR data[0] XOR ...``.  Multi-byte integers are
big-endian.  The connect packet is the only frame that carries a ``0x03``
prefix: ``03 55 55 C1 01 01 C1 AA AA``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

HEAD = b"\x55\x55"
TAIL = b"\xaa\xaa"
CONNECT_PREFIX = 0x03


class Cmd(IntEnum):
    """Command ids sent from host to printer."""

    PRINT_START = 0x01
    PAGE_START = 0x03
    PRINTER_INFO = 0x40
    SET_PAGE_SIZE = 0x13
    RFID_INFO = 0x1A
    GET_PAPER_INFO = 0x59
    SET_DENSITY = 0x21
    SET_LABEL_TYPE = 0x23
    PRINTER_CHECK_LINE = 0x86
    PRINT_BITMAP_ROW_INDEXED = 0x83
    PRINT_EMPTY_ROW = 0x84
    PRINT_BITMAP_ROW = 0x85
    #: Wrapper for one row on two-colour stock: ``<colour mode> <inner command> <payload…>``.
    PRINT_BITMAP_ROW_DOUBLE_COLOR = 0x8A
    LABEL_POSITIONING_CALIBRATION = 0x8E
    PRINT_STATUS = 0xA3
    PRINTER_STATUS_DATA = 0xA5
    CONNECT = 0xC1
    CANCEL_PRINT = 0xDA
    HEARTBEAT = 0xDC
    PAGE_END = 0xE3
    PRINT_END = 0xF3


class Resp(IntEnum):
    """Command ids sent from printer to host."""

    NOT_SUPPORTED = 0x00
    PRINT_START = 0x02
    PAGE_START = 0x04
    PRINTER_INFO_DENSITY = 0x41
    PRINTER_INFO_SPEED = 0x42
    PRINTER_INFO_LABEL_TYPE = 0x43
    PRINTER_INFO_LANGUAGE = 0x46
    PRINTER_INFO_AUTOSHUTDOWN = 0x47
    PRINTER_INFO_MODEL = 0x48
    PRINTER_INFO_FIRMWARE = 0x49
    PRINTER_INFO_BATTERY = 0x4A
    PRINTER_INFO_SERIAL = 0x4B
    PRINTER_INFO_HARDWARE = 0x4C
    PRINTER_INFO_BLUETOOTH = 0x4D
    SET_PAGE_SIZE = 0x14
    RFID_INFO = 0x1B
    GET_PAPER_INFO = 0x69
    SET_DENSITY = 0x31
    SET_LABEL_TYPE = 0x33
    CALIBRATION = 0x8F
    PRINTER_CHECK_LINE = 0xD3
    PRINT_STATUS = 0xB3
    PRINTER_STATUS_DATA = 0xB5
    CONNECT = 0xC2
    CANCEL_PRINT = 0xD0
    HEARTBEAT_ADVANCED2 = 0xD9
    PRINT_ERROR = 0xDB
    HEARTBEAT_ADVANCED1 = 0xDD
    HEARTBEAT_BASIC = 0xDE
    HEARTBEAT_INFO = 0xDF
    PAGE_END = 0xE4
    PRINT_END = 0xF4


#: Request opcode -> accepted response opcodes.
RESPONSES: dict[int, tuple[int, ...]] = {
    Cmd.PRINT_START: (Resp.PRINT_START,),
    Cmd.PAGE_START: (Resp.PAGE_START,),
    Cmd.PRINTER_INFO: tuple(range(0x41, 0x50)),
    Cmd.SET_PAGE_SIZE: (Resp.SET_PAGE_SIZE,),
    Cmd.RFID_INFO: (Resp.RFID_INFO,),
    Cmd.GET_PAPER_INFO: (Resp.GET_PAPER_INFO,),
    Cmd.SET_DENSITY: (Resp.SET_DENSITY,),
    Cmd.SET_LABEL_TYPE: (Resp.SET_LABEL_TYPE,),
    Cmd.LABEL_POSITIONING_CALIBRATION: (Resp.CALIBRATION,),
    Cmd.PRINT_STATUS: (Resp.PRINT_STATUS,),
    Cmd.PRINTER_STATUS_DATA: (Resp.PRINTER_STATUS_DATA,),
    Cmd.CONNECT: (Resp.CONNECT,),
    Cmd.CANCEL_PRINT: (Resp.CANCEL_PRINT,),
    Cmd.HEARTBEAT: (
        Resp.HEARTBEAT_ADVANCED2,
        Resp.HEARTBEAT_ADVANCED1,
        Resp.HEARTBEAT_BASIC,
        Resp.HEARTBEAT_INFO,
    ),
    Cmd.PAGE_END: (Resp.PAGE_END,),
    Cmd.PRINT_END: (Resp.PRINT_END,),
}


class InfoType(IntEnum):
    """Sub-codes for the ``PRINTER_INFO`` (0x40) query."""

    DENSITY = 0x01
    SPEED = 0x02
    LABEL_TYPE = 0x03
    LANGUAGE = 0x06
    AUTO_SHUTDOWN = 0x07
    MODEL_ID = 0x08
    FIRMWARE = 0x09
    BATTERY = 0x0A
    SERIAL = 0x0B
    HARDWARE = 0x0C
    BLUETOOTH = 0x0D
    PRINT_MODE = 0x0E


#: ``PRINTER_INFO`` sub-codes queried during the B1 handshake.
B1_HANDSHAKE_INFO = (
    InfoType.MODEL_ID,
    InfoType.SERIAL,
    InfoType.BLUETOOTH,
    InfoType.BATTERY,
    InfoType.AUTO_SHUTDOWN,
    InfoType.LABEL_TYPE,
    InfoType.HARDWARE,
    InfoType.FIRMWARE,
)


class LabelType(IntEnum):
    """Paper kinds understood by ``SET_LABEL_TYPE`` and the RFID tag."""

    INVALID = 0
    WITH_GAPS = 1
    BLACK = 2
    CONTINUOUS = 3
    PERFORATED = 4
    TRANSPARENT = 5
    PVC_TAG = 6
    BLACK_MARK_GAP = 10
    HEAT_SHRINK_TUBE = 11


LABEL_TYPE_NAMES: dict[int, str] = {
    0: "unknown",
    1: "labels with gap",
    2: "black mark",
    3: "continuous tape",
    4: "perforated labels",
    5: "transparent film",
    6: "PVC tag",
    10: "gap + black mark",
    11: "heat-shrink tube",
}


class PageColor(IntEnum):
    """Colour space of a page — the last byte of the ``PRINT_START`` payload."""

    SINGLE = 0
    #: Black and red on two-colour stock; red is the low-energy dot.
    DOUBLE = 1
    SINGLE_ALT = 2
    GRAYSCALE = 3


class BitmapColorMode(IntEnum):
    """Colour slot of the inner row inside a ``PRINT_BITMAP_ROW_DOUBLE_COLOR`` wrapper."""

    EMPTY = 0
    RED = 1
    BLACK = 2
    MIXED = 3

PRINTER_ERRORS: dict[int, str] = {
    0x01: "lid open",
    0x02: "out of paper",
    0x03: "battery level low",
    0x04: "battery error",
    0x05: "cancelled by the user",
    0x06: "data error",
    0x07: "printhead overheat",
    0x08: "paper ran out",
    0x09: "printer busy",
    0x0A: "printhead missing",
    0x0B: "temperature too low",
    0x0C: "printhead loose",
    0x0D: "no ribbon",
    0x0E: "invalid ribbon",
    0x0F: "ribbon used up",
    0x10: "invalid paper",
    0x11: "could not set the paper",
    0x13: "could not set the density",
    0x16: "communication error",
    0x17: "disconnected",
    0x18: "invalid image parameters",
    0x1C: "check the paper",
    0x1D: "the RFID tag was not written",
    0x1E: "density not supported",
    0x20: "label material error",
    0x32: "invalid page",
    0x34: "transmission timeout",
}


class ProtocolError(RuntimeError):
    """Raised when the printer reports an error or answers unexpectedly."""


class PrinterError(ProtocolError):
    """Raised when the printer returns a ``0xDB`` error frame."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"The printer reported an error: {PRINTER_ERRORS.get(code, hex(code))} (0x{code:02X})")


@dataclass(frozen=True)
class Frame:
    cmd: int
    data: bytes

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Frame(0x{self.cmd:02X}, {self.data.hex()})"


def checksum(cmd: int, data: bytes) -> int:
    value = cmd ^ len(data)
    for byte in data:
        value ^= byte
    return value


def build_frame(cmd: int, data: bytes = b"") -> bytes:
    return HEAD + bytes((cmd & 0xFF, len(data) & 0xFF)) + data + bytes((checksum(cmd, data),)) + TAIL


def build_connect_frame() -> bytes:
    """The connect frame is the only one carrying a ``0x03`` prefix."""
    return bytes((CONNECT_PREFIX,)) + build_frame(Cmd.CONNECT, b"\x01")


class FrameReader:
    """Incremental parser for the printer's byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._dropped = 0

    @property
    def dropped_bytes(self) -> int:
        return self._dropped

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer.extend(chunk)
        frames: list[Frame] = []
        while True:
            frame = self._pop_frame()
            if frame is None:
                return frames
            frames.append(frame)

    def _pop_frame(self) -> Frame | None:
        buf = self._buffer
        while True:
            start = buf.find(HEAD)
            if start < 0:
                if len(buf) > 1:
                    self._dropped += len(buf) - 1
                    del buf[: len(buf) - 1]
                return None
            if start:
                self._dropped += start
                del buf[:start]
            if len(buf) < 5:
                return None
            length = buf[3]
            total = length + 7
            if len(buf) < total:
                return None
            data = bytes(buf[4 : 4 + length])
            cmd = buf[2]
            crc = buf[4 + length]
            if buf[total - 2 : total] != TAIL or crc != checksum(cmd, data):
                # Not a valid frame - drop one byte and try to resynchronise.
                self._dropped += 1
                del buf[:1]
                continue
            del buf[:total]
            return Frame(cmd, data)


def u16(value: int) -> bytes:
    return struct.pack(">H", value)


def u16_at(data: bytes, offset: int) -> int | None:
    if len(data) < offset + 2:
        return None
    return int.from_bytes(data[offset : offset + 2], "big")
