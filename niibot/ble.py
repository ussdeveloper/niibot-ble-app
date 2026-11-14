"""Bluetooth Low Energy transport for Niimbot printers (via bleak)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner

from .protocol import RESPONSES, Frame, FrameReader, PrinterError, Resp, build_frame

log = logging.getLogger(__name__)

SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHAR_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

#: Advertised-name prefixes used by the B1/B2/D1 families.
NAME_PREFIXES = ("B1", "B2", "D1", "B21", "M2", "N1", "D11", "D110", "B18")

#: Windows only runs one BLE discovery at a time; serialise ours so the UI scan and the
#: auto-connect probe never fight over the radio.
_SCAN_LOCK = asyncio.Lock()


async def _discover(timeout: float = 6.0) -> list[tuple[str, object, object]]:
    """One advertisement scan, as ``[(address, device, advertisement), ...]``."""
    async with _SCAN_LOCK:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [(address, device, adv) for address, (device, adv) in found.items()]


def normalise_device_name(name: str) -> str:
    """Name without the address/serial chunk some printers embed in it.

    ``D11s_0A0B_BLE`` → ``D11S_BLE``: the four hex digits are the tail of the (random) BLE
    address, so the same printer advertises a different name after a power cycle.
    """
    cleaned = []
    for part in str(name or "").strip().upper().split("_"):
        if len(part) >= 4 and all(char in "0123456789ABCDEF" for char in part):
            continue
        cleaned.append(part)
    return "_".join(part for part in cleaned if part)


@dataclass
class DiscoveredDevice:
    address: str
    name: str
    rssi: int | None = None
    service_uuids: list[str] = field(default_factory=list)
    #: True when the advertisement matches the Niimbot printer service or name prefixes.
    niimbot: bool = False

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "rssi": self.rssi,
            "service_uuids": self.service_uuids,
            "niimbot": self.niimbot,
        }


class BleLink:
    """Framed request/response transport over the printer's GATT characteristic."""

    def __init__(self) -> None:
        self._client: BleakClient | None = None
        self._reader = FrameReader()
        self._frames: list[Frame] = []
        self._event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._traffic: list[dict] = []
        self.write_delay = 0.01
        self.max_write = 240
        self.address: str | None = None
        self.name: str | None = None
        self.connected = False
        self.last_error: str | None = None

    # ------------------------------------------------------------------ scan
    @staticmethod
    async def find_by_address(address: str, timeout: float = 6.0) -> bool:
        """True when the remembered device answers a real scan."""
        return await BleLink.find_printer(address, timeout=timeout) is not None

    @staticmethod
    async def find_printer(
        address: str,
        names: tuple[str, ...] = (),
        timeout: float = 6.0,
    ) -> dict | None:
        """Locate a printer by address, falling back to its advertised name.

        ``BleakScanner.find_device_by_address`` is not usable for this on Windows: it hands
        back a cached, nameless ``BLEDevice`` for an address that is not advertising (and
        raises on other attempts), so a printer that is switched off looks reachable. Reachability
        is therefore decided from the same advertisement scan the printer list uses. When the
        address is unknown but exactly one printer with the remembered name is advertising, that
        printer is returned — random BLE addresses change when the printer is power-cycled.
        """
        found = await _discover(timeout)
        wanted = (address or "").strip().lower()
        for addr, device, adv in found:
            if wanted and addr.lower() == wanted:
                return {"address": addr, "name": device.name or adv.local_name or "", "matched": "address"}

        wanted_names = {normalise_device_name(name) for name in names if name}
        wanted_names.discard("")
        if not wanted_names:
            return None
        candidates = [
            {"address": addr, "name": device.name or adv.local_name or "", "matched": "name"}
            for addr, device, adv in found
            if normalise_device_name(device.name or adv.local_name or "") in wanted_names
        ]
        # Two identical printers in range: never guess which one is the remembered unit.
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    async def scan(timeout: float = 6.0, show_all: bool = False) -> list[DiscoveredDevice]:
        found = await _discover(timeout)
        devices: list[DiscoveredDevice] = []
        for address, device, adv in found:
            name = device.name or adv.local_name or ""
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            looks_like_niimbot = SERVICE_UUID in uuids or any(
                name.upper().startswith(prefix.upper()) for prefix in NAME_PREFIXES
            )
            if not looks_like_niimbot and not show_all:
                continue
            devices.append(
                DiscoveredDevice(
                    address=address,
                    name=name or "(no name)",
                    rssi=adv.rssi,
                    service_uuids=uuids,
                    niimbot=looks_like_niimbot,
                )
            )
        # Printers we recognise first, then by signal strength: an unknown label printer
        # (Fichero, Karsten, OEM clones) must still be visible and selectable.
        devices.sort(key=lambda d: (not d.niimbot, d.rssi is None, -(d.rssi or -999)))
        return devices

    # --------------------------------------------------------------- connect
    async def connect(self, address: str, timeout: float = 25.0) -> None:
        await self.disconnect()
        self._loop = asyncio.get_running_loop()
        self._reader.reset()
        self._frames.clear()
        self._event.clear()

        client = BleakClient(address, timeout=timeout, disconnected_callback=self._on_disconnect)
        await client.connect()
        self._client = client
        self.address = address
        self.name = client.name or address
        self.connected = True
        self.last_error = None

        await client.start_notify(CHAR_UUID, self._on_notify)
        log.info("Connected to %s (%s)", self.name, address)

    async def pair(self) -> None:
        if not self._client:
            raise RuntimeError("The printer is not connected")
        await self._client.pair()

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self.connected = False
        if client is None:
            return
        with contextlib.suppress(Exception):
            await client.stop_notify(CHAR_UUID)
        with contextlib.suppress(Exception):
            await client.disconnect()

    def _on_disconnect(self, _client: BleakClient) -> None:
        self.connected = False
        self.last_error = "disconnected from the printer"
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event.set)

    def _on_notify(self, _char, data: bytearray) -> None:
        chunk = bytes(data)
        self._record("rx", chunk)
        for frame in self._reader.feed(chunk):
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._push, frame)

    def _push(self, frame: Frame) -> None:
        self._frames.append(frame)
        self._event.set()

    # ----------------------------------------------------------------- write
    def _chunk_size(self) -> int:
        mtu = 0
        if self._client is not None:
            try:
                mtu = int(getattr(self._client, "mtu_size", 0) or 0)
            except Exception:  # pragma: no cover - backend specific
                mtu = 0
        limit = mtu - 3 if mtu > 23 else self.max_write
        return max(20, min(self.max_write, limit))

    async def _write(self, payload: bytes) -> None:
        if not self._client or not self.connected:
            raise RuntimeError("The printer is not connected")
        chunk_size = self._chunk_size()
        self._record("tx", payload)
        for offset in range(0, len(payload), chunk_size):
            chunk = payload[offset : offset + chunk_size]
            try:
                await self._client.write_gatt_char(CHAR_UUID, chunk, response=False)
            except Exception:
                # A few firmware/OS combinations reject write-without-response.
                await self._client.write_gatt_char(CHAR_UUID, chunk, response=True)
            if self.write_delay:
                await asyncio.sleep(self.write_delay)

    async def write_frames(self, frames: list[bytes]) -> None:
        """Write several frames, bundling them into as few BLE writes as possible."""
        async with self._lock:
            bundle = bytearray()
            limit = self._chunk_size()
            for frame in frames:
                if bundle and len(bundle) + len(frame) > limit:
                    await self._write(bytes(bundle))
                    bundle.clear()
                bundle.extend(frame)
            if bundle:
                await self._write(bytes(bundle))

    # ------------------------------------------------------- request/response
    async def send(
        self,
        cmd: int,
        data: bytes = b"",
        expect: tuple[int, ...] | None = None,
        timeout: float = 1.5,
        wait: bool = True,
    ) -> Frame | None:
        """Send a frame and (optionally) wait for its response."""
        async with self._lock:
            del self._frames[:]
            self._event.clear()
            await self._write(build_frame(cmd, data))
            if not wait:
                return None

            accepted = expect if expect is not None else RESPONSES.get(cmd, ())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                for index, frame in enumerate(self._frames):
                    if frame.cmd == Resp.PRINT_ERROR:
                        del self._frames[: index + 1]
                        raise PrinterError(frame.data[0] if frame.data else 0)
                    # 0x00 = "command not supported" - a real answer, not noise.
                    if frame.cmd == Resp.NOT_SUPPORTED or not accepted or frame.cmd in accepted:
                        del self._frames[: index + 1]
                        return frame
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), remaining)
                except asyncio.TimeoutError:
                    return None

    async def send_raw(self, payload: bytes, timeout: float = 1.5) -> Frame | None:
        """Send raw bytes (used for the connect packet with its 0x03 prefix)."""
        async with self._lock:
            del self._frames[:]
            self._event.clear()
            await self._write(payload)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                for index, frame in enumerate(self._frames):
                    if frame.cmd == Resp.PRINT_ERROR:
                        del self._frames[: index + 1]
                        raise PrinterError(frame.data[0] if frame.data else 0)
                    del self._frames[: index + 1]
                    return frame
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), remaining)
                except asyncio.TimeoutError:
                    return None

    # ------------------------------------------------------------ diagnostics
    def _record(self, direction: str, payload: bytes) -> None:
        self._traffic.append(
            {"t": round(time.time(), 3), "dir": direction, "data": payload.hex()}
        )
        if len(self._traffic) > 600:
            del self._traffic[:200]

    def traffic(self, limit: int = 200) -> list[dict]:
        return self._traffic[-limit:]

    def clear_traffic(self) -> None:
        self._traffic.clear()
