"""SQLite storage: the single source of truth for Niibot Studio.

Everything the app knows lives in one file (``data/niibot.db``):

``consumables``  label formats — imported from the vendor app, seeded built-ins,
                 or entered by hand. Keyed by roll product barcode when known.
``labels``       saved label templates (full designer document as JSON).
``profiles``     remembered geometry per physical roll (barcode or tag UID).
``settings``     key/value application settings.
``uploads``      images used inside labels, stored as BLOBs.
``print_jobs``   print history → statistics.
``schema_meta``  schema version and migration bookkeeping.

Legacy JSON files written by earlier versions are imported once on first run.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import PaperProfile
from .paths import data_dir

DATA_DIR = data_dir()
DB_PATH = DATA_DIR / "niibot.db"
UPLOADS_CACHE = DATA_DIR / "uploads"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS consumables (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode     TEXT UNIQUE,
    sku         TEXT,
    name        TEXT,
    width_mm    REAL NOT NULL DEFAULT 0,
    height_mm   REAL NOT NULL DEFAULT 0,
    paper_type  INTEGER NOT NULL DEFAULT 1,
    rows        INTEGER,
    cols        INTEGER,
    regions     TEXT,
    capacity    INTEGER,
    color       TEXT,
    template_id INTEGER,
    localized   TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consumables_sku ON consumables(sku);
CREATE INDEX IF NOT EXISTS idx_consumables_size ON consumables(width_mm, height_mm);

CREATE TABLE IF NOT EXISTS labels (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    spec       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    spec       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    key        TEXT PRIMARY KEY,
    name       TEXT,
    width_mm   REAL NOT NULL,
    height_mm  REAL NOT NULL,
    gap_mm     REAL NOT NULL DEFAULT 2,
    label_type INTEGER NOT NULL DEFAULT 1,
    capacity   INTEGER,
    regions    TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id         TEXT PRIMARY KEY,
    filename   TEXT,
    mime       TEXT,
    data       BLOB NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    state         TEXT NOT NULL,
    rows          INTEGER,
    cols          INTEGER,
    packets       INTEGER,
    copies        INTEGER,
    density       INTEGER,
    label_type    INTEGER,
    paper_barcode TEXT,
    sku           TEXT,
    width_mm      REAL,
    height_mm     REAL,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON print_jobs(started_at);
"""


def _now() -> float:
    return time.time()


def connect() -> sqlite3.Connection:
    """Open a session that commits on success and always closes (Windows keeps locks)."""

    class _Session(sqlite3.Connection):
        def __exit__(self, exc_type, exc_value, traceback):
            try:
                return super().__exit__(exc_type, exc_value, traceback)
            finally:
                self.close()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10.0, factory=_Session)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_CACHE.mkdir(parents=True, exist_ok=True)
    init_db()


def init_db() -> None:
    with connect() as db:
        db.executescript(_SCHEMA)
        _migrate_schema(db)
        row = db.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if row is None:
            db.execute("INSERT INTO schema_meta(key, value) VALUES('version', ?)", (str(SCHEMA_VERSION),))
        migrated = db.execute("SELECT value FROM schema_meta WHERE key='legacy_imported'").fetchone()
    if migrated is None:
        _import_legacy_files()


def _migrate_schema(db: sqlite3.Connection) -> None:
    """Add columns introduced after the first release (CREATE TABLE IF NOT EXISTS skips them)."""
    profile_columns = {row["name"] for row in db.execute("PRAGMA table_info(profiles)").fetchall()}
    if "regions" not in profile_columns:
        db.execute("ALTER TABLE profiles ADD COLUMN regions TEXT")
    consumable_columns = {row["name"] for row in db.execute("PRAGMA table_info(consumables)").fetchall()}
    if "cols" not in consumable_columns:
        db.execute("ALTER TABLE consumables ADD COLUMN cols INTEGER")
    if "regions" not in consumable_columns:
        db.execute("ALTER TABLE consumables ADD COLUMN regions TEXT")


# ----------------------------------------------------------------- migration
def _import_legacy_files() -> None:
    """Move data written by the pre-SQLite version into the database."""
    imported: list[str] = []

    legacy_settings = DATA_DIR / "settings.json"
    if legacy_settings.is_file():
        data = _read_json(legacy_settings)
        if isinstance(data, dict) and data:
            save_settings(data)
            imported.append("settings.json")

    legacy_catalog = DATA_DIR / "consumables.json"
    if legacy_catalog.is_file():
        data = _read_json(legacy_catalog)
        if isinstance(data, dict):
            for barcode, entry in data.items():
                if isinstance(entry, dict):
                    upsert_consumable({**entry, "barcode": entry.get("barcode") or barcode})
            imported.append("consumables.json")

    legacy_profiles = DATA_DIR / "paper_profiles.json"
    if legacy_profiles.is_file():
        data = _read_json(legacy_profiles)
        if isinstance(data, dict):
            for key, entry in data.items():
                if isinstance(entry, dict):
                    upsert_profile(
                        PaperProfile(
                            key=key,
                            name=entry.get("name") or key,
                            width_mm=float(entry.get("width_mm") or 0),
                            height_mm=float(entry.get("height_mm") or 0),
                            gap_mm=float(entry.get("gap_mm") or 0),
                            label_type=int(entry.get("label_type") or 1),
                            capacity=entry.get("capacity"),
                        )
                    )
            imported.append("paper_profiles.json")

    labels_dir = DATA_DIR / "labels"
    if labels_dir.is_dir():
        for path in sorted(labels_dir.glob("*.json")):
            spec = _read_json(path)
            if isinstance(spec, dict):
                save_label(path.stem, spec)
        imported.append("labels/")

    uploads_dir = DATA_DIR / "uploads"
    if uploads_dir.is_dir():
        for path in sorted(uploads_dir.iterdir()):
            if path.is_file():
                try:
                    save_upload(path.name, path.read_bytes())
                except ValueError:
                    continue
        imported.append("uploads/")

    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('legacy_imported', ?)",
            (json.dumps(imported, ensure_ascii=False),),
        )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ settings
def load_settings() -> dict:
    with connect() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            result[row["key"]] = row["value"]
    return result


def save_settings(values: dict) -> None:
    now = _now()
    with connect() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )


# --------------------------------------------------------------- consumables
def _consumable_row(entry: dict[str, Any]) -> dict[str, Any]:
    localized = entry.get("localized")
    regions = entry.get("regions")
    return {
        "barcode": entry.get("barcode") or None,
        "sku": entry.get("sku") or entry.get("name") or None,
        "name": entry.get("name") or entry.get("sku") or None,
        "width_mm": float(entry.get("width_mm") or 0),
        "height_mm": float(entry.get("height_mm") or 0),
        "paper_type": int(entry.get("paper_type") or 1),
        "rows": entry.get("rows"),
        "cols": entry.get("cols"),
        "regions": json.dumps(regions, ensure_ascii=False) if regions else None,
        "capacity": entry.get("capacity"),
        "color": entry.get("color"),
        "template_id": entry.get("template_id"),
        "localized": json.dumps(localized, ensure_ascii=False) if localized else None,
        "source": entry.get("source") or "manual",
    }


def upsert_consumable(entry: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one label format. Barcode-less formats are keyed by SKU."""
    row = _consumable_row(entry)
    now = _now()
    with connect() as db:
        existing = None
        if row["barcode"]:
            existing = db.execute("SELECT id FROM consumables WHERE barcode = ?", (row["barcode"],)).fetchone()
        elif row["sku"]:
            existing = db.execute(
                "SELECT id FROM consumables WHERE barcode IS NULL AND sku = ?", (row["sku"],)
            ).fetchone()

        if existing is None:
            cursor = db.execute(
                "INSERT INTO consumables(barcode, sku, name, width_mm, height_mm, paper_type, rows, cols,"
                " regions, capacity, color, template_id, localized, source, created_at, updated_at)"
                " VALUES(:barcode, :sku, :name, :width_mm, :height_mm, :paper_type, :rows, :cols,"
                " :regions, :capacity, :color, :template_id, :localized, :source, :created_at, :updated_at)",
                {**row, "created_at": now, "updated_at": now},
            )
            identifier = cursor.lastrowid
        else:
            identifier = existing["id"]
            db.execute(
                "UPDATE consumables SET sku=:sku, name=:name, width_mm=:width_mm, height_mm=:height_mm,"
                " paper_type=:paper_type, rows=:rows, cols=:cols, regions=:regions, capacity=:capacity,"
                " color=:color, template_id=:template_id, localized=:localized, source=:source,"
                " updated_at=:updated_at"
                " WHERE id=:id",
                {**row, "id": identifier, "updated_at": now},
            )
        record = db.execute("SELECT * FROM consumables WHERE id = ?", (identifier,)).fetchone()
    return _consumable_dict(record)


def _consumable_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("localized", "regions"):
        raw = data.get(key)
        if raw:
            try:
                data[key] = json.loads(raw)
            except json.JSONDecodeError:
                data[key] = {} if key == "localized" else []
        else:
            data[key] = {} if key == "localized" else []
    return data


def list_consumables() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM consumables ORDER BY (barcode IS NULL), width_mm, height_mm, sku"
        ).fetchall()
    return [_consumable_dict(row) for row in rows]


def get_consumable_by_barcode(barcode: str | None) -> dict[str, Any] | None:
    if not barcode:
        return None
    with connect() as db:
        row = db.execute("SELECT * FROM consumables WHERE barcode = ?", (barcode,)).fetchone()
    return _consumable_dict(row) if row else None


def count_consumables() -> int:
    with connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM consumables").fetchone()[0])


def list_catalog() -> dict[str, dict[str, Any]]:
    """Barcode-keyed view of the catalog (barcode-less formats get ``sku:`` keys)."""
    catalog: dict[str, dict[str, Any]] = {}
    for entry in list_consumables():
        key = entry.get("barcode") or f"sku:{entry.get('sku')}"
        catalog[key] = entry
    return catalog


def get_catalog_entry(barcode: str | None) -> dict[str, Any] | None:
    return get_consumable_by_barcode(barcode)


def save_catalog(entries: dict[str, dict[str, Any]] | Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    items = entries.values() if isinstance(entries, dict) else entries
    for entry in items:
        upsert_consumable(entry)
    return list_catalog()


def merge_catalog(existing: dict[str, dict], incoming: dict[str, dict]) -> dict[str, dict]:
    merged = dict(existing)
    merged.update(incoming)
    return merged


# -------------------------------------------------------------------- labels
def list_labels() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT id, name, updated_at, spec FROM labels ORDER BY updated_at DESC").fetchall()
    result = []
    for row in rows:
        spec: dict[str, Any] = {}
        try:
            spec = json.loads(row["spec"])
        except json.JSONDecodeError:
            pass
        result.append(
            {
                "id": row["id"],
                "name": row["name"] or row["id"],
                "width_mm": spec.get("width_mm"),
                "height_mm": spec.get("height_mm"),
                "updated_at": row["updated_at"],
            }
        )
    return result


def load_label(label_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT spec FROM labels WHERE id = ?", (label_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["spec"])
    except json.JSONDecodeError:
        return None


def save_label(label_id: str | None, spec: Any) -> str:
    payload = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
    name = str(payload.get("name") or "Label")
    identifier = (label_id or f"{slugify(name)}-{uuid.uuid4().hex[:4]}").strip()
    now = _now()
    with connect() as db:
        db.execute(
            "INSERT INTO labels(id, name, spec, created_at, updated_at) VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, updated_at=excluded.updated_at",
            (identifier, name, json.dumps(payload, ensure_ascii=False), now, now),
        )
    return identifier


def delete_label(label_id: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM labels WHERE id = ?", (label_id,))


# ----------------------------------------------------------------- templates
def list_templates() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT id, name, updated_at, spec FROM templates ORDER BY name COLLATE NOCASE").fetchall()
    result = []
    for row in rows:
        spec: dict[str, Any] = {}
        try:
            spec = json.loads(row["spec"])
        except json.JSONDecodeError:
            pass
        result.append(
            {
                "id": row["id"],
                "name": row["name"] or row["id"],
                "width_mm": spec.get("width_mm"),
                "height_mm": spec.get("height_mm"),
                "spec": spec,
                "updated_at": row["updated_at"],
            }
        )
    return result


def load_template(template_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT spec FROM templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["spec"])
    except json.JSONDecodeError:
        return None


def save_template(template_id: str | None, name: str, spec: Any) -> str:
    payload = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
    payload["name"] = name or payload.get("name") or "Szablon"
    identifier = (template_id or f"tpl-{slugify(name)}-{uuid.uuid4().hex[:4]}").strip()
    now = _now()
    with connect() as db:
        db.execute(
            "INSERT INTO templates(id, name, spec, created_at, updated_at) VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, updated_at=excluded.updated_at",
            (identifier, payload["name"], json.dumps(payload, ensure_ascii=False), now, now),
        )
    return identifier


def delete_template(template_id: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM templates WHERE id = ?", (template_id,))


# ------------------------------------------------------------------ profiles
def _profile_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw = data.pop("regions", None)
    try:
        data["regions"] = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        data["regions"] = []
    return data


def list_profiles() -> dict[str, dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM profiles").fetchall()
    return {row["key"]: _profile_row(row) for row in rows}


def get_profile(key: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM profiles WHERE key = ?", (key,)).fetchone()
    return _profile_row(row) if row else None


def upsert_profile(profile: PaperProfile) -> dict[str, Any]:
    regions = json.dumps([region.to_dict() for region in profile.regions], ensure_ascii=False) if profile.regions else None
    with connect() as db:
        db.execute(
            "INSERT INTO profiles(key, name, width_mm, height_mm, gap_mm, label_type, capacity, regions, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET name=excluded.name, width_mm=excluded.width_mm,"
            " height_mm=excluded.height_mm, gap_mm=excluded.gap_mm, label_type=excluded.label_type,"
            " capacity=excluded.capacity, regions=excluded.regions, updated_at=excluded.updated_at",
            (
                profile.key,
                profile.name,
                profile.width_mm,
                profile.height_mm,
                profile.gap_mm,
                profile.label_type,
                profile.capacity,
                regions,
                _now(),
            ),
        )
    return get_profile(profile.key) or profile.to_dict()


def save_profile(profile: PaperProfile) -> dict[str, Any]:
    return upsert_profile(profile)


def delete_profile(key: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM profiles WHERE key = ?", (key,))


def profile_key(barcode: str | None, uid: str | None) -> str | None:
    if barcode:
        return f"bc:{barcode}"
    if uid:
        return f"uid:{uid}"
    return None


# ------------------------------------------------------------------- uploads
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def save_upload(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported image format: {suffix or 'none'}")
    identifier = uuid.uuid4().hex[:12] + suffix
    with connect() as db:
        db.execute(
            "INSERT INTO uploads(id, filename, mime, data, created_at) VALUES(?, ?, ?, ?, ?)",
            (identifier, filename, _MIME.get(suffix, "application/octet-stream"), payload, _now()),
        )
    return identifier


def upload_bytes(image_id: str) -> tuple[bytes, str] | None:
    with connect() as db:
        row = db.execute("SELECT data, mime FROM uploads WHERE id = ?", (Path(image_id).name,)).fetchone()
    if not row:
        return None
    return bytes(row["data"]), row["mime"] or "application/octet-stream"


def upload_path(image_id: str) -> Path | None:
    """Materialise a stored image to a cache file (the renderer works on paths)."""
    found = upload_bytes(image_id)
    if found is None:
        return None
    payload, _mime = found
    UPLOADS_CACHE.mkdir(parents=True, exist_ok=True)
    path = UPLOADS_CACHE / Path(image_id).name
    if not path.is_file() or path.stat().st_size != len(payload):
        path.write_bytes(payload)
    return path


# ---------------------------------------------------------------- print jobs
def record_print_job(
    *,
    job_id: str,
    state: str,
    started_at: float,
    finished_at: float | None = None,
    rows: int | None = None,
    cols: int | None = None,
    packets: int | None = None,
    copies: int | None = None,
    density: int | None = None,
    label_type: int | None = None,
    paper_barcode: str | None = None,
    sku: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    error: str | None = None,
) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO print_jobs(job_id, started_at, finished_at, state, rows, cols, packets, copies,"
            " density, label_type, paper_barcode, sku, width_mm, height_mm, error)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                started_at,
                finished_at,
                state,
                rows,
                cols,
                packets,
                copies,
                density,
                label_type,
                paper_barcode,
                sku,
                width_mm,
                height_mm,
                error,
            ),
        )


def print_statistics(recent_limit: int = 10) -> dict[str, Any]:
    with connect() as db:
        totals = db.execute(
            "SELECT COUNT(*) AS jobs,"
            " COALESCE(SUM(CASE WHEN state='done' THEN copies ELSE 0 END), 0) AS labels,"
            " COALESCE(SUM(CASE WHEN state='done' THEN 1 ELSE 0 END), 0) AS done,"
            " COALESCE(SUM(CASE WHEN state='error' THEN 1 ELSE 0 END), 0) AS failed,"
            " MAX(CASE WHEN state='done' THEN finished_at END) AS last_ok"
            " FROM print_jobs"
        ).fetchone()
        by_sku = db.execute(
            "SELECT COALESCE(sku, paper_barcode, 'unknown') AS sku,"
            " COALESCE(SUM(copies), 0) AS labels, COUNT(*) AS jobs"
            " FROM print_jobs WHERE state='done' GROUP BY 1 ORDER BY labels DESC LIMIT 8"
        ).fetchall()
        recent = db.execute(
            "SELECT job_id, started_at, finished_at, state, copies, width_mm, height_mm, sku, error"
            " FROM print_jobs ORDER BY id DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()
    return {
        "jobs": totals["jobs"],
        "labels": totals["labels"],
        "done": totals["done"],
        "failed": totals["failed"],
        "last_ok": totals["last_ok"],
        "by_sku": [dict(row) for row in by_sku],
        "recent": [dict(row) for row in recent],
        "formats": count_consumables(),
        "templates": len(list_labels()),
    }


# -------------------------------------------------------------------- helpers
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def slugify(name: str) -> str:
    cleaned = _SAFE.sub("_", (name or "").strip())
    return (cleaned.strip("._ ") or "label")[:60]
