"""Local web application: printer control + label designer."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import platform
import struct
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import ipp as ipp_mod
from . import sheet, spreadsheet, store, zones
from .bitmap import encode_planes
from .ble import BleLink, normalise_device_name
from .diagnostics import diagnose
from .fichero import FicheroPrinter
from .consumables import Consumable, builtin_formats, scan_vendor_catalog
from .fit import FitOptions, fit_to_label
from .models import (
    MODELS,
    PAPER_PRESETS,
    TWO_COLOUR_MODELS,
    PaperProfile,
    Region,
    ZoneShape,
    model_for_id,
    model_for_key,
)
from .paths import resource_dir
from .printer import NiimbotPrinter
from .protocol import LABEL_TYPE_NAMES, PageColor, ProtocolError
from .render import available_fonts, colourise, font_catalog, preview_payload, render_label, separate_inks
from .codes import minimum_size
from .spec import BARCODE_FORMATS, ELEMENT_TYPES, TEMPLATES, Element, LabelSpec, default_spec
from .version import APP_NAME, APP_TAGLINE, __version__

log = logging.getLogger(__name__)
WEB_DIR = resource_dir() / "web"
#: ``time.monotonic()`` when the process started — reported by ``/api/health``.
STARTED_AT = time.monotonic()


def _label_count(count: int) -> str:
    return f"{count} label" if count == 1 else f"{count} labels"


@dataclass
class Job:
    id: str
    state: str = "running"
    progress: float = 0.0
    message: str = "Rendering the label…"
    error: str | None = None
    rows: int = 0
    cols: int = 0
    packets: int = 0
    copies: int = 1
    density: int | None = None
    label_type: int | None = None
    paper_barcode: str | None = None
    sku: str | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    #: What produced the job — ``label``, ``sheet`` or ``windows`` (IPP queue).
    kind: str = "label"
    #: Printer name shown in the queue panel.
    printer: str | None = None
    protocol: str | None = None
    #: JSON of the spec (or the list of specs for a sheet) so the job can be repeated.
    payload: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def detect_protocol(name: str | None) -> str:
    """Pick the protocol from the advertised name; both families are told apart by it."""
    from .fichero import NAME_HINTS

    lower = str(name or "").lower()
    return "luckprinter" if any(hint in lower for hint in NAME_HINTS) else "niimbot"


def _protocol_for(address: str, settings: dict, name_hint: str | None = None) -> str:
    """Which protocol to use for one printer.

    What answered for this address wins — a printer keeps working the way it worked last time.
    Only when the app has never talked to the device does the global choice (Settings → Protocol)
    apply, and only after that the advertised name.
    """
    stored = store.printer_protocol(address)
    if stored in {"niimbot", "luckprinter"}:
        return stored
    value = str(settings.get("printer_protocol") or "auto").lower()
    if value in {"niimbot", "luckprinter"}:
        return value
    return detect_protocol(name_hint or store.printer_name(address) or "")


#: Service UUIDs only label printers advertise (Niimbot's own + the LuckPrinter UART bridges).
PRINTER_SERVICES = (
    "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
    "000018f0-0000-1000-8000-00805f9b34fb",
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "49535343-fe7d-4ae5-8fa9-9fafd205e455",
)


def looks_like_printer(name: str | None, service_uuids=None, niimbot_flag: bool = False) -> bool:
    """True when an advertisement belongs to a label printer we can drive."""
    from .ble import NAME_PREFIXES

    if niimbot_flag:
        return True
    upper = str(name or "").upper()
    hints = [prefix.upper() for prefix in NAME_PREFIXES]
    from .fichero import NAME_HINTS

    hints += [hint.upper() for hint in NAME_HINTS]
    if any(hint in upper for hint in hints):
        return True
    uuids = [str(uuid).lower() for uuid in (service_uuids or [])]
    return any(uuid in PRINTER_SERVICES for uuid in uuids)


def device_scope() -> str:
    """Identity of the printer type — labels, templates and calibrations are kept per scope.

    A B1 and a Fichero have different printheads and label stock, so their libraries are
    separate; two printers of the same model share one library.
    """
    info = state.printer.info
    model_key = (info.model_key or "").strip()
    if model_key and model_key != "UNKNOWN":
        return f"{state.protocol}:{model_key}"
    # Off-line: keep showing the library of the printer that was used last.
    return state.last_scope or f"{state.protocol}:UNKNOWN"


def _apply_geometry_to(driver) -> None:
    """Give one driver the geometry override saved in the settings."""
    settings = store.load_settings()
    geometry = settings.get("device_geometry") or {}
    driver.set_geometry_override(
        geometry.get("dpi"), geometry.get("printhead_px"), geometry.get("print_task")
    )
    if hasattr(driver, "feed_mm"):
        try:
            driver.feed_mm = float(settings.get("feed_mm") or 0.0)
        except (TypeError, ValueError):
            driver.feed_mm = 0.0


def _apply_device_geometry() -> None:
    """Give the active driver the geometry override saved in the settings."""
    _apply_geometry_to(state.printer)


def make_printer(protocol: str):
    """Driver for the configured protocol (Niimbot framing or the LuckPrinter family)."""
    if protocol == "luckprinter":
        return FicheroPrinter()
    return NiimbotPrinter(BleLink())


#: How often one printer is pinged, how long a printer that refused is left alone, and how long
#: the worker waits before looking again at a printer that is not even advertising.
PING_EVERY_S = 4.0
RETRY_S = 60.0
RETRY_LUCKPRINTER_S = 20.0
WAIT_NOT_IN_RANGE_S = 5.0


class PrinterWorker:
    """One printer, one task.

    Every printer owns its driver, its lock, its timer and its reconnect schedule, so a slow ping
    or a failing connect on one printer can no longer starve the link of another. ``wanted`` says
    the app should keep this link; discovery only tells the worker whether the printer is
    advertising, so it never spends a connect timeout on a printer that is switched off.
    """

    def __init__(self, address: str, name: str, protocol: str) -> None:
        self.address = address
        self.name = name or ""
        self.protocol = protocol if protocol in {"niimbot", "luckprinter"} else "niimbot"
        self.printer = make_printer(self.protocol)
        _apply_geometry_to(self.printer)
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.wanted = True
        self.in_range = True
        self.opened_at = 0.0
        self.last_ping: float | None = None
        self.last_error: str | None = None
        self.status = "idle"
        self.reconnects = 0
        self._next_try = 0.0
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------ state
    @property
    def connected(self) -> bool:
        return bool(self.printer.connected)

    @property
    def retry_s(self) -> float:
        return RETRY_LUCKPRINTER_S if self.protocol == "luckprinter" else RETRY_S

    def to_dict(self, active: bool) -> dict:
        info = self.printer.info
        return {
            "address": self.address,
            "name": self.name or info.model_name,
            "protocol": self.protocol,
            "model_name": info.model_name,
            "model_key": info.model_key,
            "dpi": info.dpi,
            "printhead_px": info.printhead_px,
            "connected": self.connected,
            "active": active,
            "in_range": self.in_range,
            "status": self.status,
            "last_error": self.last_error,
            "ping_age_s": (
                round(time.monotonic() - self.last_ping, 1) if self.last_ping is not None else None
            ),
        }

    # -------------------------------------------------------------- lifecycle
    def ensure_task(self) -> None:
        """Start the worker's own loop (idempotent) and wake it so it acts at once."""
        if self.task is not None and not self.task.done():
            self._wake.set()
            return
        self.wanted = True
        self.task = asyncio.create_task(self._run(), name=f"printer:{self.address}")

    def nudge(self, in_range: bool | None = None) -> None:
        """Discovery news: try again right away, or stop trying until it shows up."""
        if in_range is not None:
            self.in_range = in_range
        self._wake.set()

    def set_protocol(self, protocol: str) -> None:
        """Swap the driver family — only while the link is closed."""
        if protocol not in {"niimbot", "luckprinter"} or protocol == self.protocol:
            return
        self.protocol = protocol
        self.printer = make_printer(protocol)
        _apply_geometry_to(self.printer)

    async def stop(self) -> None:
        """Give up the link for good (the user disconnected)."""
        self.wanted = False
        self._wake.set()
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - stopping must not raise
                pass
        await self._disconnect_quietly()
        self.status = "stopped"

    async def connect_once(self) -> bool:
        """One connection attempt, with the other protocol family as a fallback.

        A wrong protocol shows up as a failed handshake, not as a missing device, so the fallback
        is only tried when the printer really answered *something* — trying the other family on a
        printer that is not even there would just double the wait.
        """
        alternative = "luckprinter" if self.protocol == "niimbot" else "niimbot"
        for attempt in (self.protocol, alternative):
            self.set_protocol(attempt)
            self.status = f"connecting ({attempt})"
            try:
                async with self.lock:
                    await self.printer.connect(self.address)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported and retried
                reason = _connect_reason(exc)
                self.last_error = reason
                self.status = "disconnected"
                _PRINTER_ERRORS[self.address.lower()] = reason
                log.warning(
                    "%s: %s failed — %s", self.name or self.address, attempt, reason
                )
                if _device_absent(exc):
                    return False
                continue
            await self._remember_protocol()
            await self._refresh_quietly()
            self.opened_at = time.time()
            self.last_error = None
            self.last_ping = time.monotonic()
            self.status = "connected"
            self._next_try = 0.0
            _clear_connect_failure(self.address)
            log.warning(
                "Connected to %s (%s, %s)", self.name or self.address, self.address, self.protocol
            )
            return True
        return False

    def remember(self, *, retire_stale: str | None = None) -> None:
        """Write this printer into the registry, with the geometry it really reports."""
        info = self.printer.info
        model_key = (info.model_key or "").strip()
        scope = f"{self.protocol}:{model_key}" if model_key and model_key != "UNKNOWN" else None
        if retire_stale:
            # The unit came back under a new BLE address: one row per physical printer.
            try:
                store.forget_printer(retire_stale)
                log.warning("Merged the old address %s into %s", retire_stale, self.address)
            except Exception:  # pragma: no cover - registry is best effort
                pass
        try:
            store.remember_printer({
                "address": self.address,
                "name": self.name or info.model_name or self.address,
                "protocol": self.protocol,
                "model_key": info.model_key,
                "model_name": info.model_name,
                "scope": scope,
            })
            store.set_printer_protocol(self.address, self.protocol)
        except Exception:  # pragma: no cover - registry is best effort
            log.debug("Could not remember %s", self.address)

    # ------------------------------------------------------------------ loops
    async def _run(self) -> None:
        while self.wanted:
            delay = PING_EVERY_S
            try:
                delay = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one printer must never kill its own worker
                log.warning(
                    "%s: worker error: %s", self.name or self.address, _describe_error(exc)
                )
                self.last_error = _describe_error(exc)
                delay = 2.0
            await self._sleep(delay)

    async def _tick(self) -> float:
        """One step of the worker; returns how long to wait before the next one."""
        if self.connected:
            if self._job_owns_link():
                # A running job is its own keep-alive.
                return 1.0
            if await self._ping_once():
                return PING_EVERY_S
            await self._drop("keep-alive lost")
            self._schedule_retry()
            return 1.0
        now = time.monotonic()
        if now < self._next_try:
            return min(PING_EVERY_S, max(0.5, self._next_try - now))
        if not self.in_range:
            self.status = "not in range"
            return WAIT_NOT_IN_RANGE_S
        if await self.connect_once():
            await _retire_duplicate_rows()
            _maybe_activate(self)
            return PING_EVERY_S
        return 1.0

    def _job_owns_link(self) -> bool:
        return self.printer is state.printer and bool(state.job and state.job.state == "running")

    async def _ping_once(self) -> bool:
        keep_alive = getattr(self.printer, "keep_alive", None)
        if keep_alive is None:
            return self.connected
        try:
            async with self.lock:
                alive = bool(await keep_alive())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed ping means a dead link
            alive = False
        if alive:
            self.last_ping = time.monotonic()
            self.status = "connected"
            return True
        return False

    async def _drop(self, reason: str) -> None:
        log.warning("Keep-alive lost %s (%s)", self.name or self.address, reason)
        self.status = "dropped"
        self.reconnects += 1
        await self._disconnect_quietly()

    def _schedule_retry(self) -> None:
        self._next_try = time.monotonic() + self.retry_s

    async def _disconnect_quietly(self) -> None:
        try:
            async with self.lock:
                await self.printer.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - best effort
            pass

    async def _refresh_quietly(self) -> None:
        try:
            async with self.lock:
                await self.printer.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - status is best effort
            pass

    async def _remember_protocol(self) -> None:
        """Persist the family that answered, per printer (the stored one may be a guess)."""
        if store.printer_protocol(self.address) == self.protocol:
            return
        try:
            store.set_printer_protocol(self.address, self.protocol)
        except Exception:  # pragma: no cover - registry is best effort
            log.debug("Could not store the protocol for %s", self.address)

    async def _sleep(self, delay: float) -> None:
        """Sleep, but wake up at once on a nudge or a cancel."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, delay))
        except asyncio.TimeoutError:
            pass


class AppState:
    def __init__(self) -> None:
        self.protocol = "niimbot"
        self.printer = NiimbotPrinter(BleLink())
        #: Used only while no printer is active; every printer has its own lock.
        self._idle_lock = asyncio.Lock()
        #: Guards connection bookkeeping that spans printers (kept for the settings endpoints).
        self.connect_lock = asyncio.Lock()
        self.job: Job | None = None
        self.job_task: asyncio.Task | None = None
        self.auto_connect_task: asyncio.Task | None = None
        self.auto_status: str = "idle"
        self.last_scan: list[dict] = []
        self.keepalive_task: asyncio.Task | None = None
        self.reconnects: int = 0
        #: True while the user expects a printer to be connected (cleared by /api/disconnect).
        self.reconnect_wanted: bool = False
        #: Kept for the endpoints that used to wait for the watchdog.
        self.reconnecting: bool = False
        #: Scope of the printer used last, kept while nothing is connected.
        self.last_scope: str = ""
        #: Every printer the app keeps a link to, keyed by lower-cased address.
        self.connections: dict[str, PrinterWorker] = {}
        #: Key of the worker that prints.
        self.printer_key: str | None = None
        #: Keep every known printer that is in range connected while it is switched on.
        self.maintain_links: bool = True

    @property
    def connected(self) -> bool:
        return self.printer.connected

    @property
    def active_worker(self) -> "PrinterWorker | None":
        if self.printer_key is None:
            return None
        worker = self.connections.get(self.printer_key)
        return worker if worker is not None and worker.printer is self.printer else None

    @property
    def lock(self) -> asyncio.Lock:
        """Serialises work on the active printer — every other printer has its own lock."""
        worker = self.active_worker
        return worker.lock if worker is not None else self._idle_lock

    @property
    def last_ping(self) -> float | None:
        worker = self.active_worker
        return worker.last_ping if worker is not None else None

    def use_protocol(self, protocol: str) -> None:
        """Swap the idle driver before connecting; keeps the object identity that handlers use."""
        protocol = protocol if protocol in {"niimbot", "luckprinter"} else "niimbot"
        if protocol == self.protocol and self.printer is not None:
            return
        self.protocol = protocol
        self.printer = make_printer(protocol)


state = AppState()


async def _shutdown() -> None:
    if state.job_task and not state.job_task.done():
        state.job_task.cancel()
    for worker in list(state.connections.values()):
        try:
            await worker.stop()
        except Exception:  # pragma: no cover - best effort
            pass
    state.connections.clear()
    try:
        await state.printer.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.ensure_dirs()
    state.last_scope = str(store.load_settings().get("device_scope") or "")
    log.info("%s %s - data folder: %s", APP_NAME, __version__, store.DATA_DIR)
    # Built-in formats make the picker usable without the vendor app installed.
    for entry in builtin_formats():
        store.upsert_consumable(entry)
    # Then fold in whatever the vendor app already cached on this machine.
    try:
        entries, _files = scan_vendor_catalog()
        for consumable in entries.values():
            store.upsert_consumable(consumable.to_dict())
        if entries:
            log.info("Imported %d formats from the NIIMBOT app", len(entries))
    except Exception as exc:  # pragma: no cover - optional convenience
        log.debug("Vendor catalogue import failed: %s", exc)
    # One discovery loop looks for printers and hands each one to its own worker: connecting,
    # pinging and reconnecting then happen per printer, on that printer's own clock.
    _migrate_protocol_setting()
    keepalive_task = asyncio.create_task(_discovery_loop())
    state.keepalive_task = keepalive_task
    yield
    keepalive_task.cancel()
    await _shutdown()


def _migrate_protocol_setting() -> None:
    """The protocol now belongs to each printer, not to the app.

    Older builds wrote the protocol of the last connected printer into the global setting, which
    then made every *other* printer connect with the wrong family first. That value was never a
    user choice, so drop it once and let the per-printer record decide.
    """
    settings = store.load_settings()
    if str(settings.get("protocol_per_device") or "") == "1":
        return
    changed: dict[str, str] = {"protocol_per_device": "1"}
    if str(settings.get("printer_protocol") or "auto").lower() in {"niimbot", "luckprinter"}:
        changed["printer_protocol"] = "auto"
    store.save_settings(changed)


app = FastAPI(title="Niibot Studio", lifespan=lifespan)


# ---------------------------------------------------------------- models API
class ScanRequest(BaseModel):
    timeout: float = 6.0
    show_all: bool = False


class ConnectRequest(BaseModel):
    address: str
    protocol: str | None = None


class DiagnoseRequest(BaseModel):
    address: str
    seconds: float = 6.0


class MirrorRequest(BaseModel):
    """Fill regions 2..N with a copy (or vertical mirror) of region 1.

    ``cells`` (the roll calibrator's cells) wins over ``regions`` when given: the first
    cell is the editable one and every other cell receives a copy of its content.
    """

    regions: int = 1
    flip: bool = False
    cells: list[RegionRequest] | None = None


class PreviewRequest(BaseModel):
    spec: LabelSpec
    mirror: MirrorRequest | None = None


class PrintRequest(BaseModel):
    spec: LabelSpec
    copies: int | None = None
    density: int | None = None
    label_type: int | None = None
    mirror: MirrorRequest | None = None


class SheetPrintRequest(BaseModel):
    spec: LabelSpec
    rows: list[dict[str, str]] = Field(default_factory=list)
    include_current: bool = True
    copies: int | None = None
    density: int | None = None
    label_type: int | None = None
    mirror: MirrorRequest | None = None


class ElementPreviewRequest(BaseModel):
    spec: LabelSpec
    element_id: str


class SpecPreviewRequest(BaseModel):
    spec: LabelSpec
    max_width: int = 320


class CodeSizeRequest(BaseModel):
    """How small a QR/barcode may get on the attached printer before it stops scanning."""

    kind: str = "qr"
    text: str = ""
    ec_level: str = "M"
    barcode_format: str = "code128"


class SaveLabelRequest(BaseModel):
    id: str | None = None
    spec: LabelSpec


class SaveTemplateRequest(BaseModel):
    id: str | None = None
    name: str
    spec: LabelSpec


class ShapeRequest(BaseModel):
    """One outline inside an item zone."""

    kind: str = "rect"
    x: float = Field(default=0.0, ge=-50, le=400)
    y: float = Field(default=0.0, ge=-50, le=400)
    w: float = Field(default=30.0, gt=0, le=400)
    h: float = Field(default=15.0, gt=0, le=400)


class RegionRequest(BaseModel):
    """One printable item zone drawn in the calibrator (millimetres from the page corner).

    ``shapes`` are the zone's outlines; when it is empty the single ``shape`` box is used.
    """

    shape: str = "rect"
    x: float = Field(default=0.0, ge=-50, le=400)
    y: float = Field(default=0.0, ge=-50, le=400)
    w: float = Field(default=30.0, gt=0, le=400)
    h: float = Field(default=15.0, gt=0, le=400)
    label: str = ""
    angle: float = 0.0
    thickness_mm: float = Field(default=0.8, gt=0, le=10)
    shapes: list[ShapeRequest] = Field(default_factory=list)


class ZoneOutlineRequest(BaseModel):
    """Ask the server to draw (or print) the zone borders on the physical label."""

    width_mm: float = Field(gt=0, le=200)
    height_mm: float = Field(gt=0, le=400)
    regions: list[RegionRequest] = Field(default_factory=list)
    stroke_mm: float = Field(default=0.3, gt=0, le=2)
    copies: int = Field(default=1, ge=1, le=20)
    density: int | None = None
    label_type: int | None = None


class ProfileRequest(BaseModel):
    key: str
    name: str = ""
    width_mm: float = Field(gt=0, le=200)
    height_mm: float = Field(gt=0, le=400)
    gap_mm: float = 2.0
    label_type: int = 1
    capacity: int | None = None
    regions: list[RegionRequest] = Field(default_factory=list)


class RollFormatRequest(BaseModel):
    """A roll whose format the printer cannot report and the catalogue does not know."""

    barcode: str
    name: str = ""
    width_mm: float = Field(gt=0, le=200)
    height_mm: float = Field(gt=0, le=400)
    rows: int = Field(default=1, ge=1, le=8)
    cols: int = Field(default=1, ge=1, le=8)
    gap_mm: float = Field(default=0.0, ge=0, le=20)
    label_type: int = 1
    capacity: int | None = None
    regions: list[RegionRequest] = Field(default_factory=list)
    #: Size of one sticker on the roll; ``width_mm``/``height_mm`` stay the print page.
    sticker_w_mm: float = Field(default=0.0, ge=0, le=200)
    sticker_h_mm: float = Field(default=0.0, ge=0, le=400)
    row_space_mm: float = Field(default=0.0, ge=0, le=50)
    col_space_mm: float = Field(default=0.0, ge=0, le=50)


class DensityRequest(BaseModel):
    density: int


class FitRequest(BaseModel):
    mode: str = "contain"
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    rotation: int = 0
    scale: float = 1.0
    margin_mm: float = 0.0
    threshold: int = 128
    dither: bool = False
    invert: bool = False


# --------------------------------------------------------------------- state
def _decorate_profile(profile: dict, key: str, barcode: str | None = None) -> dict:
    """Add the fields the UI reads (key, grid, sticker size) to a stored calibration."""
    profile = dict(profile)
    profile["key"] = key
    # The grid is stored with the calibration; older records fall back to the catalogue entry.
    entry = store.get_catalog_entry(barcode) or {}
    cols = max(1, int(profile.get("cols") or entry.get("cols") or 1))
    rows = max(1, int(profile.get("rows") or entry.get("rows") or 1))
    profile["rows"], profile["cols"] = rows, cols
    # A profile saved before stickers were tracked: derive the sticker from the grid.
    if not profile.get("sticker_w_mm"):
        profile["sticker_w_mm"] = round(float(profile["width_mm"]) / cols, 3)
    if not profile.get("sticker_h_mm"):
        profile["sticker_h_mm"] = round(float(profile["height_mm"]) / rows, 3)
    return profile


def _current_profile() -> dict | None:
    paper = state.printer.paper
    key = store.profile_key(paper.barcode, paper.uuid, device_scope())
    if not key:
        return None
    profile = store.get_profile(key)
    if not profile:
        return None
    return _decorate_profile(profile, key, paper.barcode)



def _state_payload() -> dict:
    settings = store.load_settings()
    paper = state.printer.paper.to_dict()
    # The printer reports the format only on newer firmware; for the B1 the format
    # comes from the vendor app's own cache, keyed by the roll's product barcode.
    catalog_entry = store.get_catalog_entry(paper.get("barcode"))
    if catalog_entry:
        paper["catalog"] = catalog_entry
        if not paper.get("detected_width_mm") and catalog_entry.get("width_mm"):
            paper["detected_width_mm"] = catalog_entry["width_mm"]
            paper["detected_height_mm"] = catalog_entry["height_mm"]
            paper["detected_label_type"] = catalog_entry.get("paper_type")
            paper["geometry_source"] = "catalog"
        elif paper.get("detected_width_mm"):
            paper["geometry_source"] = "printer"
    info = asdict(state.printer.info)
    if not state.connected:
        # Off-line show the printer that will be used next (the saved one), not whatever
        # driver handed over the last connection.
        remembered = _last_printer_row()
        if remembered:
            info["model_name"] = remembered.get("model_name") or info.get("model_name")
            info["model_key"] = remembered.get("model_key") or info.get("model_key")
            offline_protocol = remembered.get("protocol") or state.protocol
        else:
            offline_protocol = state.protocol
    else:
        offline_protocol = state.protocol
    # One source of truth for the geometry: the payload must report the same head the renderer uses.
    info["dpi"], info["printhead_px"] = _geometry()
    return {
        "connected": state.connected,
        "link": {
            "address": state.printer.link.address if state.connected else settings.get("last_address"),
            "name": state.printer.link.name if state.connected else info.get("model_name"),
        },
        "info": info,
        "paper": paper,
        "profile": _current_profile(),
        "job": state.job.to_dict() if state.job else None,
        "protocol": offline_protocol,
        "protocol_name": "LuckPrinter (Fichero / AiYin)" if offline_protocol == "luckprinter" else "Niimbot",
        "device_scope": device_scope(),
        "keep_alive": {
            "ping_age_s": (
                round(time.monotonic() - state.last_ping, 1) if state.last_ping is not None else None
            ),
            "reconnects": state.reconnects + sum(
                worker.reconnects for worker in state.connections.values()
            ),
            # One line per printer: each link is watched by its own worker.
            "links": {worker.address: worker.status for worker in state.connections.values()},
        },
        "last_address": settings.get("last_address"),
        "last_name": settings.get("last_name"),
        "connections": [
            worker.to_dict(active=worker.printer is state.printer)
            for worker in state.connections.values()
        ],
        "auto_connect": {
            "enabled": str(settings.get("auto_connect", "1")) not in {"0", "false", "False", ""},
            # Live count: a worker may have brought its link up since the last discovery pass.
            "status": _link_summary() if state.connections else getattr(state, "auto_status", "idle"),
        },
        "last_error": state.printer.last_error or state.printer.link.last_error,
    }


@app.get("/api/state")
async def api_state() -> dict:
    return _state_payload()


@app.get("/api/health")
async def api_health() -> dict:
    """Cheap liveness probe for the Ready/Not Ready chip — no database, no Bluetooth."""
    return {
        "ready": True,
        "version": __version__,
        "uptime_s": round(time.monotonic() - STARTED_AT, 1),
        "connected": state.connected,
        "protocol": state.protocol,
        "auto_connect": _link_summary() if state.connections else getattr(state, "auto_status", "idle"),
    }


@app.get("/api/app-info")
async def api_app_info() -> dict:
    changelog = resource_dir() / "CHANGELOG.md"
    text = ""
    if changelog.is_file():
        try:
            text = changelog.read_text(encoding="utf-8")
        except Exception:  # pragma: no cover - never break the UI on docs
            text = ""
    return {
        "name": APP_NAME,
        "version": __version__,
        "tagline": APP_TAGLINE,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "changelog": text,
    }


@app.get("/api/fonts")
async def api_fonts() -> dict:
    return {"families": font_catalog()}


@app.get("/api/options")
async def api_options() -> dict:
    return {
        "fonts": available_fonts(),
        "font_catalog": font_catalog(),
        "barcode_formats": list(BARCODE_FORMATS),
        "element_types": list(ELEMENT_TYPES),
        "label_types": [{"value": key, "name": value} for key, value in sorted(LABEL_TYPE_NAMES.items())],
        "paper_presets": [vars(preset) for preset in PAPER_PRESETS],
        "templates": [{"id": key, "name": spec.name, "spec": spec.model_dump()} for key, spec in TEMPLATES.items()],
        "default_spec": default_spec().model_dump(),
        "labels": store.list_labels(device_scope()),
        "profiles": list(store.list_profiles(device_scope()).values()),
        "catalog": store.list_consumables(),
        "models": [
            {
                "key": model.key,
                "name": model.name,
                "dpi": model.dpi,
                "printhead_px": model.printhead_px,
                "print_task": model.print_task,
                "printhead_mm": model.printhead_mm,
            }
            for model in MODELS
        ],
    }


# ------------------------------------------------------------------ devices
#: How often the remembered printer is looked for while it is missing, and how often once the
#: radio has been quiet for a while (scanning is cheap but keeps the adapter busy).
AUTO_CONNECT_FAST_S = 8.0
AUTO_CONNECT_SLOW_S = 18.0
AUTO_CONNECT_FAST_TRIES = 8


def _remembered_printer() -> tuple[str, str, tuple[str, ...]]:
    """(address, protocol, names) of the printer the app should reconnect to."""
    settings = store.load_settings()
    address = str(settings.get("last_address") or "").strip()
    names = [str(settings.get("last_name") or "").strip()]
    for row in store.list_printers():
        if row["address"].lower() == address.lower():
            names.append(str(row.get("name") or "").strip())
    return address, _protocol_for(address, settings), tuple(name for name in names if name)


def _describe_error(exc: BaseException) -> str:
    """Readable error text — ``TimeoutError`` and friends carry no message at all."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


#: Why the last attempt for an address failed, in words the panel can show.
_PRINTER_ERRORS: dict[str, str] = {}


def _connect_reason(exc: BaseException) -> str:
    """Turn a bleak failure into something a user can act on."""
    text = f"{type(exc).__name__}: {exc}"
    lower = text.lower()
    if "not found" in lower or "devicenotfound" in lower:
        return "not advertising — switched off, asleep, or connected to another device"
    if isinstance(exc, TimeoutError) or "timeout" in lower:
        return "no answer — switched off, out of range, or holding another connection"
    if "disconnect" in lower:
        return "dropped the link right after connecting"
    return _describe_error(exc)


def _clear_connect_failure(address: str) -> None:
    _PRINTER_ERRORS.pop(address.lower(), None)


def _device_name(device: dict) -> str:
    name = str(device.get("name") or "").strip()
    return "" if name in {"", "(no name)"} else name


def _known_printers() -> list[dict]:
    """Printers this app has talked to before — each one already owns a library."""
    return [row for row in store.list_printers() if row.get("address")]


def _match_device(
    row: dict, devices: list[dict], name_counts: dict[str, int] | None = None
) -> dict | None:
    """Which scanned device is this known printer? Exact address first, then the name.

    Only named advertisements count for a name match: a nameless entry is what Windows' cache
    hands back for a device that is not really there. A name two known printers share (several
    units of one model) never matches — connecting the wrong unit would be worse than waiting.
    """
    address = str(row.get("address") or "").lower()
    for device in devices:
        if str(device.get("address", "")).lower() == address:
            return device
    normalised = normalise_device_name(str(row.get("name") or ""))
    if not normalised:
        return None
    if name_counts is not None and name_counts.get(normalised, 0) > 1:
        return None
    for device in devices:
        if normalise_device_name(_device_name(device)) == normalised:
            return device
    return None


def _device_absent(exc: BaseException) -> bool:
    """True when the printer is not even advertising — no protocol can help there."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return "not found" in text or "devicenotfound" in text


def _worker_for(address: str, name: str = "", protocol: str = "") -> PrinterWorker:
    """The worker for one address, created with the protocol that address should use."""
    key = address.lower()
    worker = state.connections.get(key)
    if worker is None:
        if not protocol:
            protocol = _protocol_for(address, store.load_settings(), name)
        worker = PrinterWorker(address, name, protocol)
        state.connections[key] = worker
        return worker
    if name:
        worker.name = name
    if protocol and protocol != worker.protocol and not worker.connected:
        worker.set_protocol(protocol)
    return worker


def _maybe_activate(worker: PrinterWorker) -> None:
    """The first link that comes up starts printing — unless the remembered one is on its way."""
    if state.connected:
        return
    remembered, _protocol, _names = _remembered_printer()
    key = remembered.lower()
    if key and key != worker.address.lower():
        preferred = state.connections.get(key)
        if preferred is not None and preferred.wanted and (preferred.connected or preferred.in_range):
            return
    _activate_connection(worker)


def _activate_connection(worker: PrinterWorker, *, persist: bool = True) -> None:
    """Make one of the open links the one that prints. Book-keeping only — no Bluetooth."""
    state.printer = worker.printer
    state.protocol = worker.protocol
    state.printer_key = worker.address.lower()
    state.reconnect_wanted = True
    state.last_scope = device_scope()
    _apply_device_geometry()
    if persist:
        store.save_settings({
            "device_scope": state.last_scope,
            "last_address": worker.address,
            "last_name": worker.name or worker.address,
        })
        if state.protocol == "niimbot":
            store.claim_device_data(state.last_scope)


def _link_summary() -> str:
    connected = [worker for worker in state.connections.values() if worker.connected]
    if connected:
        return f"{len(connected)} printer(s) connected"
    if any(worker.in_range and worker.wanted for worker in state.connections.values()):
        return "connecting…"
    return "no printer in range"


def _known_name_counts() -> dict[str, int]:
    """How many *different* advertised names collapse to the same normalised one.

    More than one means a name is ambiguous (several units of one model, each with its own random
    tail), so matching a known printer by that name could grab the wrong unit. Two rows with the
    very same name are one printer seen twice — that match stays allowed.
    """
    raw: dict[str, set[str]] = {}
    for row in _known_printers():
        name = str(row.get("name") or "")
        normalised = normalise_device_name(name)
        if normalised:
            raw.setdefault(normalised, set()).add(name.lower())
    return {key: len(names) for key, names in raw.items()}


def _dedupe_rows(rows: list[dict], devices: list[dict]) -> tuple[list[dict], list[str]]:
    """One row per physical printer (the advertised name carries the serial).

    A printer that came back under a new BLE address leaves its old row behind, and Windows keeps
    advertising that old address from its cache — two workers then fight over one printer and
    neither link survives. Returns the rows to keep and the addresses to forget.
    """
    live = {str(device.get("address", "")).lower() for device in devices}
    chosen: dict[str, dict] = {}
    keep: list[dict] = []
    doomed: list[str] = []

    def rank(row: dict) -> tuple[bool, float]:
        return (str(row.get("address", "")).lower() in live, float(row.get("last_seen") or 0))

    for row in rows:
        name = str(row.get("name") or "").strip().lower()
        if not name:
            keep.append(row)
            continue
        current = chosen.get(name)
        if current is None:
            chosen[name] = row
            keep.append(row)
            continue
        if rank(row) > rank(current):
            keep.remove(current)
            keep.append(row)
            chosen[name] = row
            doomed.append(str(current["address"]))
        else:
            doomed.append(str(row["address"]))
    return keep, doomed


async def _retire_duplicate_rows() -> None:
    """One registry row per physical printer.

    The advertised name carries the serial, so a second row with the same name is the same unit
    under an address it no longer uses — Windows even keeps advertising that old address from its
    cache, which used to make the app open a second BLE link to one printer.
    """
    for worker in list(state.connections.values()):
        if not worker.connected:
            continue
        name = worker.name.strip().lower()
        if not name:
            continue
        for row in store.list_printers():
            address = str(row.get("address") or "")
            if not address or address.lower() == worker.address.lower():
                continue
            if str(row.get("name") or "").strip().lower() != name:
                continue
            try:
                store.forget_printer(address)
            except Exception:  # pragma: no cover - registry is best effort
                pass
            duplicate = state.connections.pop(address.lower(), None)
            if duplicate is not None:
                await duplicate.stop()
            log.warning(
                "Merged the stale entry %s into %s (%s)", address, worker.address, worker.name
            )


async def _ensure_connections(devices: list[dict] | None = None) -> int:
    """Hand every known printer to its own worker and tell it who is advertising.

    One pass may see two registry rows for the same physical unit (a printer that came back under
    a new BLE address); the device address is therefore handled once, and the stale row is merged
    away as soon as that unit is really connected.
    """
    if devices is None:
        try:
            devices = [device.to_dict() for device in await BleLink.scan(timeout=5.0, show_all=True)]
        except Exception as exc:  # noqa: BLE001 - a failed scan is not fatal
            state.auto_status = f"scan failed: {_describe_error(exc)}"
            devices = []
        else:
            state.last_scan = devices
    name_counts = _known_name_counts()
    rows, doomed = _dedupe_rows(_known_printers(), devices)
    reachable: set[str] = set()
    stale_rows: dict[str, list[str]] = {}
    for row in rows:
        device = _match_device(row, devices, name_counts) if devices else None
        address = str(device.get("address") or row["address"]) if device else row["address"]
        name = (_device_name(device) if device else "") or str(row.get("name") or "")
        protocol = row.get("protocol") or _protocol_for(address, store.load_settings(), name)
        worker = _worker_for(address, name, protocol)
        key = worker.address.lower()
        if device is not None and _device_name(device):
            reachable.add(key)
            worker.nudge(in_range=True)
            if str(row["address"]).lower() != key:
                stale_rows.setdefault(key, []).append(row["address"])
        else:
            worker.nudge(in_range=False)
        if worker.wanted:
            worker.ensure_task()
    for key, worker in list(state.connections.items()):
        if key not in reachable and not worker.connected:
            worker.nudge(in_range=False)
    for address in doomed:
        # Same unit, address it no longer uses: drop the row and any worker that tried it.
        try:
            store.forget_printer(address)
        except Exception:  # pragma: no cover - registry is best effort
            pass
        duplicate = state.connections.pop(address.lower(), None)
        if duplicate is not None:
            await duplicate.stop()
        log.warning("Merged the stale entry %s", address)
    # A printer whose advertisement was seen under a new address owns that address now: one row
    # per physical unit, so the panel cannot offer a link that can never come up.
    for key, stale in stale_rows.items():
        worker = state.connections.get(key)
        if worker is not None and worker.connected:
            for address in stale:
                worker.remember(retire_stale=address)
    await _retire_duplicate_rows()
    connected = [worker for worker in state.connections.values() if worker.connected]
    if connected and not state.connected:
        # The active printer is gone but another link is up: print on that one instead of
        # reporting "not connected" while a printer is perfectly reachable.
        address, _protocol, _names = _remembered_printer()
        preferred = state.connections.get(address.lower())
        pick = preferred if preferred is not None and preferred.connected else connected[0]
        _activate_connection(pick)
    state.auto_status = _link_summary()
    return len(connected)


async def _await_links(timeout: float = 12.0) -> int:
    """Give the workers a moment to bring their links up (used by the endpoints that report)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connected = [worker for worker in state.connections.values() if worker.connected]
        if connected:
            return len(connected)
        if not any(worker.wanted for worker in state.connections.values()):
            return 0
        await asyncio.sleep(0.2)
    return len([worker for worker in state.connections.values() if worker.connected])


async def _auto_connect_once() -> tuple[bool, str]:
    """Open links to every known printer in range. Used at start-up and after a scan."""
    if getattr(state, "auto_busy", False):
        return state.connected, "already trying"
    state.auto_busy = True
    try:
        await _ensure_connections()
        count = await _await_links()
    finally:
        state.auto_busy = False
    if count and state.connected:
        return True, f"{count} printer(s) connected"
    return (
        state.connected,
        state.auto_status if state.connected else f"{_remembered_printer()[0] or 'no printer'} is not in range",
    )


MANAGER_SCAN_S = 15.0


async def _discovery_loop() -> None:
    """Discovery only: who is advertising? Connecting, pinging and retrying belong to the workers."""
    await asyncio.sleep(3.0)
    while True:
        try:
            job_running = bool(state.job and state.job.state == "running")
            if state.maintain_links and not job_running:
                await _ensure_connections()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("Discovery error: %s", exc)
        await asyncio.sleep(MANAGER_SCAN_S)


@app.post("/api/keepalive/drop")
async def api_keepalive_drop() -> dict:
    """Test hook: close the BLE link the way a printer does when it idles out.

    The active printer's worker notices the missing link and reconnects on its own.
    """
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    worker = state.active_worker
    try:
        async with state.lock:
            await state.printer.disconnect()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if worker is not None:
        worker.status = "dropped"
        worker.reconnects += 1
    return {"dropped": True, "keep_alive": {"reconnects": state.reconnects}}


@app.post("/api/auto-connect")
async def api_auto_connect() -> dict:
    """Connect every known printer in range — called at start-up and after a scan."""
    if not _known_printers():
        raise HTTPException(status_code=409, detail="No printer has been paired yet.")
    if state.reconnecting:
        await asyncio.sleep(1.5)
        if state.connected:
            return _state_payload()
    connected, status = await _auto_connect_once()
    if not connected:
        raise HTTPException(status_code=404, detail=f"Printer not reachable: {status}")
    return _state_payload()


class ActivateRequest(BaseModel):
    address: str


@app.post("/api/activate")
async def api_activate(request: ActivateRequest) -> dict:
    """Switch the printer that prints. The link is already open, so this is instant."""
    key = request.address.strip().lower()
    worker = state.connections.get(key)
    if worker is None or not worker.connected:
        state.connections.pop(key, None)
        raise HTTPException(status_code=409, detail="That printer is not connected.")
    _activate_connection(worker)
    try:
        async with worker.lock:
            await worker.printer.refresh()
    except Exception:  # pragma: no cover - status is best effort
        pass
    return _state_payload()


@app.get("/api/connections")
async def api_connections() -> dict:
    return {
        "connections": [
            worker.to_dict(active=worker.printer is state.printer)
            for worker in state.connections.values()
        ],
        "errors": dict(_PRINTER_ERRORS),
        "maintain": state.maintain_links,
    }


@app.get("/api/settings/auto-connect")
async def api_auto_connect_state() -> dict:
    settings = store.load_settings()
    return {
        "address": settings.get("last_address"),
        "enabled": str(settings.get("auto_connect", "1")) not in {"0", "false", "False", ""},
        "status": getattr(state, "auto_status", "idle"),
    }


@app.post("/api/settings/auto-connect")
async def api_auto_connect_set(request: Request) -> dict:
    payload = await request.json()
    store.save_settings({"auto_connect": "1" if payload.get("enabled", True) else "0"})
    return {"enabled": bool(payload.get("enabled", True))}


# ------------------------------------------------------------------ UI state
# The browser keeps nothing: drafts, panel sizes, view toggles, density, language and the
# last calibration live in the ui_state table, so the EXE, a second window and a reload
# all start from the same picture.
@app.get("/api/ui")
async def api_ui_state() -> dict:
    return {"keys": store.load_ui_state(), "claimed_scope": store.claimed_scope()}


@app.post("/api/ui")
async def api_ui_state_set(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON object.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    try:
        store.save_ui_state(payload)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"keys": store.load_ui_state(), "claimed_scope": store.claimed_scope()}


@app.get("/api/settings/device")
async def api_device_settings() -> dict:
    geometry = store.load_settings().get("device_geometry") or {}
    info = state.printer.info
    return {
        # The setting, not the live driver: "auto" keeps the select on Automatic.
        "protocol": str(store.load_settings().get("printer_protocol") or "auto"),
        "active_protocol": state.protocol,
        "feed_mm": float(store.load_settings().get("feed_mm") or 0.0),
        "override": {
            "dpi": int(geometry.get("dpi") or 0),
            "printhead_px": int(geometry.get("printhead_px") or 0),
            "print_task": geometry.get("print_task") or "",
        },
        "detected": {
            "model": info.model_name,
            "model_id": info.model_id,
            "dpi": info.dpi,
            "printhead_px": info.printhead_px,
            "print_task": info.print_task,
            "overridden": info.geometry_override,
            "known": info.model_key != "UNKNOWN",
        },
    }


@app.post("/api/settings/device")
async def api_device_settings_set(request: Request) -> dict:
    payload = await request.json()
    dpi = int(payload.get("dpi") or 0)
    printhead_px = int(payload.get("printhead_px") or 0)
    print_task = str(payload.get("print_task") or "").strip().lower()
    if dpi and not 100 <= dpi <= 1200:
        raise HTTPException(status_code=400, detail="DPI must be between 100 and 1200.")
    if printhead_px and not 32 <= printhead_px <= 2048:
        raise HTTPException(status_code=400, detail="Printhead width must be between 32 and 2048 px.")
    if print_task and print_task not in {"b1", "v4"}:
        raise HTTPException(status_code=400, detail="Unknown print task.")
    feed_mm = payload.get("feed_mm")
    if feed_mm is not None:
        try:
            feed_value = max(0.0, min(50.0, float(feed_mm)))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Feed must be a number of millimetres.")
        store.save_settings({"feed_mm": feed_value})
    protocol = str(payload.get("protocol") or "").strip().lower()
    if protocol and protocol not in {"niimbot", "luckprinter"}:
        raise HTTPException(status_code=400, detail="Unknown printer protocol.")
    store.save_settings(
        {"device_geometry": {"dpi": dpi, "printhead_px": printhead_px, "print_task": print_task}}
    )
    if protocol:
        store.save_settings({"printer_protocol": protocol})
    async with state.lock:
        if protocol and not state.connected:
            state.use_protocol(protocol)
        _apply_device_geometry()
        if state.connected:
            # Re-apply immediately so the canvas and printing pick the new geometry up.
            state.printer.refresh_geometry()
    return await api_device_settings()


# ------------------------------------------------------------------ devices
@app.post("/api/scan")
async def api_scan(request: ScanRequest) -> dict:
    try:
        devices = await BleLink.scan(timeout=max(2.0, min(30.0, request.timeout)), show_all=request.show_all)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc
    state.last_scan = [device.to_dict() for device in devices]
    # A scan that sees the remembered printer is as good a trigger as the timer: connect now
    # instead of waiting for the next retry. Nothing is awaited here — the scan response must
    # not wait for a BLE connect.
    _connect_if_remembered_seen(state.last_scan)
    return {"devices": state.last_scan}


def _connect_if_remembered_seen(devices: list[dict]) -> None:
    """A scan that sees a printer we know is a good moment to connect it — no waiting for the timer."""
    if state.reconnecting:
        return
    known = _known_printers()
    if not known:
        return
    name_counts = _known_name_counts()
    seen = any(
        _device_name(match) for row in known if (match := _match_device(row, devices, name_counts))
    )
    if not seen:
        return
    # Connect in the background: the scan response must not wait for a BLE connect.
    asyncio.create_task(_auto_connect_once())


@app.post("/api/device/diagnose")
async def api_device_diagnose(request: DiagnoseRequest) -> dict:
    """Identify any BLE device (see :mod:`niibot.diagnostics`)."""
    try:
        async with state.lock:
            return await diagnose(request.address, request.seconds)
    except Exception as exc:  # noqa: BLE001 - reported to the UI as is
        raise HTTPException(status_code=502, detail=f"Could not talk to the device: {exc}") from exc


@app.get("/api/printers")
async def api_printers(all: bool = False) -> dict:
    """The printer list: everything known (once connected → own library) plus printers in range.

    Other Bluetooth devices are only included with ``?all=true`` (the "show everything" switch).
    One physical printer appears once, even when its old BLE address is still in the registry.
    """
    current = state.printer.link.address if state.connected else None
    items: list[dict] = []
    seen: set[str] = set()
    for row in store.list_printers():
        seen.add(row["address"])
        worker = state.connections.get(str(row["address"]).lower())
        protocol = (worker.protocol if worker else None) or row.get("protocol") or detect_protocol(row.get("name"))
        items.append({
            **row,
            "known": True,
            "available": any(device.get("address") == row["address"] for device in state.last_scan)
            or bool(worker and worker.connected),
            "protocol": protocol,
            "connected": bool(worker and worker.connected),
            "active": bool(current and worker is not None and worker.printer is state.printer),
            "active_scope": row.get("scope") == device_scope(),
            "in_range": bool(worker.in_range) if worker else False,
            "status": worker.status if worker else "idle",
            "last_error": _PRINTER_ERRORS.get(str(row["address"]).lower()) or (worker.last_error if worker else None),
        })
    for device in state.last_scan:
        address = device.get("address")
        if not address or address in seen:
            continue
        is_printer = looks_like_printer(
            device.get("name"), device.get("service_uuids"), bool(device.get("niimbot"))
        )
        if not is_printer and not all:
            continue
        protocol = detect_protocol(device.get("name"))
        items.append({
            "address": address,
            "name": device.get("name"),
            "protocol": protocol,
            "model_key": None,
            "model_name": None,
            "scope": None,
            "known": False,
            "available": True,
            "connected": False,
            "active_scope": False,
            "label_printer": is_printer,
        })
    items.sort(key=lambda item: (not item["connected"], not item["available"], not item["known"], str(item.get("name"))))
    # One physical printer, one entry: the same unit can still be in the registry twice while its
    # old BLE address lingers (Windows keeps advertising it from the cache).
    unique: list[dict] = []
    claimed: dict[str, dict] = {}
    for item in items:
        if not item.get("known"):
            unique.append(item)
            continue
        identity = f"{str(item.get('name') or '').strip().lower()}|{item.get('protocol')}"
        holder = claimed.get(identity)
        if holder is not None and not item["connected"]:
            continue
        if holder is not None:
            unique.remove(holder)
        claimed[identity] = item
        unique.append(item)
    return {
        "printers": unique,
        "current": current,
        "scope": device_scope(),
        "protocol": state.protocol,
    }


@app.delete("/api/printers/{address}")
async def api_printer_forget(address: str) -> dict:
    store.forget_printer(address)
    return await api_printers()


@app.post("/api/connect")
async def api_connect(request: ConnectRequest) -> dict:
    """Connect one printer through its worker, then make it the one that prints."""
    settings = store.load_settings()
    requested = (request.protocol or "").strip().lower()
    auto = requested in {"", "auto"}
    name = _scanned_name(request.address) or store.printer_name(request.address) or ""
    protocol = _protocol_for(request.address, settings, name) if auto else requested
    worker = _worker_for(request.address, name, protocol)
    worker.wanted = True
    state.maintain_links = True
    if not await worker.connect_once():
        # The worker keeps trying on its own: a printer that was asleep comes back by itself.
        worker.ensure_task()
        detail = f"Could not connect: {worker.last_error or 'unknown error'}"
        raise HTTPException(status_code=502, detail=detail)
    worker.remember()
    await _retire_duplicate_rows()
    _activate_connection(worker)
    worker.ensure_task()
    # Name as well as address: a printer with a random BLE address comes back under a new
    # one after a power cycle, and only the name tells the app it is the same unit.
    store.save_settings({
        "last_address": worker.address,
        "last_name": worker.name or worker.printer.info.model_name or worker.address,
    })
    if worker.printer.info.model_name and worker.printer.info.model_name != "unknown":
        store.save_settings({"last_model": worker.printer.info.model_name})
    state.maintain_links = True
    return _state_payload()


def _scanned_name(address: str) -> str:
    """Advertised name for an address from the last scan (the freshest source we have)."""
    for device in state.last_scan:
        if str(device.get("address", "")).lower() == address.lower():
            return _device_name(device)
    return ""


@app.post("/api/disconnect")
async def api_disconnect() -> dict:
    """Drop every link and stop the workers — an explicit disconnect must stay disconnected."""
    state.reconnect_wanted = False
    state.maintain_links = False
    for worker in list(state.connections.values()):
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001 - best effort
            pass
    state.connections.clear()
    state.printer_key = None
    try:
        await state.printer.disconnect()
    except Exception:  # noqa: BLE001 - best effort
        pass
    return _state_payload()


@app.post("/api/refresh")
async def api_refresh() -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    async with state.lock:
        try:
            await state.printer.refresh()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _state_payload()


@app.get("/api/calibration-strip")
async def api_calibration_strip(width_mm: float = 0.0, height_mm: float = 0.0) -> dict:
    """A millimetre scale to print on the roll — read the label pitch off it."""
    from .calibration import measuring_strip

    label_w, label_h, _dpi, _head, _wpx, _hpx = _label_geometry()
    spec = measuring_strip(
        float(width_mm) or label_w,
        float(height_mm) or label_h,
    )
    return {"spec": spec.model_dump(mode="json")}


@app.post("/api/detect-size")
async def api_detect_size() -> dict:
    """Read the label format measured by the printer and store it for this roll."""
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    async with state.lock:
        try:
            geometry = await state.printer.read_paper_info()
            await state.printer.refresh()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    paper = state.printer.paper
    if not geometry.valid:
        if geometry.supported is False:
            detail = (
                "This model does not report the label format over Bluetooth (the B1 does not answer 0x59). "
                "Enter the size once — it is remembered for this roll code and the next rolls "
                "from the same series configure themselves."
            )
        else:
            detail = "The printer did not report the format — enter the size manually."
        return {
            "detected": False,
            "detail": detail,
            "geometry": geometry.to_dict(),
            "state": _state_payload(),
        }

    key = store.profile_key(paper.barcode, paper.uuid, device_scope())
    stored = None
    if key:
        stored = store.save_profile(
            PaperProfile(
                key=key,
                name=f"{geometry.width_mm:g} × {geometry.height_mm:g} mm",
                width_mm=geometry.width_mm or 0.0,
                height_mm=geometry.height_mm or 0.0,
                gap_mm=geometry.gap_mm or 0.0,
                label_type=geometry.label_type if geometry.label_type is not None else (paper.label_type or 1),
                capacity=paper.capacity,
            ),
            device_scope(),
        )
    return {
        "detected": True,
        "geometry": geometry.to_dict(),
        "profile": stored,
        "state": _state_payload(),
    }


class FeedRequest(BaseModel):
    mm: float = 6.0


@app.post("/api/printer/feed")
async def api_printer_feed(request: FeedRequest) -> dict:
    """Advance the paper by hand — the quickest way to find the right value for the tear bar."""
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    feed = getattr(state.printer, "feed", None)
    if feed is None:
        raise HTTPException(status_code=409, detail="This printer handles paper advance itself.")
    millimetres = max(0.5, min(50.0, float(request.mm)))
    async with state.lock:
        try:
            await feed(millimetres)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"fed_mm": millimetres}


@app.post("/api/printer/gap-feed")
async def api_printer_gap_feed(request: FeedRequest) -> dict:
    """Walk to the next gap: the printer's sensor decides where the label ends.

    This is the calibration the vendor app performs on this family. The Fichero has no command
    that reports a label size, so the app sets the paper type to *gap* and asks the printer to
    feed; the firmware advances until its sensor sees the gap. Doing it once per label shows
    immediately whether the printer finds the gaps on this stock.
    """
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    form_feed = getattr(state.printer, "form_feed", None)
    if form_feed is None:
        raise HTTPException(
            status_code=409,
            detail="This printer reports the label size itself — use Detect format.",
        )
    labels = max(1, min(5, int(request.mm or 1)))
    steps: list[dict] = []
    async with state.lock:
        try:
            set_paper = getattr(state.printer, "set_paper_type", None)
            if callable(set_paper):
                await set_paper(1)  # the panel's Niimbot label type 1 = labels with a gap
            for index in range(labels):
                ok = await form_feed()
                paper = state.printer.paper
                steps.append({
                    "label": index + 1,
                    "paper_present": paper.paper_inserted,
                    "gap_found": bool(ok),
                })
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"labels": labels, "steps": steps, "state": _state_payload()}


@app.post("/api/calibrate")
async def api_calibrate() -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    async with state.lock:
        try:
            await state.printer.calibrate()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await state.printer.refresh()
    return _state_payload()


@app.post("/api/density")
async def api_density(request: DensityRequest) -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    async with state.lock:
        try:
            await state.printer.set_density(request.density)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _state_payload()


@app.get("/api/log")
async def api_log(limit: int = 200) -> dict:
    return {
        "connected": state.connected,
        "info": asdict(state.printer.info),
        "traffic": state.printer.link.traffic(max(10, min(500, limit))),
        "dropped_bytes": state.printer.link._reader.dropped_bytes,  # noqa: SLF001 - diagnostics
    }


# ------------------------------------------------------------------- labels
def _last_printer_row() -> dict | None:
    """The stored row of the printer the settings point at — the one that will be used next."""
    address = str(store.load_settings().get("last_address") or "").strip()
    if not address:
        return None
    return next(
        (row for row in store.list_printers() if str(row["address"]).lower() == address.lower()),
        None,
    )


def _geometry() -> tuple[int, int]:
    """Print resolution and head width — the model's own off-line, the driver's while connected."""
    info = state.printer.info
    if not state.connected and not info.geometry_override:
        remembered = _last_printer_row()
        if remembered:
            if (remembered.get("protocol") or state.protocol) == "luckprinter":
                return 203, 96
            model = model_for_key(remembered.get("model_key"))
            if model is not None:
                return model.dpi, model.printhead_px
    return info.dpi or 203, info.printhead_px or 384


def _with_mirror(spec: LabelSpec, mirror: MirrorRequest | None) -> LabelSpec:
    if mirror is None:
        return spec
    if mirror.cells and len(mirror.cells) > 1:
        cells = [
            {"shape": cell.shape, "x": cell.x, "y": cell.y, "w": cell.w, "h": cell.h}
            for cell in mirror.cells
        ]
        return sheet.apply_cell_mirror(spec, cells, mirror.flip)
    if mirror.regions < 2:
        return spec
    return sheet.apply_mirror(spec, mirror.regions, mirror.flip)


def _ink_supported() -> bool:
    """Red is a Niimbot page mode; the LuckPrinter family has one ink and no colour field."""
    return state.protocol == "niimbot"


def _printer_model_key() -> str:
    """Which model is printing — the live one, or the remembered one while it is asleep."""
    key = state.printer.info.model_key or ""
    if state.connected and key and key != "UNKNOWN":
        return key
    remembered = _last_printer_row()
    return str((remembered or {}).get("model_key") or key or "")


def _ink_warnings(spec: LabelSpec) -> list[str]:
    if not spec.uses_red:
        return []
    if not _ink_supported():
        return ["This printer family prints one colour — the red items will print in black."]
    key = _printer_model_key()
    if key in TWO_COLOUR_MODELS:
        return []
    model = model_for_key(key)
    name = model.name if model is not None else (state.printer.info.model_name or "this model")
    return [
        f"Red ink needs red/black two-colour stock and a head that develops it (B1 Pro, B21 Pro,"
        f" B2 Pro). {name} is not one of them, so red may come out grey or black."
    ]


@app.post("/api/preview")
async def api_preview(request: PreviewRequest) -> dict:
    dpi, printhead = _geometry()
    spec = _with_mirror(request.spec, request.mirror)
    try:
        payload = preview_payload(spec, dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload["warnings"] = list(payload.get("warnings") or []) + _ink_warnings(spec)
    return payload


@app.post("/api/code-size")
async def api_code_size(request: CodeSizeRequest) -> dict:
    """Minimum box for a code element: every module must hold >= 2 printer dots."""
    dpi, _printhead = _geometry()
    try:
        limit = minimum_size(
            request.kind,
            dpi,
            request.text,
            request.ec_level,
            request.barcode_format,
        )
    except Exception as exc:  # unknown format / empty data
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    limit.update({"dpi": dpi, "kind": request.kind})
    return limit


@app.post("/api/spec-preview")
async def api_spec_preview(request: SpecPreviewRequest) -> dict:
    """Small PNG preview of any label spec — used by the template gallery."""
    dpi, printhead = _geometry()
    try:
        rendered = render_label(request.spec, dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    image = rendered.image.convert("L")
    limit = max(80, min(720, request.max_width))
    colour = colourise(rendered.black, rendered.red)
    if image.width > limit:
        ratio = limit / image.width
        size = (limit, max(1, int(image.height * ratio)))
        image = image.resize(size, Image.LANCZOS)
        colour = colour.resize(size, Image.LANCZOS)
    buffer = io.BytesIO()
    colour.save(buffer, format="PNG")
    return {"png": base64.b64encode(buffer.getvalue()).decode("ascii"), "width_px": colour.width, "height_px": colour.height}


@app.post("/api/element-preview")
async def api_element_preview(request: ElementPreviewRequest) -> dict:
    """Render a single element on its own — used for drag ghosts and layer thumbnails."""
    element = next((item for item in request.spec.elements if item.id == request.element_id), None)
    if element is None:
        raise HTTPException(status_code=404, detail="Element not found.")
    dpi, _printhead = _geometry()
    margin = round(max(element.w, element.h) * 0.75 + 1, 2)
    single = LabelSpec(
        name="element",
        width_mm=round(element.w + 2 * margin, 2),
        height_mm=round(element.h + 2 * margin, 2),
        elements=[element.model_copy(update={"x": margin, "y": margin, "visible": True}, deep=True)],
    )
    try:
        rendered = render_label(single, dpi, 20000, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    image = rendered.image.convert("L")
    colour = colourise(rendered.black, rendered.red)
    inverted = ImageOps.invert(image)
    box = inverted.getbbox()
    # Where the element itself sits inside the returned PNG (after trimming), so a drag
    # ghost can place the artwork exactly inside the element frame instead of stretching it.
    px_per_mm = dpi / 25.4
    element_box = {
        "x": round(margin * px_per_mm),
        "y": round(margin * px_per_mm),
        "w": max(1, round(element.w * px_per_mm)),
        "h": max(1, round(element.h * px_per_mm)),
    }
    if box:
        image = image.crop(box)
        colour = colour.crop(box)
        element_box["x"] -= box[0]
        element_box["y"] -= box[1]
    buffer = io.BytesIO()
    colour.save(buffer, format="PNG")
    return {
        "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width_px": colour.width,
        "height_px": colour.height,
        "element_box": element_box,
    }


@app.post("/api/render.png")
async def api_render_png(request: PreviewRequest) -> Response:
    dpi, printhead = _geometry()
    spec = _with_mirror(request.spec, request.mirror)
    try:
        rendered = render_label(spec, dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    buffer = io.BytesIO()
    colourise(rendered.black, rendered.red).save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/api/labels")
async def api_labels() -> dict:
    return {"labels": store.list_labels(device_scope())}


@app.get("/api/labels/{label_id}")
async def api_label_get(label_id: str) -> dict:
    data = store.load_label(label_id)
    if not data:
        raise HTTPException(status_code=404, detail="Label not found.")
    return {"id": label_id, "spec": data}


@app.post("/api/labels")
async def api_label_save(request: SaveLabelRequest) -> dict:
    label_id = store.save_label(request.id, request.spec, device_scope())
    return {"id": label_id, "labels": store.list_labels(device_scope())}


@app.delete("/api/labels/{label_id}")
async def api_label_delete(label_id: str) -> dict:
    store.delete_label(label_id)
    return {"labels": store.list_labels(device_scope())}


# ---------------------------------------------------------------- templates
@app.get("/api/templates")
async def api_templates() -> dict:
    return {"templates": store.list_templates(device_scope())}


@app.get("/api/templates/{template_id}")
async def api_template_get(template_id: str) -> dict:
    data = store.load_template(template_id)
    if not data:
        raise HTTPException(status_code=404, detail="Template not found.")
    return {"id": template_id, "spec": data}


@app.post("/api/templates")
async def api_template_save(request: SaveTemplateRequest) -> dict:
    name = request.name.strip() or request.spec.name or "Template"
    template_id = store.save_template(request.id, name, request.spec, device_scope())
    return {"id": template_id, "templates": store.list_templates(device_scope())}


@app.delete("/api/templates/{template_id}")
async def api_template_delete(template_id: str) -> dict:
    store.delete_template(template_id)
    return {"templates": store.list_templates(device_scope())}


# ----------------------------------------------------------------- profiles
@app.get("/api/profiles")
async def api_profiles() -> dict:
    return {"profiles": list(store.list_profiles(device_scope()).values())}


# ------------------------------------------------------------------- catalog
@app.get("/api/catalog")
async def api_catalog() -> dict:
    return {"catalog": store.list_consumables()}


@app.post("/api/catalog/import")
async def api_catalog_import() -> dict:
    """Read label formats out of the vendor app's local cache into the database."""
    try:
        found, scanned = scan_vendor_catalog()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read the NIIMBOT app cache: {exc}") from exc
    for consumable in found.values():
        store.upsert_consumable(consumable.to_dict())
    stats = store.print_statistics(recent_limit=0)
    return {
        "imported": len(found),
        "total": stats["formats"],
        "scanned": scanned,
        "catalog": store.list_consumables(),
        "state": _state_payload(),
    }


@app.post("/api/catalog/manual")
async def api_catalog_manual(request: ProfileRequest) -> dict:
    """Store a format for a roll by hand (same effect as the vendor catalog)."""
    barcode = request.key[3:] if request.key.startswith("bc:") else request.key
    record = store.upsert_consumable(
        {
            "barcode": barcode,
            "name": request.name or f"{request.width_mm:g} × {request.height_mm:g} mm",
            "sku": request.name or None,
            "width_mm": request.width_mm,
            "height_mm": request.height_mm,
            "paper_type": request.label_type,
            "capacity": request.capacity,
            "source": "manual",
        }
    )
    return {"catalog": store.list_consumables(), "entry": record, "state": _state_payload()}


# ------------------------------------------------------------------ statistics
@app.get("/api/stats")
async def api_stats(recent: int = 10) -> dict:
    return store.print_statistics(recent_limit=max(0, min(100, recent)))


def _regions_from_request(items: list[RegionRequest]) -> list[Region]:
    """Turn drawn zones into stored regions, keeping each zone's merged bounding box."""
    regions: list[Region] = []
    for item in items:
        shapes = [
            ZoneShape(
                kind="circle" if shape.kind == "circle" else "rect",
                x=shape.x,
                y=shape.y,
                w=shape.w,
                h=shape.h,
            )
            for shape in item.shapes
        ]
        region = Region(
            shape=item.shape if item.shape in ("rect", "circle", "line", "mixed") else "rect",
            x=item.x,
            y=item.y,
            w=item.w,
            h=item.h,
            label=item.label,
            angle=item.angle,
            thickness_mm=item.thickness_mm,
            shapes=shapes,
        )
        regions.append(region.normalize() if shapes else region)
    return regions


@app.post("/api/roll-format")
async def api_roll_format(request: RollFormatRequest) -> dict:
    """Store the size (and the drawn item zones) of a roll the printer cannot measure."""
    regions = _regions_from_request(request.regions)
    sticker_w = request.sticker_w_mm or request.width_mm / max(1, request.cols)
    sticker_h = request.sticker_h_mm or request.height_mm / max(1, request.rows)
    name = request.name or f"{sticker_w:g} × {sticker_h:g} mm"
    entry = {
        "barcode": request.barcode,
        "name": name,
        "width_mm": request.width_mm,
        "height_mm": request.height_mm,
        "paper_type": request.label_type,
        "rows": request.rows,
        "cols": request.cols,
        "regions": [region.to_dict() for region in regions],
        "capacity": request.capacity,
        "source": "manual",
    }
    store.upsert_consumable(entry)
    profile_key_value = store.profile_key(request.barcode, None, device_scope())
    if profile_key_value:
        store.save_profile(
            PaperProfile(
                key=profile_key_value,
                name=name,
                width_mm=request.width_mm,
                height_mm=request.height_mm,
                gap_mm=request.gap_mm,
                label_type=request.label_type,
                capacity=request.capacity,
                regions=regions,
                sticker_w_mm=round(sticker_w, 3),
                sticker_h_mm=round(sticker_h, 3),
                row_space_mm=request.row_space_mm,
                col_space_mm=request.col_space_mm,
                rows=request.rows,
                cols=request.cols,
            ),
            device_scope(),
        )
    log.info(
        "Roll %s remembered as %g × %g mm, sticker %g × %g mm (%d rows, %d zones drawn)",
        request.barcode,
        request.width_mm,
        request.height_mm,
        sticker_w,
        sticker_h,
        request.rows,
        len(regions),
    )
    payload = _state_payload()
    # Hand the calibration back even when the printer is away: with no roll code on the wire the
    # polled state cannot resolve a profile, and the top bar would keep showing the old one.
    if profile_key_value:
        saved = store.get_profile(profile_key_value)
        if saved:
            payload["profile"] = _decorate_profile(saved, profile_key_value, request.barcode)
    return payload


@app.post("/api/roll-format/outline")
async def api_roll_outline(request: ZoneOutlineRequest) -> dict:
    """The exact bitmap the calibrator prints: only the zone borders."""
    dpi, _printhead = _geometry()
    regions = _regions_from_request(request.regions)
    image = zones.outline_image(request.width_mm, request.height_mm, regions, dpi,
                                stroke_mm=request.stroke_mm)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width_px": image.width,
        "height_px": image.height,
        "dpi": dpi,
        "zones": len(regions),
    }


@app.post("/api/roll-format/print")
async def api_roll_print(request: ZoneOutlineRequest) -> dict:
    """Print the zone borders on the physical roll — a calibration check."""
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="A print job is already running — wait or cancel it.")
    dpi, _printhead = _geometry()
    regions = _regions_from_request(request.regions)
    image = zones.outline_image(request.width_mm, request.height_mm, regions, dpi,
                                stroke_mm=request.stroke_mm)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_id = store.save_upload("zone-outline.png", buffer.getvalue())
    spec = LabelSpec(
        name="Zone check",
        width_mm=request.width_mm,
        height_mm=request.height_mm,
        elements=[
            Element(
                type="image",
                x=0,
                y=0,
                w=request.width_mm,
                h=request.height_mm,
                image_id=image_id,
                image_fit="stretch",
            )
        ],
    )
    label_type = request.label_type
    if label_type is None:
        paper = state.printer.paper
        label_type = paper.label_type if paper.label_type_source == "rfid" else spec.label_type
    return await _start_job(
        spec,
        copies=max(1, request.copies),
        density=request.density,
        label_type=label_type,
        title="Zone outline check",
    )


@app.post("/api/profiles")
async def api_profile_save(request: ProfileRequest) -> dict:
    regions = _regions_from_request(request.regions)
    profile = PaperProfile(
        key=request.key,
        name=request.name or f"{request.width_mm:g} × {request.height_mm:g} mm",
        width_mm=request.width_mm,
        height_mm=request.height_mm,
        gap_mm=request.gap_mm,
        label_type=request.label_type,
        capacity=request.capacity,
        regions=regions,
    )
    stored = store.save_profile(profile, device_scope())
    return {"profile": stored, "profiles": list(store.list_profiles(device_scope()).values())}


@app.delete("/api/profiles/{key}")
async def api_profile_delete(key: str) -> dict:
    store.delete_profile(key)
    return {"profiles": list(store.list_profiles(device_scope()).values())}


# ------------------------------------------------------------------ uploads
@app.post("/api/sheet/import")
async def api_sheet_import(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if len(payload) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is too large (16 MB limit).")
    try:
        table = spreadsheet.read_table(file.filename or "", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # malformed archive, bad XML, ...
        raise HTTPException(status_code=400, detail=f"Cannot read the spreadsheet: {exc}") from exc
    table = [row for row in table if any(cell for cell in row)]
    if not table:
        raise HTTPException(status_code=400, detail="The spreadsheet is empty.")
    header = table[0]
    rows = table[1:]
    return {
        "header": header,
        "rows": rows,
        "rows_count": len(rows),
        "columns_count": max((len(row) for row in table), default=0),
    }


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if len(payload) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The file is too large (8 MB limit).")
    try:
        image_id = store.save_upload(file.filename or "image.png", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    size = _image_size(payload)
    return {"id": image_id, "url": f"/api/uploads/{image_id}", "width_px": size[0], "height_px": size[1]}


@app.get("/api/uploads/{image_id}")
async def api_upload_get(image_id: str) -> Response:
    found = store.upload_bytes(image_id)
    if found is None:
        raise HTTPException(status_code=404, detail="File not found.")
    payload, mime = found
    return Response(content=payload, media_type=mime)


@app.get("/api/uploads/{image_id}/meta")
async def api_upload_meta(image_id: str) -> dict:
    """Source pixel size of an upload — the editor uses it for the print-quality hint."""
    found = store.upload_bytes(image_id)
    if found is None:
        raise HTTPException(status_code=404, detail="File not found.")
    payload, mime = found
    width, height = _image_size(payload)
    return {"id": image_id, "width_px": width, "height_px": height, "mime": mime, "bytes": len(payload)}


def _image_size(payload: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:  # pragma: no cover - depends on the file
        return (0, 0)


# -------------------------------------------------------------------- print
def _test_spec() -> LabelSpec:
    width = 48.0
    height = 30.0
    spec = LabelSpec(
        name="Printer test",
        width_mm=width,
        height_mm=height,
        elements=[
            Element(type="rect", x=1, y=1, w=width - 2, h=height - 2, thickness_mm=0.4),
            Element(
                type="text",
                x=2,
                y=2,
                w=width - 4,
                h=7,
                text=f"TEST {state.printer.info.model_name}",
                font_size_pt=13,
                bold=True,
                align="center",
            ),
            Element(
                type="text",
                x=2,
                y=9,
                w=width - 24,
                h=6,
                text="Density 3 · 203 dpi" if state.printer.info.dpi == 203 else "Density 3",
                font_size_pt=8,
            ),
            Element(type="barcode", x=2, y=16, w=width - 24, h=11, text="NIIBOT-B1", barcode_format="code128"),
            Element(type="qr", x=width - 20, y=9, w=17, h=17, text="https://niimbot.local/test"),
        ],
    )
    return spec


@app.post("/api/test-print")
async def api_test_print() -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _check_ready()
    return await _start_job(_test_spec(), copies=1, density=None, label_type=None, title="Test print")


@app.post("/api/print")
async def api_print(request: PrintRequest) -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="A print job is already running — wait or cancel it.")
    spec = _with_mirror(request.spec, request.mirror)
    _check_ink(spec)
    copies = request.copies if request.copies is not None else spec.copies
    density = request.density if request.density is not None else spec.density
    label_type = request.label_type
    if label_type is None:
        paper = state.printer.paper
        # The roll's tag is authoritative; otherwise use what the designer says.
        label_type = paper.label_type if paper.label_type_source == "rfid" else spec.label_type
    return await _start_job(spec, copies=copies, density=density, label_type=label_type)


@app.post("/api/print-sheet")
async def api_print_sheet(request: SheetPrintRequest) -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="A print job is already running — wait or cancel it.")

    columns = sheet.sync_columns(request.spec)
    _check_ink(request.spec)
    specs = sheet.build_sheet_specs(
        request.spec,
        columns,
        request.rows,
        include_current=request.include_current,
        regions=request.mirror.regions if request.mirror else 1,
        flip=request.mirror.flip if request.mirror else False,
    )
    if not specs:
        raise HTTPException(status_code=400, detail="The sheet has no rows to print.")

    density = request.density if request.density is not None else request.spec.density
    label_type = request.label_type
    if label_type is None:
        paper = state.printer.paper
        label_type = paper.label_type if paper.label_type_source == "rfid" else request.spec.label_type
    return await _start_sheet_job(specs, copies=request.copies, density=density, label_type=label_type)


def _check_ready() -> None:
    paper = state.printer.paper
    if paper.lid_closed is False:
        raise HTTPException(status_code=409, detail="The printer lid is open.")
    if paper.paper_inserted is False:
        raise HTTPException(status_code=409, detail="There is no paper in the printer.")


def _check_ink(spec: LabelSpec) -> None:
    """Refuse a red job on a printer that cannot develop it — printing black would be a surprise."""
    if not spec.uses_red or _ink_supported():
        return
    raise HTTPException(
        status_code=409,
        detail="This printer family prints one colour — switch the red items back to black to print this label.",
    )


async def _start_job(
    spec: LabelSpec,
    *,
    copies: int,
    density: int | None,
    label_type: int | None,
    title: str | None = None,
) -> dict:
    paper = state.printer.paper
    catalog_entry = store.get_catalog_entry(paper.barcode)
    job = Job(
        id=uuid.uuid4().hex[:8],
        message=title or "Rendering the label…",
        copies=max(1, copies),
        density=density,
        label_type=label_type,
        paper_barcode=paper.barcode,
        sku=(catalog_entry or {}).get("sku") or (catalog_entry or {}).get("name"),
        width_mm=spec.width_mm,
        height_mm=spec.height_mm,
        kind="label",
        printer=_current_printer_name(),
        protocol=state.protocol,
        payload=spec.model_dump_json(),
    )
    state.job = job
    state.job_task = asyncio.create_task(_run_print(job, spec, copies, density, label_type))
    return {"job": job.to_dict()}


async def _run_print(job: Job, spec: LabelSpec, copies: int, density: int | None, label_type: int | None) -> None:
    try:
        dpi, printhead = _geometry()
        rendered = render_label(spec, dpi, printhead, store.upload_path)
        encoded = encode_planes(rendered.black, rendered.red)
        job.rows = encoded.rows
        job.cols = encoded.cols
        job.packets = encoded.packet_count
        job.message = "Sending data to the printer…"

        def progress(value: float, **extra: Any) -> None:
            job.progress = max(0.0, min(1.0, value))
            if extra.get("page"):
                job.message = f"Drukowanie: strona {extra['page']} / {max(1, copies)}"

        async with state.lock:
            await state.printer.print_bitmap(
                encoded,
                copies=max(1, copies),
                density=density,
                label_type=label_type,
                on_progress=progress,
            )
        job.state = "done"
        job.progress = 1.0
        job.message = "Printed."
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.message = "Cancelled."
        raise
    except ProtocolError as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = str(exc)
        await state.printer.cancel()
    except Exception as exc:  # pragma: no cover - unexpected
        log.exception("Printing failed")
        job.state = "error"
        job.error = str(exc)
        job.message = f"Error: {exc}"
        await state.printer.cancel()
    finally:
        _finish_job(job)


async def _start_sheet_job(
    specs: list[LabelSpec],
    *,
    copies: int | None,
    density: int | None,
    label_type: int | None,
) -> dict:
    paper = state.printer.paper
    catalog_entry = store.get_catalog_entry(paper.barcode)
    first = specs[0]
    job = Job(
        id=uuid.uuid4().hex[:8],
        message=f"Sheet: {_label_count(len(specs))} — rendering…",
        copies=len(specs) * max(1, copies or 1),
        density=density,
        label_type=label_type,
        paper_barcode=paper.barcode,
        sku=(catalog_entry or {}).get("sku") or (catalog_entry or {}).get("name"),
        width_mm=first.width_mm,
        height_mm=first.height_mm,
        kind="sheet",
        printer=_current_printer_name(),
        protocol=state.protocol,
        payload=json.dumps([spec.model_dump(mode="json") for spec in specs]),
    )
    state.job = job
    state.job_task = asyncio.create_task(_run_sheet(job, specs, copies or 1, density, label_type))
    return {"job": job.to_dict()}


async def _run_sheet(job: Job, specs: list[LabelSpec], copies: int, density: int | None, label_type: int | None) -> None:
    total = len(specs) * max(1, copies)
    sent = 0
    try:
        dpi, printhead = _geometry()
        async with state.lock:
            for index, spec in enumerate(specs, start=1):
                rendered = render_label(spec, dpi, printhead, store.upload_path)
                encoded = encode_planes(rendered.black, rendered.red)
                job.rows = encoded.rows
                job.cols = encoded.cols
                job.packets = encoded.packet_count
                label = f"Sheet: row {index} / {len(specs)}"
                job.message = f"{label} — sending…"
                job.progress = max(0.0, min(0.98, sent / total))

                def progress(value: float, **extra: Any) -> None:
                    done = sent + max(0.0, min(1.0, value)) * max(1, copies)
                    job.progress = max(0.0, min(0.98, done / total))
                    job.message = f"{label} — {int(job.progress * 100)}%"

                await state.printer.print_bitmap(
                    encoded,
                    copies=max(1, copies),
                    density=density,
                    label_type=label_type,
                    on_progress=progress,
                )
                sent += max(1, copies)
                job.progress = max(0.0, min(0.98, sent / total))
        job.state = "done"
        job.progress = 1.0
        job.message = f"Printed {_label_count(total)} from the sheet."
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.message = "Cancelled."
        raise
    except ProtocolError as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = str(exc)
        await state.printer.cancel()
    except Exception as exc:  # pragma: no cover - unexpected
        log.exception("Sheet printing failed")
        job.state = "error"
        job.error = str(exc)
        job.message = f"Error: {exc}"
        await state.printer.cancel()
    finally:
        _finish_job(job)


def _current_printer_name() -> str:
    """How the queue panel should name the printer that is doing the work."""
    link = getattr(state.printer, "link", None)
    name = getattr(link, "name", None) or state.printer.info.model_name
    return str(name or getattr(link, "address", "") or state.protocol)


def _finish_job(job: Job) -> None:
    job.finished = time.time()
    try:
        store.record_print_job(
            job_id=job.id,
            state=job.state,
            started_at=job.started,
            finished_at=job.finished,
            rows=job.rows,
            cols=job.cols,
            packets=job.packets,
            copies=job.copies,
            density=job.density,
            label_type=job.label_type,
            paper_barcode=job.paper_barcode,
            sku=job.sku,
            width_mm=job.width_mm,
            height_mm=job.height_mm,
            error=job.error,
            printer=job.printer,
            protocol=job.protocol,
            kind=job.kind,
            message=job.message,
            payload=job.payload,
        )
        store.prune_print_jobs()
    except Exception:  # pragma: no cover - statistics must never break printing
        log.exception("Could not save the print statistics")


def _queue_entry(row: dict) -> dict:
    """One row for the printer-queue panel."""
    return {
        "id": row.get("id"),
        "job_id": row.get("job_id"),
        "state": row.get("state") or "done",
        "kind": row.get("kind") or "label",
        "message": row.get("message") or "",
        "error": row.get("error"),
        "copies": row.get("copies") or 1,
        "width_mm": row.get("width_mm"),
        "height_mm": row.get("height_mm"),
        "sku": row.get("sku") or row.get("paper_barcode"),
        "printer": row.get("printer"),
        "protocol": row.get("protocol"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "repeatable": bool(row.get("repeatable")),
    }


@app.get("/api/queue")
async def api_queue(limit: int = 20) -> dict:
    """Print history plus the job that is running right now."""
    current = None
    if state.job and state.job.state == "running":
        job = state.job
        current = {
            "id": None,
            "job_id": job.id,
            "state": job.state,
            "kind": job.kind,
            "message": job.message,
            "error": job.error,
            "progress": round(job.progress, 3),
            "copies": job.copies,
            "width_mm": job.width_mm,
            "height_mm": job.height_mm,
            "sku": job.sku,
            "printer": job.printer,
            "protocol": job.protocol,
            "started_at": job.started,
            "finished_at": None,
            "repeatable": False,
        }
    jobs = [_queue_entry(row) for row in store.list_print_jobs(max(1, min(100, limit)))]
    return {"jobs": jobs, "current": current}


@app.post("/api/queue/{row_id}/repeat")
async def api_queue_repeat(row_id: int) -> dict:
    """Print the same label (or sheet) again, exactly as it was sent."""
    row = store.get_print_job(row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That job is no longer in the queue.")
    if not row.get("payload"):
        raise HTTPException(
            status_code=409,
            detail="This job cannot be repeated — it was not created from a label in the designer.",
        )
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="A print job is already running — wait or cancel it.")
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="The stored job data is damaged.") from exc
    copies = int(row.get("copies") or 1)
    if row.get("kind") == "sheet":
        specs = [LabelSpec.model_validate(item) for item in payload]
        result = await _start_sheet_job(
            specs,
            copies=None,
            density=row.get("density"),
            label_type=row.get("label_type"),
        )
    else:
        spec = LabelSpec.model_validate(payload)
        result = await _start_job(
            spec,
            copies=copies,
            density=row.get("density"),
            label_type=row.get("label_type"),
        )
    return result


@app.delete("/api/queue/{row_id}")
async def api_queue_delete(row_id: int) -> dict:
    if not store.delete_print_job(row_id):
        raise HTTPException(status_code=404, detail="That job is no longer in the queue.")
    return await api_queue()


@app.get("/api/job")
async def api_job() -> dict:
    return {"job": state.job.to_dict() if state.job else None}


@app.post("/api/cancel")
async def api_cancel() -> dict:
    await state.printer.cancel()
    if state.job and state.job.state == "running":
        state.job.state = "cancelled"
        state.job.message = "Cancelled."
        state.job.finished = time.time()
    if state.job_task and not state.job_task.done():
        state.job_task.cancel()
    return {"job": state.job.to_dict() if state.job else None}


# -------------------------------------------------------------------- pages
class _NoCacheStatic(StaticFiles):
    """Assets revalidate on every load so a refreshed page never runs stale JS/CSS."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------- Windows IPP printer
@dataclass
class IppPending:
    document: bytes = b""
    mime: str = ""
    copies: int = 1


IPP_PENDING: dict[int, IppPending] = {}
IPP_COUNTER = {"value": 0}


def _fit_options() -> FitOptions:
    raw = store.load_settings().get("fit")
    if not isinstance(raw, dict):
        return FitOptions()
    allowed = set(FitOptions.__annotations__)
    try:
        return FitOptions(**{key: value for key, value in raw.items() if key in allowed}).normalized()
    except (TypeError, ValueError):
        return FitOptions()


def _label_geometry() -> tuple[float, float, int, int, int, int]:
    """Label size for incoming Windows jobs: printer measurement, catalog, then design."""
    paper = state.printer.paper
    catalog = store.get_catalog_entry(paper.barcode) or {}
    width_mm = paper.detected_width_mm or catalog.get("width_mm") or 40.0
    height_mm = paper.detected_height_mm or catalog.get("height_mm") or 30.0
    dpi = state.printer.info.dpi or 203
    head_px = state.printer.info.printhead_px or 384
    width_px = min(int(round(float(width_mm) * dpi / 25.4)), head_px)
    height_px = int(round(float(height_mm) * dpi / 25.4))
    return float(width_mm), float(height_mm), dpi, head_px, max(1, width_px), max(1, height_px)


def _ipp_state() -> ipp_mod.IppPrinterState:
    width_mm, height_mm, dpi, _head, _w, _h = _label_geometry()
    return ipp_mod.IppPrinterState(
        name=f"Niimbot {state.printer.info.model_name}".strip(),
        make_and_model=f"Niimbot {state.printer.info.model_name} (Niibot Studio)".strip(),
        dpi=dpi,
        width_mm=width_mm,
        height_mm=height_mm,
        accepting=state.connected,
    )


async def _print_ipp_document(payload: bytes, mime: str, copies: int) -> tuple[bool, str]:
    if not state.connected:
        raise HTTPException(status_code=409, detail="The printer is not connected.")
    _w, _h, dpi, _head, _wpx, _hpx = _label_geometry()
    image = ipp_mod.decode_document(payload, mime, dpi=dpi)
    if image is None:
        return False, f"unsupported document format ({mime or 'none'})"

    width_mm, height_mm, dpi, head_px, width_px, height_px = _label_geometry()
    options = _fit_options()
    black_layer, red_layer = separate_inks(image)
    black_bitmap = fit_to_label(black_layer, width_px, height_px, dpi, options)
    red_bitmap = fit_to_label(red_layer, width_px, height_px, dpi, options)
    encoded = encode_planes(black_bitmap, red_bitmap)
    if encoded.page_color == int(PageColor.DOUBLE) and not _ink_supported():
        raise HTTPException(
            status_code=409,
            detail="This printer family prints one colour — the document has red in it.",
        )
    paper = state.printer.paper
    catalog = store.get_catalog_entry(paper.barcode) or {}
    job = Job(
        id=uuid.uuid4().hex[:8],
        message="Printing from Windows…",
        copies=max(1, min(99, copies)),
        density=state.printer.info.density,
        label_type=paper.label_type,
        paper_barcode=paper.barcode,
        sku=catalog.get("sku") or catalog.get("name"),
        width_mm=width_mm,
        height_mm=height_mm,
        rows=encoded.rows,
        cols=encoded.cols,
        packets=encoded.packet_count,
        kind="windows",
        printer=_current_printer_name(),
        protocol=state.protocol,
    )
    state.job = job
    try:
        async with state.lock:
            await state.printer.print_bitmap(
                encoded,
                copies=job.copies,
                density=job.density,
                label_type=job.label_type,
                on_progress=lambda value, **_: setattr(job, "progress", value),
            )
        job.state = "done"
        job.progress = 1.0
        job.message = "Printed from Windows."
        return True, ""
    except Exception as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = f"Error: {exc}"
        raise
    finally:
        job.finished = time.time()
        try:
            store.record_print_job(
                job_id=job.id,
                state=job.state,
                started_at=job.started,
                finished_at=job.finished,
                rows=job.rows,
                cols=job.cols,
                packets=job.packets,
                copies=job.copies,
                density=job.density,
                label_type=job.label_type,
                paper_barcode=job.paper_barcode,
                sku=job.sku,
                width_mm=job.width_mm,
                height_mm=job.height_mm,
                error=job.error,
            )
        except Exception:  # pragma: no cover
            log.exception("Could not save the print statistics")


@app.post("/ipp/print")
async def ipp_print(request: Request) -> Response:
    """IPP endpoint used by the Windows "Microsoft IPP Class Driver" queue."""
    body = await request.body()
    try:
        ipp_request = ipp_mod.parse_request(body)
    except ValueError as exc:
        log.warning("Invalid IPP request: %s", exc)
        return Response(status_code=400, content=str(exc).encode("utf-8"))

    base_uri = f"ipp://{request.url.hostname}:{request.url.port or 80}/ipp/print"
    operation = ipp_request.operation

    if operation == ipp_mod.OP_GET_PRINTER_ATTRIBUTES:
        payload = ipp_mod.build_printer_attributes(_ipp_state(), base_uri)
        payload = payload[:4] + struct.pack(">I", ipp_request.request_id) + payload[8:]
        return Response(content=payload, media_type="application/ipp")

    if operation == ipp_mod.OP_VALIDATE_JOB:
        return Response(
            content=ipp_mod.build_simple_response(ipp_request), media_type="application/ipp"
        )

    if operation in (ipp_mod.OP_PRINT_JOB, ipp_mod.OP_SEND_DOCUMENT):
        mime = (
            ipp_request.get("document-format")
            or (IPP_PENDING.get(ipp_request.get_int("job-id", 0) or 0).mime if IPP_PENDING else None)
            or "application/octet-stream"
        )
        copies = ipp_request.get_int("copies", 1) or 1
        document = ipp_request.document
        if operation == ipp_mod.OP_SEND_DOCUMENT:
            pending = IPP_PENDING.pop(ipp_request.get_int("job-id", 0) or 0, None)
            if pending is not None:
                document = document or pending.document
                copies = copies or pending.copies
                mime = pending.mime or mime
        try:
            ok, message = await _print_ipp_document(document, str(mime), int(copies))
        except Exception as exc:
            log.exception("Printing from Windows failed")
            return Response(
                content=ipp_mod.build_simple_response(
                    ipp_request, ipp_mod.STATUS_NOT_POSSIBLE, message=str(exc)[:200]
                ),
                media_type="application/ipp",
            )
        if not ok:
            return Response(
                content=ipp_mod.build_simple_response(
                    ipp_request, ipp_mod.STATUS_NOT_POSSIBLE, message=message
                ),
                media_type="application/ipp",
            )
        IPP_COUNTER["value"] += 1
        return Response(
            content=ipp_mod.build_simple_response(
                ipp_request, ipp_mod.STATUS_OK, job_id=IPP_COUNTER["value"], job_state=9
            ),
            media_type="application/ipp",
        )

    if operation == ipp_mod.OP_CREATE_JOB:
        IPP_COUNTER["value"] += 1
        job_id = IPP_COUNTER["value"]
        IPP_PENDING[job_id] = IppPending(
            mime=str(ipp_request.get("document-format") or "application/octet-stream"),
            copies=ipp_request.get_int("copies", 1) or 1,
        )
        return Response(
            content=ipp_mod.build_simple_response(
                ipp_request, ipp_mod.STATUS_OK, job_id=job_id, job_state=3
            ),
            media_type="application/ipp",
        )

    if operation == ipp_mod.OP_CANCEL_JOB:
        await state.printer.cancel()
        IPP_PENDING.clear()
        return Response(
            content=ipp_mod.build_simple_response(ipp_request), media_type="application/ipp"
        )

    if operation in (ipp_mod.OP_GET_JOB_ATTRIBUTES, ipp_mod.OP_GET_JOBS):
        return Response(
            content=ipp_mod.build_simple_response(ipp_request), media_type="application/ipp"
        )

    log.info("Unsupported IPP operation: 0x%04X", operation)
    return Response(
        content=ipp_mod.build_simple_response(
            ipp_request, ipp_mod.STATUS_NOT_POSSIBLE, message="operation not supported"
        ),
        media_type="application/ipp",
    )


@app.get("/api/fit")
async def api_fit_get() -> dict:
    return {"fit": _fit_options().to_dict(), "geometry": dict(zip(
        ("width_mm", "height_mm", "dpi", "printhead_px", "width_px", "height_px"), _label_geometry()
    ))}


@app.post("/api/fit")
async def api_fit_set(request: FitRequest) -> dict:
    options = FitOptions(**request.model_dump()).normalized()
    store.save_settings({"fit": options.to_dict()})
    return {"fit": options.to_dict()}


@app.get("/api/driver")
async def api_driver() -> dict:
    """Everything the installer needs to register the Windows printer queue."""
    width_mm, height_mm, _dpi, _head, _w, _h = _label_geometry()
    port = "http://127.0.0.1:8765/ipp/print"
    model = state.printer.info.model_name
    if not model or model == "unknown":
        model = store.load_settings().get("last_model") or "B1"
    printer_name = f"Niimbot {model}".strip()
    install_command = (
        f'Add-PrinterPort -Name "{port}" -ErrorAction SilentlyContinue\n'
        f'Add-Printer -Name "{printer_name}" -DriverName "Microsoft IPP Class Driver" '
        f'-PortName "{port}"'
    )
    return {
        "port": port,
        "printer_name": printer_name,
        "driver": "Microsoft IPP Class Driver",
        "media": f"{width_mm:g} × {height_mm:g} mm",
        "install_command": install_command,
        "connected": state.connected,
    }


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


app.mount("/static", _NoCacheStatic(directory=str(WEB_DIR)), name="static")


@app.exception_handler(ProtocolError)
async def protocol_error_handler(_request, exc: ProtocolError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})

