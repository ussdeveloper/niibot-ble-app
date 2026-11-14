"""Local web application: printer control + label designer."""

from __future__ import annotations

import asyncio
import base64
import io
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
from .bitmap import encode_image
from .ble import BleLink
from .consumables import Consumable, builtin_formats, scan_vendor_catalog
from .fit import FitOptions, fit_to_label
from .models import MODELS, PAPER_PRESETS, PaperProfile, Region, ZoneShape
from .paths import resource_dir
from .printer import NiimbotPrinter
from .protocol import LABEL_TYPE_NAMES, ProtocolError
from .render import available_fonts, font_catalog, preview_payload, render_label
from .spec import BARCODE_FORMATS, ELEMENT_TYPES, TEMPLATES, Element, LabelSpec, default_spec
from .version import APP_NAME, APP_TAGLINE, __version__

log = logging.getLogger(__name__)
WEB_DIR = resource_dir() / "web"


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

    def to_dict(self) -> dict:
        return asdict(self)


class AppState:
    def __init__(self) -> None:
        self.printer = NiimbotPrinter(BleLink())
        self.lock = asyncio.Lock()
        self.job: Job | None = None
        self.job_task: asyncio.Task | None = None
        self.auto_connect_task: asyncio.Task | None = None
        self.auto_status: str = "idle"
        self.last_scan: list[dict] = []

    @property
    def connected(self) -> bool:
        return self.printer.connected


state = AppState()


async def _shutdown() -> None:
    if state.job_task and not state.job_task.done():
        state.job_task.cancel()
    try:
        await state.printer.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.ensure_dirs()
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
    # Remembered printer: look for it in the background and connect when it is in range.
    auto_task = asyncio.create_task(_auto_connect_loop())
    state.auto_connect_task = auto_task
    yield
    auto_task.cancel()
    await _shutdown()


app = FastAPI(title="Niibot Studio", lifespan=lifespan)


# ---------------------------------------------------------------- models API
class ScanRequest(BaseModel):
    timeout: float = 6.0
    show_all: bool = False


class ConnectRequest(BaseModel):
    address: str


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
def _current_profile() -> dict | None:
    paper = state.printer.paper
    key = store.profile_key(paper.barcode, paper.uuid)
    if not key:
        return None
    profile = store.get_profile(key)
    if profile:
        profile = dict(profile)
        profile["key"] = key
    return profile


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
    return {
        "connected": state.connected,
        "link": {"address": state.printer.link.address, "name": state.printer.link.name},
        "info": asdict(state.printer.info),
        "paper": paper,
        "profile": _current_profile(),
        "job": state.job.to_dict() if state.job else None,
        "last_address": settings.get("last_address"),
        "last_error": state.printer.last_error or state.printer.link.last_error,
    }


@app.get("/api/state")
async def api_state() -> dict:
    return _state_payload()


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
        "labels": store.list_labels(),
        "profiles": list(store.list_profiles().values()),
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
AUTO_CONNECT_RETRY_S = 25.0


async def _auto_connect_loop() -> None:
    """Connect to the remembered printer whenever it appears in range."""
    await asyncio.sleep(2.5)
    while True:
        try:
            settings = store.load_settings()
            address = (settings.get("last_address") or "").strip()
            enabled = str(settings.get("auto_connect", "1")) not in {"0", "false", "False", ""}
            if enabled and address and not state.connected:
                state.auto_status = f"Looking for {address}\u2026"
                in_range = await BleLink.find_by_address(address, timeout=6.0)
                if in_range:
                    state.auto_status = f"Connecting to {address}\u2026"
                    async with state.lock:
                        await state.printer.connect(address)
                        store.save_settings({"last_address": address})
                        try:
                            await state.printer.refresh()
                        except Exception:  # pragma: no cover - status is best effort
                            pass
                    state.auto_status = "connected"
                    log.info("Auto-connected to %s", address)
                    await asyncio.sleep(AUTO_CONNECT_RETRY_S)
                    continue
                state.auto_status = "not in range"
            else:
                state.auto_status = "connected" if state.connected else "idle"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            state.auto_status = f"auto-connect failed: {exc}"
            log.debug("Automatic connect failed: %s", exc)
        await asyncio.sleep(AUTO_CONNECT_RETRY_S)


@app.post("/api/auto-connect")
async def api_auto_connect() -> dict:
    """Trigger an immediate attempt (the UI calls this right after start-up)."""
    settings = store.load_settings()
    address = (settings.get("last_address") or "").strip()
    if not address:
        raise HTTPException(status_code=409, detail="No printer has been paired yet.")
    if state.connected:
        return _state_payload()
    if not await BleLink.find_by_address(address, timeout=8.0):
        raise HTTPException(status_code=404, detail=f"Printer {address} is not in range.")
    async with state.lock:
        await state.printer.connect(address)
        try:
            await state.printer.refresh()
        except Exception:  # pragma: no cover
            pass
    state.auto_status = "connected"
    return _state_payload()


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


# ------------------------------------------------------------------ devices
@app.post("/api/scan")
async def api_scan(request: ScanRequest) -> dict:
    try:
        devices = await BleLink.scan(timeout=max(2.0, min(30.0, request.timeout)), show_all=request.show_all)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc
    state.last_scan = [device.to_dict() for device in devices]
    return {"devices": state.last_scan}


@app.post("/api/connect")
async def api_connect(request: ConnectRequest) -> dict:
    async with state.lock:
        try:
            await state.printer.connect(request.address)
        except Exception as exc:
            state.printer.last_error = str(exc)
            raise HTTPException(status_code=502, detail=f"Could not connect: {exc}") from exc
        store.save_settings({"last_address": request.address})
        if state.printer.info.model_name and state.printer.info.model_name != "unknown":
            store.save_settings({"last_model": state.printer.info.model_name})
        try:
            await state.printer.refresh()
        except Exception as exc:  # pragma: no cover - status is best effort
            log.debug("Reading the status after connecting failed: %s", exc)
    return _state_payload()

@app.post("/api/disconnect")
async def api_disconnect() -> dict:
    async with state.lock:
        await state.printer.disconnect()
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

    key = store.profile_key(paper.barcode, paper.uuid)
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
            )
        )
    return {
        "detected": True,
        "geometry": geometry.to_dict(),
        "profile": stored,
        "state": _state_payload(),
    }


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
def _geometry() -> tuple[int, int]:
    info = state.printer.info
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


@app.post("/api/preview")
async def api_preview(request: PreviewRequest) -> dict:
    dpi, printhead = _geometry()
    try:
        return preview_payload(_with_mirror(request.spec, request.mirror), dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    if image.width > limit:
        ratio = limit / image.width
        image = image.resize((limit, max(1, int(image.height * ratio))), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"png": base64.b64encode(buffer.getvalue()).decode("ascii"), "width_px": image.width, "height_px": image.height}


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
        element_box["x"] -= box[0]
        element_box["y"] -= box[1]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width_px": image.width,
        "height_px": image.height,
        "element_box": element_box,
    }


@app.post("/api/render.png")
async def api_render_png(request: PreviewRequest) -> Response:
    dpi, printhead = _geometry()
    try:
        rendered = render_label(_with_mirror(request.spec, request.mirror), dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    buffer = io.BytesIO()
    rendered.image.convert("L").save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/api/labels")
async def api_labels() -> dict:
    return {"labels": store.list_labels()}


@app.get("/api/labels/{label_id}")
async def api_label_get(label_id: str) -> dict:
    data = store.load_label(label_id)
    if not data:
        raise HTTPException(status_code=404, detail="Label not found.")
    return {"id": label_id, "spec": data}


@app.post("/api/labels")
async def api_label_save(request: SaveLabelRequest) -> dict:
    label_id = store.save_label(request.id, request.spec)
    return {"id": label_id, "labels": store.list_labels()}


@app.delete("/api/labels/{label_id}")
async def api_label_delete(label_id: str) -> dict:
    store.delete_label(label_id)
    return {"labels": store.list_labels()}


# ---------------------------------------------------------------- templates
@app.get("/api/templates")
async def api_templates() -> dict:
    return {"templates": store.list_templates()}


@app.get("/api/templates/{template_id}")
async def api_template_get(template_id: str) -> dict:
    data = store.load_template(template_id)
    if not data:
        raise HTTPException(status_code=404, detail="Template not found.")
    return {"id": template_id, "spec": data}


@app.post("/api/templates")
async def api_template_save(request: SaveTemplateRequest) -> dict:
    name = request.name.strip() or request.spec.name or "Template"
    template_id = store.save_template(request.id, name, request.spec)
    return {"id": template_id, "templates": store.list_templates()}


@app.delete("/api/templates/{template_id}")
async def api_template_delete(template_id: str) -> dict:
    store.delete_template(template_id)
    return {"templates": store.list_templates()}


# ----------------------------------------------------------------- profiles
@app.get("/api/profiles")
async def api_profiles() -> dict:
    return {"profiles": list(store.list_profiles().values())}


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
    entry = {
        "barcode": request.barcode,
        "name": request.name or request.barcode,
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
    profile_key_value = store.profile_key(request.barcode, None)
    if profile_key_value:
        store.save_profile(
            PaperProfile(
                key=profile_key_value,
                name=request.name or f"{request.width_mm:g} × {request.height_mm:g} mm",
                width_mm=request.width_mm,
                height_mm=request.height_mm,
                gap_mm=request.gap_mm,
                label_type=request.label_type,
                capacity=request.capacity,
                regions=regions,
            )
        )
    log.info(
        "Roll %s remembered as %g × %g mm (%d rows, %d cells drawn)",
        request.barcode,
        request.width_mm,
        request.height_mm,
        request.rows,
        len(regions),
    )
    return _state_payload()


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
    stored = store.save_profile(profile)
    return {"profile": stored, "profiles": list(store.list_profiles().values())}


@app.delete("/api/profiles/{key}")
async def api_profile_delete(key: str) -> dict:
    store.delete_profile(key)
    return {"profiles": list(store.list_profiles().values())}


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
    )
    state.job = job
    state.job_task = asyncio.create_task(_run_print(job, spec, copies, density, label_type))
    return {"job": job.to_dict()}


async def _run_print(job: Job, spec: LabelSpec, copies: int, density: int | None, label_type: int | None) -> None:
    try:
        dpi, printhead = _geometry()
        rendered = render_label(spec, dpi, printhead, store.upload_path)
        encoded = encode_image(rendered.image)
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
                encoded = encode_image(rendered.image)
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
        )
    except Exception:  # pragma: no cover - statistics must never break printing
        log.exception("Could not save the print statistics")


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
    image = ipp_mod.decode_document(payload, mime)
    if image is None:
        return False, f"unsupported document format ({mime or 'none'})"

    width_mm, height_mm, dpi, head_px, width_px, height_px = _label_geometry()
    bitmap = fit_to_label(image, width_px, height_px, dpi, _fit_options())
    encoded = encode_image(bitmap)
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

