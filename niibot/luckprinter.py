"""LuckPrinter / AiYin protocol — the family the Fichero D11s belongs to.

The Fichero label printer sold at Action is an AiYin D11s made by Xiamen Print Future
Technology, driven by the LuckPrinter SDK (``com.luckprinter.sdk_new``). The same SDK
powers 159+ OEM models (Karsten and other house brands included), so this module covers
the whole family rather than one device.

Command reference: the reverse-engineered notes in ``0xMH/fichero-printer``
(``docs/PROTOCOL.md``) — every byte below was verified against real hardware there.
Everything in this file is pure: no Bluetooth, no I/O, easy to test.
"""

from __future__ import annotations

#: A printer that is asleep ignores the first bytes; twelve zeroes wake it.
WAKE = b"\x00" * 12
#: Advance to the next label (also positions the gap sensor).
FORM_FEED = b"\x1d\x0c"

REQUESTS = {
    "model": b"\x10\xff\x20\xf0",
    "firmware": b"\x10\xff\x20\xf1",
    "serial": b"\x10\xff\x20\xf2",
    "boot": b"\x10\xff\x20\xef",
    "battery": b"\x10\xff\x50\xf1",
    "status": b"\x10\xff\x40",
    "density": b"\x10\xff\x11",
    "shutdown": b"\x10\xff\x13",
    "info": b"\x10\xff\x70",
}

#: Status byte bits from the ``10 FF 40`` reply.
STATUS_BITS = {
    0x01: "printing",
    0x02: "cover_open",
    0x04: "out_of_paper",
    0x08: "low_battery",
    0x10: "overheated_alt",
    0x20: "charging",
    0x40: "overheated",
}

#: Enable/stop frames differ per device class; using the wrong pair makes the printer
#: accept the raster silently and never print.
FAMILIES = {
    "aiyin": (b"\x10\xff\xfe\x01", b"\x10\xff\xfe\x45"),
    "lujiang": (b"\x10\xff\xf1\x03", b"\x10\xff\xf1\x45"),
}

PAPER_TYPES = {0: "gap", 1: "black_mark", 2: "continuous"}


def set_density(level: int) -> bytes:
    """0 = light, 1 = medium, 2 = thick (the printer's own scale, not Niimbot's 1-5)."""
    return b"\x10\xff\x10\x00" + bytes((max(0, min(2, int(level))),))


def set_paper_type(kind: int) -> bytes:
    return b"\x10\xff\x84" + bytes((max(0, min(2, int(kind))),))


def set_shutdown(minutes: int) -> bytes:
    value = max(1, min(480, int(minutes)))
    return b"\x10\xff\x12" + value.to_bytes(2, "big")


def enable_command(family: str = "aiyin") -> bytes:
    return FAMILIES.get(family, FAMILIES["aiyin"])[0]


def stop_command(family: str = "aiyin") -> bytes:
    return FAMILIES.get(family, FAMILIES["aiyin"])[1]


def build_raster(raw: bytes, width_px: int, height: int, mode: int = 0) -> bytes:
    """``GS v 0`` raster transfer: 8 pixels per byte, MSB first, 1 = black."""
    stride = (int(width_px) + 7) // 8
    expected = stride * int(height)
    payload = raw[:expected] if len(raw) >= expected else raw + b"\x00" * (expected - len(raw))
    header = b"\x1d\x76\x30" + bytes((max(0, min(3, int(mode))),))
    header += stride.to_bytes(2, "little") + int(height).to_bytes(2, "little")
    return header + payload


def parse_status(data: bytes) -> dict:
    """Decode the status reply (first byte is a bitmask)."""
    value = data[0] if data else 0
    flags = {name: bool(value & mask) for mask, name in STATUS_BITS.items()}
    flags["raw"] = value
    flags["ready"] = value == 0
    return flags


def decode_text(data: bytes) -> str:
    return bytes(data).decode("ascii", "replace").strip("\x00 \r\n")


def parse_battery(data: bytes) -> dict:
    """``[status, percent]`` — e.g. ``00 56`` is 86 %."""
    if len(data) < 2:
        return {"percent": None}
    return {"status": data[0], "percent": data[1], "charging": data[0] == 1}


def parse_info(text: str) -> dict:
    """The ``10 FF 70`` answer: ``BT_NAME|MAC_CLASSIC|MAC_BLE|FIRMWARE|SERIAL|BATTERY``."""
    parts = [part.strip() for part in text.split("|")]
    keys = ("name", "mac_classic", "mac_ble", "firmware", "serial", "battery")
    result: dict = {}
    for key, value in zip(keys, parts):
        result[key] = value
    if result.get("battery"):
        try:
            result["battery"] = int(result["battery"])
        except ValueError:
            result["battery"] = None
    return result
