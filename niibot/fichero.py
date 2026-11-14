"""Fichero / Karsten (AiYin D11s, LuckPrinter SDK) driver.

The printer speaks a simple byte protocol over a UART-like BLE characteristic: plain
command bytes out, plain replies (ASCII or a couple of bytes) back — no framing, no
checksums. This module wraps that into the same interface :class:`niibot.printer.
NiimbotPrinter` offers, so the panel, the renderer and the Windows queue work unchanged.

Protocol reference: https://github.com/0xMH/fichero-printer (``docs/PROTOCOL.md``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from . import luckprinter as lp
from .printer import PaperGeometry, PaperStatus, PrinterInfo

log = logging.getLogger(__name__)

#: The four interchangeable UART services the SDK exposes (write, notify).
KNOWN_UARTS: tuple[tuple[str, str], ...] = (
    ("000018f0-0000-1000-8000-00805f9b34fb", "00002af1-0000-1000-8000-00805f9b34fb"),
    ("0000ff00-0000-1000-8000-00805f9b34fb", "0000ff02-0000-1000-8000-00805f9b34fb"),
    ("e7810a71-73ae-499d-8c15-faa9aef0c3f2", "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"),
    ("49535343-fe7d-4ae5-8fa9-9fafd205e455", "49535343-8841-43f4-a8d4-ecbe34729bb3"),
)

#: Names the vendor sticks on these printers, used for the protocol auto-detection.
NAME_HINTS = ("fichero", "d11s", "d12", "aiyin", "karsten", "luckp", "dp_d1")


class _Reader:
    """Stand-in for the Niimbot reader so the diagnostics endpoint keeps working."""

    dropped_bytes = 0


class FicheroLink:
    """Raw byte transport over the printer's write/notify characteristic pair."""

    def __init__(self) -> None:
        self._client = None
        self._write_char: str | None = None
        self._notify_char: str | None = None
        self._buffer = bytearray()
        self._event = asyncio.Event()
        self._inbox: deque[bytes] = deque(maxlen=200)
        self._traffic: list[dict] = []
        self._reader = _Reader()
        self.address: str | None = None
        self.name: str | None = None
        self.connected = False
        self.last_error: str | None = None
        self.max_write = 180

    # ------------------------------------------------------------------ setup
    async def connect(self, address: str, timeout: float = 12.0) -> None:
        from bleak import BleakClient

        await self.disconnect()
        client = BleakClient(address, timeout=timeout, disconnected_callback=self._on_disconnected)
        await client.connect()
        self._client = client
        self.address = address
        self.name = client.name or address
        try:
            self._pick_characteristics(client)
        except Exception:
            await client.disconnect()
            self._client = None
            raise
        await client.start_notify(self._notify_char, self._on_notify)
        self.connected = True
        self.last_error = None
        log.info("Connected to %s (%s, LuckPrinter family)", self.name, address)

    def _on_disconnected(self, client) -> None:  # type: ignore[no-untyped-def]
        # A stale client closing (during a reconnect) must not mark the live link as dead.
        if self._client is not None and client is not self._client:
            return
        if self.connected:
            log.warning("The printer dropped the Bluetooth link")
        self.connected = False
        self._event.set()

    @property
    def alive(self) -> bool:
        """True while the underlying BLE client still thinks it is connected."""
        client = self._client
        return bool(self.connected and client is not None and getattr(client, "is_connected", True))

    def _pick_characteristics(self, client) -> None:  # type: ignore[no-untyped-def]
        by_uuid = {}
        for service in client.services:
            for char in service.characteristics:
                by_uuid[str(char.uuid).lower()] = char
        for write_uuid, notify_uuid in KNOWN_UARTS:
            write = by_uuid.get(write_uuid)
            notify = by_uuid.get(notify_uuid)
            if write and notify:
                self._write_char = str(write.uuid)
                self._notify_char = str(notify.uuid)
                return
        # Unknown clone: take the first pair of a writable and a notifying characteristic.
        for service in client.services:
            writable = [c for c in service.characteristics if "write" in c.properties or "write-without-response" in c.properties]
            notifying = [c for c in service.characteristics if "notify" in c.properties or "indicate" in c.properties]
            if writable and notifying:
                self._write_char = str(writable[0].uuid)
                self._notify_char = str(notifying[0].uuid)
                return
        raise RuntimeError("No writable/notifying characteristic found — not a LuckPrinter device?")

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self.connected = False
        self._buffer.clear()
        if client is not None:
            try:
                await client.stop_notify(self._notify_char) if self._notify_char else None
            except Exception:  # noqa: BLE001 - tearing down, ignore
                pass
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------- data
    def _on_notify(self, _char, data) -> None:  # type: ignore[no-untyped-def]
        payload = bytes(data)
        self._buffer.extend(payload)
        self._inbox.append(payload)
        self._traffic.append({"dir": "in", "hex": payload.hex(), "at": time.time()})
        if len(self._traffic) > 400:
            del self._traffic[:100]
        self._event.set()

    async def write(self, payload: bytes) -> None:
        if not self._client or not self._write_char:
            raise RuntimeError("The printer is not connected")
        self._traffic.append({"dir": "out", "hex": payload.hex(), "at": time.time()})
        if len(self._traffic) > 400:
            del self._traffic[:100]
        for start in range(0, len(payload), self.max_write):
            chunk = payload[start : start + self.max_write]
            try:
                await self._client.write_gatt_char(self._write_char, chunk, response=False)
            except Exception:  # noqa: BLE001 - some stacks insist on a response
                await self._client.write_gatt_char(self._write_char, chunk, response=True)

    async def request(self, payload: bytes, timeout: float = 2.0) -> bytes:
        """Send ``payload`` and return whatever the printer says first."""
        self._buffer.clear()
        self._event.clear()
        await self.write(payload)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._buffer:
                data = bytes(self._buffer)
                self._buffer.clear()
                return data
            try:
                await asyncio.wait_for(self._event.wait(), timeout=max(0.02, deadline - loop.time()))
            except asyncio.TimeoutError:
                break
            self._event.clear()
        return b""

    async def wait_for_finish(self, timeout: float = 60.0) -> bool:
        """Wait for the printer's ``0xAA`` / ``OK`` after a stop command."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._buffer:
                data = bytes(self._buffer)
                self._buffer.clear()
                if b"\xaa" in data or b"OK" in data:
                    return True
            try:
                await asyncio.wait_for(self._event.wait(), timeout=min(0.25, max(0.02, deadline - loop.time())))
            except asyncio.TimeoutError:
                continue
            self._event.clear()
        return False

    def traffic(self, limit: int = 200) -> list[dict]:
        return self._traffic[-limit:]

    def clear_traffic(self) -> None:
        self._traffic.clear()


class FicheroPrinter:
    """Same surface as :class:`NiimbotPrinter`, backed by the LuckPrinter protocol."""

    def __init__(self) -> None:
        self.link = FicheroLink()
        self.info = PrinterInfo(
            model_key="FICHERO",
            model_name="D11s (Fichero / AiYin)",
            dpi=203,
            printhead_px=96,
            print_task="luckprinter",
        )
        self.paper = PaperStatus(label_type=1, label_type_name="labels with gaps", label_type_source="printer")
        self.last_error: str | None = None
        self._family = "aiyin"
        #: Set by the panel (Settings → Printer); the geometry override drives 96 px vs 142 px heads.
        self.geometry_override: dict = {}
        #: Extra advance after each label so it reaches the tear bar (0 = rely on the printer).
        self.feed_mm: float = 0.0

    @property
    def connected(self) -> bool:
        return self.link.connected

    # ------------------------------------------------------------- lifecycle
    async def connect(self, address: str) -> PrinterInfo:
        await self.link.connect(address)
        try:
            await self.identify()
            await self.refresh()
        except Exception:
            await self.link.disconnect()
            raise
        log.info("Fichero family printer ready: %s, %s dpi, %s px", self.info.model_name, self.info.dpi, self.info.printhead_px)
        return self.info

    async def disconnect(self) -> None:
        await self.link.disconnect()

    async def identify(self) -> None:
        model = lp.decode_text(await self.link.request(lp.REQUESTS["model"]))
        firmware = lp.decode_text(await self.link.request(lp.REQUESTS["firmware"]))
        serial = lp.decode_text(await self.link.request(lp.REQUESTS["serial"]))
        if model:
            self.info.model_name = f"{model} (Fichero / AiYin)"
        self.info.firmware = firmware or None
        self.info.serial = serial or None
        self._apply_geometry()
        # "lujiang" devices answer differently; keep the AiYin pair unless the model says otherwise.
        self._family = "aiyin"

    def _apply_geometry(self) -> None:
        override = getattr(self, "geometry_override", None) or {}
        self.info.dpi = int(override.get("dpi") or 203)
        self.info.printhead_px = int(override.get("printhead_px") or 96)

    def set_geometry_override(self, dpi: int | None = None, printhead_px: int | None = None, print_task: str | None = None) -> None:
        self.geometry_override = {"dpi": dpi, "printhead_px": printhead_px, "print_task": print_task}
        self._apply_geometry()

    def refresh_geometry(self) -> None:
        self._apply_geometry()

    # --------------------------------------------------------------- reading
    async def read_info(self) -> PrinterInfo:
        await self.identify()
        return self.info

    async def refresh(self) -> PaperStatus:
        status = lp.parse_status(await self.link.request(lp.REQUESTS["status"]))
        battery = lp.parse_battery(await self.link.request(lp.REQUESTS["battery"]))
        if not getattr(self, "_paper_type_sent", False):
            # One-time: make sure the printer is in gap mode, whatever a previous app left behind.
            try:
                await self.set_paper_type(self.paper.label_type or 1)
                self._paper_type_sent = True
            except Exception:  # noqa: BLE001 - best effort
                pass
        self.paper = PaperStatus(
            lid_closed=not status.get("cover_open", False),
            paper_inserted=not status.get("out_of_paper", False),
            rfid_ok=None,
            battery=battery.get("percent"),
            label_type=self.paper.label_type or 1,
            label_type_name="labels with gaps",
            label_type_source="printer",
            # No roll tag on this family: the calibration is found under this fixed key.
            barcode="AIYIN",
            serial=self.info.serial,
            capacity=None,
            raw_heartbeat=f"status={status.get('raw')}",
            geometry_supported=False,
            errors=[],
        )
        self.info.battery = battery.get("percent")
        self.info.geometry_override = bool(self.geometry_override.get("dpi") or self.geometry_override.get("printhead_px"))
        return self.paper

    async def read_paper_info(self, timeout: float = 1.5) -> PaperGeometry:
        """The family does not measure the label; the size comes from the calibration."""
        return PaperGeometry(valid=False, supported=False, raw="luckprinter: no geometry query")

    async def keep_alive(self) -> bool:
        """Poll the status byte: keeps the link from idling out and detects a drop fast."""
        if not self.connected:
            return False
        try:
            reply = await self.link.request(lp.REQUESTS["status"], timeout=2.5)
        except Exception:  # noqa: BLE001 - a dead link raises here
            return False
        alive = getattr(self.link, "alive", self.link.connected)
        if not alive:
            return False
        if reply:
            status = lp.parse_status(reply)
            self.paper.lid_closed = not status.get("cover_open", False)
            self.paper.paper_inserted = not status.get("out_of_paper", False)
            self.paper.raw_heartbeat = f"status={status.get('raw')}"
        # An empty reply is normal when the printer is busy; only the link state decides.
        return True

    # -------------------------------------------------------------- settings
    async def set_density(self, density: int) -> None:
        # The panel speaks Niimbot's 1-5; this family has three steps (0 light … 2 thick).
        level = max(0, min(2, round((max(1, min(5, int(density))) - 1) / 2)))
        await self.link.request(lp.set_density(level), timeout=1.5)
        self.info.density = max(1, min(5, int(density)))

    async def set_paper_type(self, kind: int) -> None:
        """``kind`` is the panel's Niimbot label type (1 = gaps), not the LuckPrinter value."""
        luck_type = lp.paper_type_from_label_type(kind)
        await self.link.request(lp.set_paper_type(luck_type), timeout=1.5)
        self.paper.label_type = int(kind or 1)
        self.paper.label_type_name = lp.PAPER_TYPES.get(luck_type)
        self.paper.label_type_source = "printer"

    async def set_label_type(self, label_type: int) -> None:
        await self.set_paper_type(int(label_type))

    async def calibrate(self) -> None:
        """No calibration command on this family — feed one label instead."""
        await self.link.write(lp.FORM_FEED)
        await asyncio.sleep(0.4)

    async def cancel(self) -> None:
        try:
            await self.link.write(lp.stop_command(self._family))
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("Cancel failed: %s", exc)

    # --------------------------------------------------------------- printing
    async def print_bitmap(
        self,
        encoded,
        copies: int = 1,
        density: int | None = None,
        label_type: int | None = None,
        on_progress=None,  # noqa: ANN001 - same callback as NiimbotPrinter
    ) -> None:
        """Send 1-bit rows to the printer (``encoded`` comes from ``bitmap.encode_image``)."""
        height = int(encoded.rows)
        width_px = int(encoded.cols)
        raw = getattr(encoded, "raw", b"")
        raster = lp.build_raster(raw, width_px, height)
        copies = max(1, int(copies))
        # Always send the paper type: the printer must know how to find the gap, otherwise it
        # prints every label but never advances to the next one (the reported symptom).
        await self.set_paper_type(label_type if label_type is not None else (self.paper.label_type or 1))
        if density is not None:
            level = max(0, min(2, round((max(1, min(5, int(density))) - 1) / 2)))
            await self.link.request(lp.set_density(level), timeout=1.5)

        for page in range(1, copies + 1):
            if on_progress:
                on_progress(0.05, page=page)
            await self.link.write(lp.WAKE)
            await self.link.write(lp.enable_command(self._family))
            await asyncio.sleep(0.05)
            await self.link.write(raster)
            if on_progress:
                on_progress(0.6, page=page)
            await self.link.write(lp.FORM_FEED)
            # The family does not walk to the tear bar by itself: the vendor app sends an
            # explicit advance, so the value is configurable per printer (Settings → Printer).
            await self.feed(getattr(self, "feed_mm", 0.0) or 0.0)
            await self.link.write(lp.stop_command(self._family))
            await self.link.wait_for_finish(timeout=60.0)
            if on_progress:
                on_progress(0.9, page=page)
        if on_progress:
            on_progress(1.0, page=copies)

    async def feed(self, millimetres: float) -> None:
        """Push the paper forward by ``millimetres`` — used to reach the tear bar after a print."""
        dots = int(round(max(0.0, float(millimetres)) * self.info.dpi / 25.4))
        if dots <= 0:
            return
        await self.link.write(lp.feed_forward(dots))

    async def form_feed(self, timeout: float = 8.0) -> bool:
        """Advance to the next label — the printer's gap sensor decides where to stop.

        This is how the gap calibration works on this family: with paper type *gap* the printer
        walks the stock itself until the sensor sees the gap, so the app only has to ask for the
        feed and check that the printer answered and still has paper. ``10 0C`` is used first
        because it answers ``OK`` (proof the seek finished); ``1D 0C`` is the fallback.
        """
        answered = False
        try:
            reply = await self.link.request(lp.FORM_FEED_ACK, timeout=timeout)
            answered = bool(reply)
        except Exception:  # noqa: BLE001 - fall back to the silent variant
            answered = False
        if not answered:
            await self.link.write(lp.FORM_FEED)
            try:
                answered = await self.link.wait_for_finish(timeout=timeout)
            except Exception:  # noqa: BLE001 - a slow feed is not a failure
                answered = False
        await asyncio.sleep(0.4)
        try:
            await self.refresh()
        except Exception:  # noqa: BLE001 - status is best effort
            pass
        return answered or self.paper.paper_inserted is not False

    # ----------------------------------------------------------- convenience
    async def get_battery(self) -> int | None:
        return lp.parse_battery(await self.link.request(lp.REQUESTS["battery"])).get("percent")

    async def set_shutdown_minutes(self, minutes: int) -> None:
        await self.link.request(lp.set_shutdown(minutes), timeout=1.5)
