"""Import label formats from the vendor NIIMBOT app.

The official app resolves a roll in two steps: it reads the RFID tag through the
printer (same ``0x1A`` response we get), then asks Niimbot's cloud about that tag
serial (``GET /api/rfid/getRfid/v2?serialNumbers[]=…``) and caches the answer in
the Electron renderer's ``localStorage`` as:

* ``ConsumablesInfo-<tag serial>``  – roll counters (allowNum / actualNum / used)
* ``ConsumablesTemplate-<roll barcode>`` – the label format: ``width``, ``height``,
  ``paperType``, SKU name (e.g. ``T40*15/2R-460WHITE``) and the multilingual names.

This module reads that local cache. It never talks to Niimbot's servers: the data
is already on this machine, produced by the app the user installed and runs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Default install locations of the vendor app's renderer storage.
VENDOR_ROOTS: tuple[Path, ...] = (
    Path(r"C:\Program Files\NIIMBOT\resources\app\userData\Local Storage\leveldb"),
    Path(r"C:\Program Files (x86)\NIIMBOT\resources\app\userData\Local Storage\leveldb"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "NIIMBOT" / "Local Storage" / "leveldb",
    Path(os.environ.get("APPDATA", "")) / "NIIMBOT" / "Local Storage" / "leveldb",
)

TEMPLATE_KEY = "ConsumablesTemplate-"
INFO_KEY = "ConsumablesInfo-"

#: ``T40*15/2R-460WHITE`` → 40 mm × 15 mm, 2 rows, 460 labels, WHITE
#: ``T10*20/4L-1200WHITE`` → 10 mm × 20 mm labels, 4 side by side (a 40 mm page)
#: ``TT40*60-125transparent`` → thermal-transfer roll, the extra ``T`` is packaging.
SKU_RE = re.compile(
    r"^[Tt]{1,2}\s*(?P<w>\d+(?:[.,]\d+)?)\s*\*\s*(?P<h>\d+(?:[.,]\d+)?)"
    r"(?:\s*/\s*(?P<count>\d)\s*(?P<axis>[RrLl]))?"
    r"(?:\s*-\s*(?P<labels>\d+))?"
    r"(?P<tail>.*)$"
)


@dataclass
class Consumable:
    """A label format known for a specific roll product barcode."""

    barcode: str
    name: str = ""
    width_mm: float = 0.0
    height_mm: float = 0.0
    paper_type: int = 1
    rows: int | None = None
    cols: int | None = None
    regions: list[dict] = field(default_factory=list)
    capacity: int | None = None
    color: str | None = None
    template_id: int | None = None
    localized: dict[str, str] = field(default_factory=dict)
    source: str = "vendor-cache"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_usable(self) -> bool:
        return self.width_mm > 0 and self.height_mm > 0


def parse_sku_name(name: str) -> dict:
    """Decode a Niimbot SKU name such as ``T50*30`` or ``T40*15/2R-460WHITE``.

    ``rows`` means regions stacked vertically (``/2R``), ``cols`` regions side by
    side (``/4L``). The returned width/height describe **one** region.
    """
    match = SKU_RE.match((name or "").strip())
    if not match:
        return {}
    result: dict = {}
    try:
        result["width_mm"] = float(match.group("w").replace(",", "."))
        result["height_mm"] = float(match.group("h").replace(",", "."))
    except (TypeError, ValueError):
        return {}
    count = match.group("count")
    if count:
        number = max(1, int(count))
        if (match.group("axis") or "").lower() == "r":
            result["rows"] = number
        else:
            result["cols"] = number
    if match.group("labels"):
        result["capacity"] = int(match.group("labels"))
    tail = (match.group("tail") or "").strip()
    color = re.match(r"[A-Za-z+ ]+", tail)
    if color and color.group().strip():
        result["color"] = color.group().strip()
    return result


def sku_regions(width_mm: float, height_mm: float, rows: int = 1, cols: int = 1) -> list[dict]:
    """Build the printable cells of a grid roll (rows stacked, columns side by side)."""
    rows = max(1, int(rows or 1))
    cols = max(1, int(cols or 1))
    if rows == 1 and cols == 1:
        return []
    cells: list[dict] = []
    for row in range(rows):
        for col in range(cols):
            cells.append(
                {
                    "shape": "rect",
                    "x": round(col * width_mm, 2),
                    "y": round(row * height_mm, 2),
                    "w": round(width_mm, 2),
                    "h": round(height_mm, 2),
                    "label": f"R{row + 1}C{col + 1}",
                }
            )
    return cells


#: Standard Niimbot roll formats, so the picker works without the vendor app.
#: Symbols follow the manufacturer's convention: ``T<width>*<height>[/NR]``.
BUILTIN_SKUS: tuple[str, ...] = (
    "T50*80",
    "T50*50",
    "T50*40",
    "T50*30",
    "T40*60",
    "T40*40",
    "T40*30",
    "T40*20",
    "T40*15",
    "T40*15/2R",
    "T33*15/2R",
    "T30*45",
    "T30*30",
    "T30*20",
    "T30*15",
    "T25*10",
    "T20*40",
    "T20*30",
    "T15*30",
    "T15*50",
    "T12*50",
    "T12*40",
    "T12*22",
    "T70*30",
    "T70*50",
)

#: Formats whose symbol carries an extra tail length (cable flags).
BUILTIN_EXTRA: tuple[dict, ...] = (
    {"sku": "T12.5*74+35", "width_mm": 12.5, "height_mm": 74},
    {"sku": "T30*45+50", "width_mm": 30.0, "height_mm": 45},
)


def builtin_formats() -> list[dict]:
    """Normalised built-in formats ready for :func:`niibot.store.upsert_consumable`."""
    formats: list[dict] = []
    for sku in BUILTIN_SKUS:
        parsed = parse_sku_name(sku)
        if not parsed:
            continue
        height = parsed["height_mm"]
        rows = parsed.get("rows")
        if rows and rows > 1:
            # One template spans every row of the label (e.g. 2 × 15 mm = 30 mm).
            height = round(height * rows, 1)
        formats.append(
            {
                "barcode": None,
                "sku": sku,
                "name": sku,
                "width_mm": parsed["width_mm"],
                "height_mm": height,
                "paper_type": 1,
                "rows": rows,
                "source": "builtin",
            }
        )
    for item in BUILTIN_EXTRA:
        formats.append({**item, "name": item["sku"], "paper_type": 1, "source": "builtin"})
    return formats


# ------------------------------------------------------------------ extraction
def _balanced_json(text: str) -> str | None:
    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : index + 1]
    return None


def _decode_record(raw: bytes, offset: int) -> dict | None:
    """Decode the JSON value that follows ``offset``.

    Chromium stores ``localStorage`` values either as UTF-8 (Latin-1 safe) or as
    UTF-16LE when the record contains non-Latin characters — the template records
    carry Chinese names, so both cases occur in the same log.
    """
    window = raw[offset : offset + 200]
    utf16_at = window.find(b"{\x00")
    utf8_at = window.find(b"{")
    candidates: list[tuple[int, str]] = []
    if utf16_at >= 0:
        candidates.append((utf16_at, "utf-16-le"))
    if utf8_at >= 0:
        candidates.append((utf8_at, "utf-8"))
    candidates.sort()
    for start, encoding in candidates:
        text = raw[offset + start : offset + start + 20000].decode(encoding, errors="replace")
        payload = _balanced_json(text)
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def read_records(directory: Path) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """Return ``(templates, infos, scanned_files)`` found in a LevelDB directory."""
    templates: dict[str, dict] = {}
    infos: dict[str, dict] = {}
    scanned: list[str] = []
    if not directory.is_dir():
        return templates, infos, scanned

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".log", ".ldb", ""}:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        scanned.append(path.name)

        for key, target in ((TEMPLATE_KEY, templates), (INFO_KEY, infos)):
            needle = key.encode("ascii")
            index = raw.find(needle)
            while index >= 0:
                identifier = _read_key_tail(raw, index + len(needle))
                record = _decode_record(raw, index + len(needle))
                if record and identifier:
                    target[identifier] = record
                index = raw.find(needle, index + len(needle))
    return templates, infos, scanned


def _read_key_tail(raw: bytes, offset: int) -> str:
    """Read the barcode/serial that follows the key prefix (ASCII, NUL terminated)."""
    end = offset
    while end < len(raw) and end - offset < 64:
        byte = raw[end]
        if byte == 0 or not (0x30 <= byte <= 0x39 or 0x41 <= byte <= 0x5A or 0x61 <= byte <= 0x7A):
            break
        end += 1
    return raw[offset:end].decode("ascii", "replace")


# ----------------------------------------------------------------- conversion
def template_to_consumable(barcode: str, template: dict, info: dict | None) -> Consumable:
    name = str(template.get("name") or "").strip()
    sku = parse_sku_name(name)

    width = template.get("width")
    height = template.get("height")
    try:
        width_mm = float(width) if width is not None else float(sku.get("width_mm") or 0)
    except (TypeError, ValueError):
        width_mm = float(sku.get("width_mm") or 0)
    try:
        height_mm = float(height) if height is not None else float(sku.get("height_mm") or 0)
    except (TypeError, ValueError):
        height_mm = float(sku.get("height_mm") or 0)

    capacity = sku.get("capacity")
    if info:
        capacity = info.get("actualNum") or capacity

    localized = {
        str(entry.get("languageCode")): str(entry.get("name"))
        for entry in template.get("names") or []
        if isinstance(entry, dict) and entry.get("name")
    }

    paper_type = template.get("paperType")
    rows = int(sku.get("rows") or 1)
    cols = int(sku.get("cols") or 1)
    return Consumable(
        barcode=barcode,
        name=name,
        width_mm=width_mm,
        height_mm=height_mm,
        paper_type=int(paper_type) if isinstance(paper_type, (int, float, str)) and str(paper_type).isdigit() else 1,
        rows=rows,
        cols=cols,
        regions=sku_regions(
            width_mm / cols if cols else width_mm,
            height_mm / rows if rows else height_mm,
            rows,
            cols,
        ),
        capacity=capacity,
        color=sku.get("color"),
        template_id=template.get("id"),
        localized=localized,
    )


#: The new Flutter NIIMBOT app keeps its label library in an Isar database.
FLUTTER_DB: tuple[Path, ...] = (
    Path(os.environ.get("APPDATA", "")) / "niimbot.com" / "NIIMBOT" / "Niimbot" / "niimbot_db.isar",
    Path(os.environ.get("LOCALAPPDATA", "")) / "niimbot-print" / "niimbot_db.isar",
)

#: ``08 00 00`` + eight ASCII digits is how Isar stores the roll's ``oneCode`` string.
_ISAR_CODE_RE = re.compile(rb"\x08\x00\x00(\d{8})")
_ISAR_NAMES_RE = re.compile(rb'\[\{"languageCode".{0,6000}?\]', re.S)
_ISAR_NAME_RE = re.compile(rb'"languageCode":"([a-zA-Z-]+)","languageName":"[^"]*","name":"([^"]*)"')


def scan_flutter_catalog(extra_files: list[Path] | None = None) -> tuple[dict[str, Consumable], list[str]]:
    """Read roll formats the new Flutter app cached locally.

    Only the roll records (the ones that carry a multilingual name such as
    ``T40*15/2R-460WHITE``) are useful: their eight-digit ``oneCode`` is exactly
    the value the RFID tag reports, so it can key our own catalogue. Design
    templates stored next to them carry no geometry, so they are skipped.
    """
    files = [Path(item) for item in (extra_files or [])]
    files.extend(path for path in FLUTTER_DB if path.is_file())
    found: dict[str, Consumable] = {}
    scanned: list[str] = []

    for path in files:
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        scanned.append(str(path))
        for match in _ISAR_NAMES_RE.finditer(raw):
            names = {
                code.decode("ascii", "replace"): name.decode("utf-8", "replace")
                for code, name in _ISAR_NAME_RE.findall(match.group())
                if name
            }
            if not names:
                continue
            display = names.get("en") or names.get("zh-cn") or next(iter(names.values()))
            if not display.strip():
                continue
            codes = _ISAR_CODE_RE.findall(raw[max(0, match.start() - 260): match.start()])
            if not codes:
                continue
            code = codes[-1].decode("ascii")
            sku = parse_sku_name(display)
            if not sku.get("width_mm") or not sku.get("height_mm"):
                continue
            rows = int(sku.get("rows") or 1)
            cols = int(sku.get("cols") or 1)
            found[code] = Consumable(
                barcode=code,
                name=display,
                width_mm=float(sku["width_mm"]) * cols,
                height_mm=float(sku["height_mm"]) * rows,
                paper_type=1,
                rows=rows,
                cols=cols,
                regions=sku_regions(float(sku["width_mm"]), float(sku["height_mm"]), rows, cols),
                capacity=sku.get("capacity"),
                color=sku.get("color"),
                localized=names,
                source="vendor-flutter",
            )
    return found, scanned


def scan_vendor_catalog(extra_dirs: list[Path] | None = None) -> tuple[dict[str, Consumable], list[str]]:
    """Scan every known vendor storage location and return the formats found."""
    directories = list(extra_dirs or [])
    directories.extend(VENDOR_ROOTS)
    found: dict[str, Consumable] = {}
    scanned: list[str] = []

    for directory in directories:
        templates, infos, files = read_records(Path(directory))
        scanned.extend(f"{directory}\\{name}" for name in files)
        for barcode, template in templates.items():
            # Roll info is keyed by tag serial; pair the newest matching entry by
            # barcode when the template carries it, otherwise leave counters out.
            info = None
            for record in infos.values():
                if str(record.get("serialNumber") or "") and template.get("barcode") == barcode:
                    info = record
                    break
            found[barcode] = template_to_consumable(barcode, template, info)

    # The newer Flutter app stores the same rolls under the code the RFID tag reports.
    flutter_found, flutter_scanned = scan_flutter_catalog()
    scanned.extend(flutter_scanned)
    for code, consumable in flutter_found.items():
        found.setdefault(code, consumable)

    return {key: value for key, value in found.items() if value.is_usable}, scanned
