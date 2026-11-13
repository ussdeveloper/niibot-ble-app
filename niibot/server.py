"""Local web application: printer control + label designer."""

from __future__ import annotations

import asyncio
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
from pydantic import BaseModel, Field

from . import ipp as ipp_mod
from . import sheet, store
from .bitmap import encode_image
from .ble import BleLink
from .consumables import Consumable, builtin_formats, scan_vendor_catalog
from .fit import FitOptions, fit_to_label
from .models import MODELS, PAPER_PRESETS, PaperProfile
from .paths import resource_dir
from .printer import NiimbotPrinter
from .protocol import LABEL_TYPE_NAMES, ProtocolError
from .render import available_fonts, font_catalog, preview_payload, render_label
from .spec import BARCODE_FORMATS, ELEMENT_TYPES, TEMPLATES, Element, LabelSpec, default_spec
from .version import APP_NAME, APP_TAGLINE, __version__

log = logging.getLogger(__name__)
WEB_DIR = resource_dir() / "web"

@dataclass
class Job:
    id: str
    state: str = "running"
    progress: float = 0.0
    message: str = "Renderowanie etykiety…"
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
    # Built-in formats make the picker usable without the vendor app installed.
    for entry in builtin_formats():
        store.upsert_consumable(entry)
    # Then fold in whatever the vendor app already cached on this machine.
    try:
        entries, _files = scan_vendor_catalog()
        for consumable in entries.values():
            store.upsert_consumable(consumable.to_dict())
        if entries:
            log.info("Zaimportowano %d formatów z aplikacji NIIMBOT", len(entries))
    except Exception as exc:  # pragma: no cover - optional convenience
        log.debug("Import katalogu producenta nie powiódł się: %s", exc)
    yield
    await _shutdown()


app = FastAPI(title="Niibot Studio", lifespan=lifespan)


# ---------------------------------------------------------------- models API
class ScanRequest(BaseModel):
    timeout: float = 6.0
    show_all: bool = False


class ConnectRequest(BaseModel):
    address: str


class MirrorRequest(BaseModel):
    """Fill regions 2..N with a copy (or vertical mirror) of region 1."""

    regions: int = 1
    flip: bool = False


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


class SaveLabelRequest(BaseModel):
    id: str | None = None
    spec: LabelSpec


class SaveTemplateRequest(BaseModel):
    id: str | None = None
    name: str
    spec: LabelSpec


class ProfileRequest(BaseModel):
    key: str
    name: str = ""
    width_mm: float = Field(gt=0, le=200)
    height_mm: float = Field(gt=0, le=400)
    gap_mm: float = 2.0
    label_type: int = 1
    capacity: int | None = None


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
@app.post("/api/scan")
async def api_scan(request: ScanRequest) -> dict:
    try:
        devices = await BleLink.scan(timeout=max(2.0, min(30.0, request.timeout)), show_all=request.show_all)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Skanowanie nie powiodło się: {exc}") from exc
    state.last_scan = [device.to_dict() for device in devices]
    return {"devices": state.last_scan}


@app.post("/api/connect")
async def api_connect(request: ConnectRequest) -> dict:
    async with state.lock:
        try:
            await state.printer.connect(request.address)
        except Exception as exc:
            state.printer.last_error = str(exc)
            raise HTTPException(status_code=502, detail=f"Nie udało się połączyć: {exc}") from exc
        store.save_settings({"last_address": request.address})
        if state.printer.info.model_name and state.printer.info.model_name != "nieznany":
            store.save_settings({"last_model": state.printer.info.model_name})
        try:
            await state.printer.refresh()
        except Exception as exc:  # pragma: no cover - status is best effort
            log.debug("Odczyt statusu po połączeniu nie powiódł się: %s", exc)
    return _state_payload()

@app.post("/api/disconnect")
async def api_disconnect() -> dict:
    async with state.lock:
        await state.printer.disconnect()
    return _state_payload()


@app.post("/api/refresh")
async def api_refresh() -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
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
                "Ten model nie raportuje formatu przez Bluetooth (B1 nie odpowiada na 0x59). "
                "Podaj wymiary raz — zapamiętam je dla kodu tej rolki i następne rolki z tej samej "
                "serii ustawią się same."
            )
        else:
            detail = "Drukarka nie podała formatu — wpisz wymiary ręcznie."
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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
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
    if mirror is None or mirror.regions < 2:
        return spec
    return sheet.apply_mirror(spec, mirror.regions, mirror.flip)


@app.post("/api/preview")
async def api_preview(request: PreviewRequest) -> dict:
    dpi, printhead = _geometry()
    try:
        return preview_payload(_with_mirror(request.spec, request.mirror), dpi, printhead, store.upload_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        raise HTTPException(status_code=404, detail="Nie znaleziono etykiety.")
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
        raise HTTPException(status_code=500, detail=f"Nie udało się odczytać cache aplikacji NIIMBOT: {exc}") from exc
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


@app.post("/api/profiles")
async def api_profile_save(request: ProfileRequest) -> dict:
    profile = PaperProfile(
        key=request.key,
        name=request.name or f"{request.width_mm:g} × {request.height_mm:g} mm",
        width_mm=request.width_mm,
        height_mm=request.height_mm,
        gap_mm=request.gap_mm,
        label_type=request.label_type,
        capacity=request.capacity,
    )
    store.save_profile(profile)
    return {"profile": profile.to_dict(), "profiles": list(store.list_profiles().values())}


@app.delete("/api/profiles/{key}")
async def api_profile_delete(key: str) -> dict:
    store.delete_profile(key)
    return {"profiles": list(store.list_profiles().values())}


# ------------------------------------------------------------------ uploads
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if len(payload) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Plik jest za duży (limit 8 MB).")
    try:
        image_id = store.save_upload(file.filename or "image.png", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": image_id, "url": f"/api/uploads/{image_id}"}


@app.get("/api/uploads/{image_id}")
async def api_upload_get(image_id: str) -> Response:
    found = store.upload_bytes(image_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Brak pliku.")
    payload, mime = found
    return Response(content=payload, media_type=mime)


# -------------------------------------------------------------------- print
def _test_spec() -> LabelSpec:
    width = 48.0
    height = 30.0
    spec = LabelSpec(
        name="Test drukarki",
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
                text="Gęstość 3 · 203 dpi" if state.printer.info.dpi == 203 else "Gęstość 3",
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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
    _check_ready()
    return await _start_job(_test_spec(), copies=1, density=None, label_type=None, title="Wydruk testowy")


@app.post("/api/print")
async def api_print(request: PrintRequest) -> dict:
    if not state.connected:
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="Trwa już wydruk — poczekaj lub anuluj.")
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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
    _check_ready()
    if state.job and state.job.state == "running":
        raise HTTPException(status_code=409, detail="Trwa już wydruk — poczekaj lub anuluj.")

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
        raise HTTPException(status_code=400, detail="Arkusz nie ma żadnych wierszy do wydruku.")

    density = request.density if request.density is not None else request.spec.density
    label_type = request.label_type
    if label_type is None:
        paper = state.printer.paper
        label_type = paper.label_type if paper.label_type_source == "rfid" else request.spec.label_type
    return await _start_sheet_job(specs, copies=request.copies, density=density, label_type=label_type)


def _check_ready() -> None:
    paper = state.printer.paper
    if paper.lid_closed is False:
        raise HTTPException(status_code=409, detail="Pokrywa drukarki jest otwarta.")
    if paper.paper_inserted is False:
        raise HTTPException(status_code=409, detail="W drukarce nie ma papieru.")


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
        message=title or "Renderowanie etykiety…",
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
        job.message = "Wysyłanie danych do drukarki…"

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
        job.message = "Wydrukowano."
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.message = "Anulowano."
        raise
    except ProtocolError as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = str(exc)
        await state.printer.cancel()
    except Exception as exc:  # pragma: no cover - unexpected
        log.exception("Wydruk nie powiódł się")
        job.state = "error"
        job.error = str(exc)
        job.message = f"Błąd: {exc}"
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
        message=f"Arkusz: {len(specs)} etykiet — renderowanie…",
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
                label = f"Arkusz: wiersz {index} / {len(specs)}"
                job.message = f"{label} — wysyłanie…"
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
        job.message = f"Wydrukowano {total} etykiet z arkusza."
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.message = "Anulowano."
        raise
    except ProtocolError as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = str(exc)
        await state.printer.cancel()
    except Exception as exc:  # pragma: no cover - unexpected
        log.exception("Wydruk arkusza nie powiódł się")
        job.state = "error"
        job.error = str(exc)
        job.message = f"Błąd: {exc}"
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
        log.exception("Nie udało się zapisać statystyki wydruku")


@app.get("/api/job")
async def api_job() -> dict:
    return {"job": state.job.to_dict() if state.job else None}


@app.post("/api/cancel")
async def api_cancel() -> dict:
    await state.printer.cancel()
    if state.job and state.job.state == "running":
        state.job.state = "cancelled"
        state.job.message = "Anulowano."
        state.job.finished = time.time()
    if state.job_task and not state.job_task.done():
        state.job_task.cancel()
    return {"job": state.job.to_dict() if state.job else None}


# -------------------------------------------------------------------- pages
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


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
        raise HTTPException(status_code=409, detail="Drukarka nie jest połączona.")
    image = ipp_mod.decode_document(payload, mime)
    if image is None:
        return False, f"nieobsługiwany format dokumentu ({mime or 'brak'})"

    width_mm, height_mm, dpi, head_px, width_px, height_px = _label_geometry()
    bitmap = fit_to_label(image, width_px, height_px, dpi, _fit_options())
    encoded = encode_image(bitmap)
    paper = state.printer.paper
    catalog = store.get_catalog_entry(paper.barcode) or {}
    job = Job(
        id=uuid.uuid4().hex[:8],
        message="Wydruk z Windows…",
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
        job.message = "Wydrukowano z Windows."
        return True, ""
    except Exception as exc:
        job.state = "error"
        job.error = str(exc)
        job.message = f"Błąd: {exc}"
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
            log.exception("Nie udało się zapisać statystyki wydruku")


@app.post("/ipp/print")
async def ipp_print(request: Request) -> Response:
    """IPP endpoint used by the Windows "Microsoft IPP Class Driver" queue."""
    body = await request.body()
    try:
        ipp_request = ipp_mod.parse_request(body)
    except ValueError as exc:
        log.warning("Nieprawidłowe żądanie IPP: %s", exc)
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
            log.exception("Wydruk z Windows nie powiódł się")
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

    log.info("Nieobsługiwana operacja IPP: 0x%04X", operation)
    return Response(
        content=ipp_mod.build_simple_response(
            ipp_request, ipp_mod.STATUS_NOT_POSSIBLE, message="operacja nieobsługiwana"
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
    if not model or model == "nieznany":
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


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.exception_handler(ProtocolError)
async def protocol_error_handler(_request, exc: ProtocolError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})
