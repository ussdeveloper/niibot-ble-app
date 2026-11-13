"""High level Niimbot printer client."""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import asdict, dataclass, field

from .bitmap import EncodedBitmap
from .ble import BleLink
from .models import INVERTED_LID_MODELS, TOTAL_HEIGHT_INCLUDES_GAP, PrinterModel, model_for_id
from .protocol import (
    B1_HANDSHAKE_INFO,
    Cmd,
    Frame,
    InfoType,
    LABEL_TYPE_NAMES,
    ProtocolError,
    Resp,
    build_connect_frame,
    u16_at,
)

log = logging.getLogger(__name__)


@dataclass
class PrinterInfo:
    model_id: int | None = None
    model_key: str = "UNKNOWN"
    model_name: str = "nieznany"
    dpi: int = 203
    printhead_px: int = 384
    print_task: str = "b1"
    protocol_version: int | None = None
    firmware: str | None = None
    hardware: str | None = None
    serial: str | None = None
    bluetooth_address: str | None = None
    battery: int | None = None
    density: int | None = None
    label_type: int | None = None


@dataclass
class PaperGeometry:
    """Label geometry reported by the printer itself (``0x59`` GetPaperInfo).

    This is measured by the printer's paper sensor, so it is the authoritative
    answer to "what format is on the roll" — independent of the RFID tag, which
    does not expose the size.
    """

    valid: bool = False
    #: ``False`` when the printer explicitly answered "not supported" (``0x00``).
    supported: bool | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    gap_mm: float | None = None
    label_type: int | None = None
    direction: int | None = None
    tail_mm: float | None = None
    width_px: int | None = None
    height_px: int | None = None
    raw: str | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["label_type_name"] = LABEL_TYPE_NAMES.get(self.label_type or -1)
        return payload


@dataclass
class PaperStatus:
    """Result of a heartbeat + RFID read."""

    lid_closed: bool | None = None
    paper_inserted: bool | None = None
    rfid_ok: bool | None = None
    battery: int | None = None
    temperature: int | None = None
    label_type: int | None = None
    label_type_name: str | None = None
    #: ``rfid`` when the type came from the roll tag, ``printer`` when it is
    #: just the printer's current setting.
    label_type_source: str | None = None
    barcode: str | None = None
    serial: str | None = None
    uuid: str | None = None
    capacity: int | None = None
    #: Raw ``print_limit`` word from the tag (the consumable's DRM cap).
    print_limit: int | None = None
    #: True when ``capacity`` was derived from the tag's DRM limit
    #: (``print_limit / 1.2``, the ratio Niimbot provisions rolls with) instead
    #: of being read straight from the tag.
    capacity_estimated: bool = False
    used: int | None = None
    remaining: int | None = None
    raw_heartbeat: str | None = None
    raw_rfid: str | None = None
    #: Geometry measured by the printer (``0x59``), if it answered.
    detected_width_mm: float | None = None
    detected_height_mm: float | None = None
    detected_gap_mm: float | None = None
    detected_label_type: int | None = None
    detected_width_px: int | None = None
    detected_height_px: int | None = None
    #: ``False`` when the firmware answered "not supported" to the geometry query.
    geometry_supported: bool | None = None
    geometry_raw: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class NiimbotPrinter:
    def __init__(self, link: BleLink | None = None) -> None:
        self.link = link or BleLink()
        self.info = PrinterInfo()
        self.paper = PaperStatus()
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    async def connect(self, address: str) -> PrinterInfo:
        await self.link.connect(address)
        try:
            await self._send_connect()
            await self.identify()
            if self.info.print_task == "b1":
                await self._b1_handshake()
            else:
                await self._quick_handshake()
            await self.read_info()
        except Exception:
            await self.link.disconnect()
            raise
        self.link.write_delay = 0.01 if self.info.print_task == "b1" else 0.0
        log.info(
            "Połączono: %s (id=%s, dpi=%s, głowica=%s px, protokół=%s, zadanie=%s)",
            self.info.model_name,
            self.info.model_id,
            self.info.dpi,
            self.info.printhead_px,
            self.info.protocol_version,
            self.info.print_task,
        )
        return self.info

    async def disconnect(self) -> None:
        await self.link.disconnect()

    @property
    def connected(self) -> bool:
        return self.link.connected

    async def _send_connect(self) -> Frame | None:
        frame = await self.link.send_raw(build_connect_frame(), timeout=6.0)
        if frame is None:
            raise ProtocolError("Drukarka nie odpowiedziała na pakiet połączenia.")
        return frame

    async def identify(self) -> None:
        frame = await self.link.send(Cmd.PRINTER_STATUS_DATA, b"\x01", timeout=3.0)
        if frame and len(frame.data) >= 13:
            self.info.protocol_version = frame.data[11] * 100 + frame.data[12]
        model_frame = await self.link.send(Cmd.PRINTER_INFO, bytes((InfoType.MODEL_ID,)), timeout=3.0)
        self.info.model_id = _info_int(model_frame)
        model = model_for_id(self.info.model_id)
        self._apply_model(model)

    def _apply_model(self, model: PrinterModel) -> None:
        protocol = self.info.protocol_version or 0
        task = model.print_task
        if task is None:
            task = "v4" if protocol >= 4 else "b1"
        self.info.model_key = model.key
        self.info.model_name = model.name
        self.info.dpi = model.dpi
        self.info.printhead_px = model.printhead_px
        self.info.print_task = task

    async def _b1_handshake(self) -> None:
        """The B1 ignores print commands until this exact handshake is done."""
        await self.link.send(Cmd.PRINTER_STATUS_DATA, b"\x01", timeout=3.0)
        for info_type in B1_HANDSHAKE_INFO:
            await self.link.send(
                Cmd.PRINTER_INFO,
                bytes((int(info_type),)),
                expect=(0x40 + int(info_type),),
                timeout=1.5,
            )
        await self.link.send(Cmd.HEARTBEAT, b"\x04", timeout=3.0)

    async def _quick_handshake(self) -> None:
        await self.link.send(Cmd.PRINTER_STATUS_DATA, b"\x01", timeout=3.0)
        await self.link.send(Cmd.HEARTBEAT, b"\x04", timeout=3.0)

    # ------------------------------------------------------------------ info
    async def get_info(self, info_type: InfoType) -> bytes | None:
        frame = await self.link.send(
            Cmd.PRINTER_INFO, bytes((int(info_type),)), expect=(0x40 + int(info_type),), timeout=1.5
        )
        if frame is None or frame.cmd == Resp.NOT_SUPPORTED:
            return None
        return frame.data

    async def read_info(self) -> PrinterInfo:
        for info_type in (
            InfoType.DENSITY,
            InfoType.LABEL_TYPE,
            InfoType.FIRMWARE,
            InfoType.HARDWARE,
            InfoType.SERIAL,
            InfoType.BLUETOOTH,
            InfoType.BATTERY,
        ):
            data = await self.get_info(info_type)
            if data is None:
                continue
            if info_type == InfoType.DENSITY:
                self.info.density = _to_int(data)
            elif info_type == InfoType.LABEL_TYPE:
                self.info.label_type = _to_int(data)
            elif info_type == InfoType.FIRMWARE:
                self.info.firmware = _to_version(data)
            elif info_type == InfoType.HARDWARE:
                self.info.hardware = _to_version(data)
            elif info_type == InfoType.SERIAL:
                self.info.serial = _to_text(data)
            elif info_type == InfoType.BLUETOOTH:
                self.info.bluetooth_address = ":".join(f"{b:02X}" for b in data) if data else None
            elif info_type == InfoType.BATTERY:
                self.info.battery = _to_int(data)
        return self.info

    # --------------------------------------------------------------- status
    async def heartbeat(self) -> dict:
        frame = await self.link.send(Cmd.HEARTBEAT, b"\x04", timeout=3.0)
        if frame is None or frame.cmd == Resp.NOT_SUPPORTED:
            return {}
        return self._parse_heartbeat(frame)

    def _parse_heartbeat(self, frame: Frame) -> dict:
        data = frame.data
        status: dict = {"raw": data.hex()}
        invert = self.info.model_id in INVERTED_LID_MODELS

        if frame.cmd == Resp.HEARTBEAT_ADVANCED2:
            if len(data) >= 9:
                status["battery"] = data[2]
                status["temperature"] = data[3]
                status["lid_closed"] = (data[4] == 0) != invert
                status["paper_inserted"] = data[5] == 0
                status["rfid_ok"] = data[6] != 0
        elif frame.cmd == Resp.HEARTBEAT_ADVANCED1:
            layout = {
                10: (8, 9, None, None),
                13: (9, 10, 11, 12),
                19: (15, 16, 17, 18),
                20: (None, None, 18, 19),
            }.get(len(data))
            if layout:
                lid_idx, battery_idx, paper_idx, rfid_idx = layout
                if lid_idx is not None:
                    status["lid_closed"] = (data[lid_idx] == 0) != invert
                if battery_idx is not None:
                    status["battery"] = data[battery_idx]
                if paper_idx is not None:
                    status["paper_inserted"] = data[paper_idx] == 0
                if rfid_idx is not None:
                    status["rfid_ok"] = data[rfid_idx] != 0
        return status

    async def read_rfid(self) -> dict | None:
        """Read the consumable tag. Returns ``None`` when no tag answers."""
        frame = await self.link.send(Cmd.RFID_INFO, b"\x01", timeout=1.2)
        if frame is None or frame.cmd == Resp.NOT_SUPPORTED or len(frame.data) <= 1:
            return None
        data = frame.data
        try:
            uuid = data[0:8].hex()
            index = 8
            barcode_len = data[index]
            index += 1
            barcode = data[index : index + barcode_len].decode("ascii", "replace")
            index += barcode_len
            serial_len = data[index]
            index += 1
            serial = data[index : index + serial_len].decode("ascii", "replace")
            index += serial_len
            print_limit = u16_at(data, index)
            used = u16_at(data, index + 2)
            index += 4
            consumable_type = data[index] if len(data) > index else None
            index += 1
            capacity = u16_at(data, index)
        except (IndexError, ValueError):
            return {"raw": data.hex(), "raw_only": True}
        return {
            "uuid": uuid,
            "barcode": barcode.strip() or None,
            "serial": serial.strip() or None,
            "print_limit": print_limit,
            "used": used,
            "consumable_type": consumable_type,
            "capacity": capacity,
            "raw": data.hex(),
        }

    async def read_paper_info(self, timeout: float = 1.5) -> PaperGeometry:
        """Ask the printer for the measured label format (``0x59`` → ``0x69``).

        Payload layout (18 bytes, big-endian)::

            gap_px(u16) total_px(u16) type(u8) gap(u16/10) total(u16/10)
            width_px(u16) width(u16/10) direction(u8) tail_px(u16) tail(u16/10)
        """
        frame = await self.link.send(Cmd.GET_PAPER_INFO, b"\x01", timeout=timeout)
        if frame is None:
            return PaperGeometry()
        if frame.cmd == Resp.NOT_SUPPORTED:
            return PaperGeometry(supported=False, raw=frame.data.hex())
        data = frame.data
        if len(data) != 18:
            return PaperGeometry(raw=data.hex())

        gap_px = int.from_bytes(data[0:2], "big")
        total_px = int.from_bytes(data[2:4], "big")
        label_type = data[4]
        gap_mm = int.from_bytes(data[5:7], "big") / 10
        total_mm = int.from_bytes(data[7:9], "big") / 10
        width_px = int.from_bytes(data[9:11], "big")
        width_mm = int.from_bytes(data[11:13], "big") / 10
        direction = data[13]
        tail_px = int.from_bytes(data[14:16], "big")
        tail_mm = int.from_bytes(data[16:18], "big") / 10

        height_mm = total_mm
        height_px = total_px
        if self.info.model_id in TOTAL_HEIGHT_INCLUDES_GAP:
            height_mm = round(max(0.0, total_mm - gap_mm), 1)
            height_px = max(0, total_px - gap_px)

        return PaperGeometry(
            valid=height_mm > 0 and width_mm > 0,
            supported=True,
            width_mm=width_mm,
            height_mm=height_mm,
            gap_mm=gap_mm,
            label_type=label_type,
            direction=direction,
            tail_mm=tail_mm,
            width_px=width_px,
            height_px=height_px,
            raw=data.hex(),
        )

    async def refresh(self) -> PaperStatus:
        status = PaperStatus()
        try:
            heartbeat = await self.heartbeat()
        except ProtocolError as exc:
            heartbeat = {}
            status.errors.append(str(exc))
        if heartbeat:
            status.lid_closed = heartbeat.get("lid_closed")
            status.paper_inserted = heartbeat.get("paper_inserted")
            status.rfid_ok = heartbeat.get("rfid_ok")
            if heartbeat.get("battery") is not None:
                status.battery = heartbeat["battery"]
                if self.info.battery is None:
                    self.info.battery = status.battery
            status.temperature = heartbeat.get("temperature")
            status.raw_heartbeat = heartbeat.get("raw")

        try:
            rfid = await self.read_rfid()
        except ProtocolError as exc:
            rfid = None
            status.errors.append(str(exc))
        if rfid and not rfid.get("raw_only"):
            status.barcode = rfid.get("barcode")
            status.serial = rfid.get("serial")
            status.uuid = rfid.get("uuid")
            status.capacity = rfid.get("capacity")
            status.print_limit = rfid.get("print_limit")
            if status.capacity is None and status.print_limit:
                # Niimbot writes the tag limit as 120 % of the nominal roll size.
                status.capacity = round(status.print_limit / 1.2)
                status.capacity_estimated = True
            status.used = rfid.get("used")
            status.label_type = rfid.get("consumable_type")
            status.label_type_source = "rfid" if status.label_type is not None else None
            status.raw_rfid = rfid.get("raw")
            if status.capacity is not None and status.used is not None:
                status.remaining = max(0, status.capacity - status.used)
        elif rfid:
            status.raw_rfid = rfid.get("raw")

        try:
            geometry = await self.read_paper_info()
        except ProtocolError as exc:
            geometry = PaperGeometry()
            status.errors.append(str(exc))
        if geometry.valid:
            status.detected_width_mm = geometry.width_mm
            status.detected_height_mm = geometry.height_mm
            status.detected_gap_mm = geometry.gap_mm
            status.detected_label_type = geometry.label_type
            status.detected_width_px = geometry.width_px
            status.detected_height_px = geometry.height_px
        status.geometry_supported = geometry.supported
        if geometry.raw:
            status.geometry_raw = geometry.raw

        if status.label_type is None:
            status.label_type = self.info.label_type
            if status.label_type is not None:
                status.label_type_source = "printer"
        if status.label_type is not None:
            status.label_type_name = LABEL_TYPE_NAMES.get(status.label_type, f"typ {status.label_type}")

        self.paper = status
        return status

    # -------------------------------------------------------------- commands
    async def _expect(self, cmd: Cmd, data: bytes = b"\x01", timeout: float = 5.0) -> Frame:
        frame = await self.link.send(cmd, data, timeout=timeout)
        if frame is None:
            raise ProtocolError(f"Brak odpowiedzi na polecenie 0x{int(cmd):02X}")
        if frame.cmd == Resp.NOT_SUPPORTED:
            raise ProtocolError(f"Drukarka nie obsługuje polecenia 0x{int(cmd):02X}")
        return frame

    async def set_density(self, density: int) -> None:
        density = max(1, min(5, int(density)))
        await self._expect(Cmd.SET_DENSITY, bytes((density,)), timeout=2.0)
        self.info.density = density

    async def set_label_type(self, label_type: int) -> None:
        await self._expect(Cmd.SET_LABEL_TYPE, bytes((int(label_type),)), timeout=2.0)
        self.info.label_type = int(label_type)

    async def calibrate(self) -> None:
        """Run the printer's label positioning calibration (feeds paper)."""
        await self.link.send(Cmd.LABEL_POSITIONING_CALIBRATION, b"\x01", timeout=25.0)

    async def cancel(self) -> None:
        try:
            await self.link.send(Cmd.CANCEL_PRINT, b"\x01", timeout=3.0)
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("Anulowanie nie powiodło się: %s", exc)

    # ---------------------------------------------------------------- print
    async def print_bitmap(
        self,
        bitmap: EncodedBitmap,
        *,
        copies: int = 1,
        density: int | None = None,
        label_type: int | None = None,
        on_progress=None,
    ) -> None:
        if not self.connected:
            raise ProtocolError("Drukarka nie jest połączona.")
        copies = max(1, min(999, int(copies)))
        task = self.info.print_task
        self.link.write_delay = 0.01 if task == "b1" else 0.0

        if density is not None:
            await self.set_density(density)
        if label_type is not None and label_type != self.info.label_type:
            await self.set_label_type(label_type)

        if task == "b1":
            # 7 byte PrintStart - no speed byte.
            await self._expect(Cmd.PRINT_START, struct.pack(">HBBBBB", copies, 0, 0, 0, 0, 0), timeout=6.0)
            await self._expect(Cmd.PAGE_START, b"\x01", timeout=6.0)
            await self._expect(Cmd.SET_PAGE_SIZE, struct.pack(">HHH", bitmap.rows, bitmap.cols, copies), timeout=6.0)
        else:
            # 9 byte PrintStart (protocol v4+): pages, 4x0, colour, speed, flag.
            await self._expect(Cmd.PRINT_START, struct.pack(">HBBBBBBB", copies, 0, 0, 0, 0, 0, 1, 0), timeout=6.0)
            await self.link.send(Cmd.PRINT_STATUS, b"\x01", wait=False)
            await asyncio.sleep(0.03)
            await self._expect(
                Cmd.SET_PAGE_SIZE,
                # rows, cols, copies, cut height, cut type, 0x00, send-all, part height
                struct.pack(">HHHHBBBH", bitmap.rows, bitmap.cols, copies, 0, 0, 0, 0, 0),
                timeout=6.0,
            )

        total = len(bitmap.packets)
        step = 40
        for index in range(0, total, step):
            await self.link.write_frames(bitmap.packets[index : index + step])
            if on_progress:
                sent = min(index + step, total)
                on_progress(sent / total * 0.9 if total else 0.9)
        if on_progress:
            on_progress(0.9)

        await self._expect(Cmd.PAGE_END, b"\x01", timeout=40.0)
        await self._wait_print_finished(copies, on_progress)
        await self._expect(Cmd.PRINT_END, b"\x01", timeout=15.0)
        if on_progress:
            on_progress(1.0)

    async def _wait_print_finished(self, copies: int, on_progress=None) -> None:
        deadline = time.monotonic() + 25.0 + 20.0 * copies
        while time.monotonic() < deadline:
            frame = await self.link.send(Cmd.PRINT_STATUS, b"\x01", timeout=2.0)
            if frame is not None and len(frame.data) >= 2:
                page = int.from_bytes(frame.data[0:2], "big")
                print_pct = frame.data[2] if len(frame.data) > 2 else 0
                feed_pct = frame.data[3] if len(frame.data) > 3 else 0
                if on_progress:
                    on_progress(min(0.99, 0.9 + 0.1 * page / copies), page=page, print_pct=print_pct, feed_pct=feed_pct)
                if page >= copies:
                    return
            await asyncio.sleep(0.3)
        raise ProtocolError("Drukarka nie potwierdziła zakończenia wydruku (timeout).")


def _to_int(data: bytes) -> int | None:
    if not data:
        return None
    return int.from_bytes(data, "big")


def _to_version(data: bytes) -> str:
    if not data:
        return "?"
    value = int.from_bytes(data, "big")
    return f"{value / 100:.2f}"


def _to_text(data: bytes) -> str:
    try:
        return data.rstrip(b"\x00").decode("ascii")
    except UnicodeDecodeError:
        return data.hex()


def _info_int(frame: Frame | None) -> int | None:
    if frame is None or not frame.data:
        return None
    data = frame.data
    if len(data) == 1:
        return data[0] << 8
    return int.from_bytes(data[0:2], "big")
