"""Render a :class:`~niibot.spec.LabelSpec` into a printable 1-bit bitmap."""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

from .spec import Element, LabelSpec
from .paths import data_dir

MM_PER_INCH = 25.4
EPS = 1e-6

#: family -> (regular, bold, italic, bold-italic) file names
FONT_FILES: dict[str, tuple[str, ...]] = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Arial Black": ("ariblk.ttf", "ariblk.ttf", "ariblk.ttf", "ariblk.ttf"),
    "Arial Narrow": ("arialn.ttf", "arialnb.ttf", "arialni.ttf", "arialnbi.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "Consolas": ("consola.ttf", "consolab.ttf", "consolai.ttf", "consolaz.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "Franklin Gothic": ("framd.ttf", "framdb.ttf", "framdi.ttf", "framdbi.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Impact": ("impact.ttf", "impact.ttf", "impact.ttf", "impact.ttf"),
    "Lucida Console": ("lucon.ttf", "lucon.ttf", "lucon.ttf", "lucon.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebuci.ttf", "trebucbi.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    "Liberation Sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
}


@lru_cache(maxsize=1)
def _font_dirs() -> tuple[Path, ...]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    project = Path(__file__).resolve().parent.parent
    candidates = [
        project / "data" / "fonts",
        windir / "Fonts",
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
    ]
    return tuple(path for path in candidates if path.is_dir())


@lru_cache(maxsize=1)
def _font_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for directory in _font_dirs():
        for path in directory.rglob("*"):
            if path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                index.setdefault(path.name.lower(), path)
    return index


#: style slot order used by :func:`load_font`
STYLE_SLOTS = ("regular", "bold", "italic", "bold-italic")

#: file signature -> (family, style) so the scan only happens once per file version
_FONT_CACHE_FILE = data_dir() / "font-index.json"
_FONT_CACHE_VERSION = 2


def _guess_style(style_text: str) -> str:
    text = style_text.lower()
    bold = "bold" in text or "black" in text or "heavy" in text
    italic = "italic" in text or "oblique" in text
    if bold and italic:
        return "bold-italic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


@lru_cache(maxsize=1)
def _font_faces() -> dict[str, dict[str, Path]]:
    """family name -> {style: font file} for every installed (or custom) font."""
    index = _font_index()
    signature = {name: f"{path.stat().st_mtime_ns}:{path.stat().st_size}" for name, path in index.items()}

    cached: dict = {}
    try:
        if _FONT_CACHE_FILE.is_file():
            cached = json.loads(_FONT_CACHE_FILE.read_text(encoding="utf-8"))
            if cached.get("version") != _FONT_CACHE_VERSION:
                cached = {}
    except Exception:
        cached = {}

    files: dict[str, dict] = dict(cached.get("files") or {})
    dirty = False
    for name, path in index.items():
        if files.get(name, {}).get("sig") == signature[name]:
            continue
        family, style = "", "regular"
        try:
            probe = ImageFont.truetype(str(path), 12)
            family, style_text = probe.getname()
            family = (family or "").strip()
            style = _guess_style(style_text or "")
        except Exception:
            family = ""
        if not family or "?" in family or "\ufffd" in family:
            family = path.stem
        files[name] = {"sig": signature[name], "family": family, "style": style}
        dirty = True

    if dirty:
        try:
            _FONT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _FONT_CACHE_FILE.write_text(
                json.dumps({"version": _FONT_CACHE_VERSION, "files": files}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    faces: dict[str, dict[str, Path]] = {}
    used: set[Path] = set()

    # curated families keep their exact files (Arial Black stays separate from Arial, ...)
    for family, names in FONT_FILES.items():
        bucket: dict[str, Path] = {}
        for slot, name in enumerate(names):
            path = index.get(name.lower())
            if path is None:
                continue
            bucket.setdefault(STYLE_SLOTS[slot], path)
            bucket.setdefault("any", path)
        if bucket:
            faces[family] = bucket
            used.update(bucket.values())

    for name, meta in files.items():
        path = index.get(name)
        if path is None or path in used:
            continue
        family = meta.get("family") or path.stem
        style = meta.get("style") or "regular"
        if not family or "?" in family or "\ufffd" in family:
            family = path.stem
        existing = faces.get(family)
        if existing is None:
            existing = {}
            faces[family] = existing
        existing.setdefault(style, path)
        existing.setdefault("any", path)
    return faces


def font_catalog() -> list[dict]:
    """Family list with available styles — used by the font picker in the UI."""
    faces = _font_faces()
    curated_rank = {name: position for position, name in enumerate(FONT_FILES)}
    entries: list[dict] = []
    for family, styles in faces.items():
        entries.append(
            {
                "name": family,
                "styles": [style for style in STYLE_SLOTS if style in styles],
                "curated": family in FONT_FILES,
            }
        )
    entries.sort(key=lambda item: (0 if item["curated"] else 1, curated_rank.get(item["name"], 999), item["name"].lower()))
    return entries


def available_fonts() -> list[str]:
    families = [entry["name"] for entry in font_catalog()]
    return families or ["Arial"]


@lru_cache(maxsize=1024)
def load_font(family: str, size_px: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    size_px = max(4, int(size_px))
    if bold and italic:
        slot = 3
    elif bold:
        slot = 1
    elif italic:
        slot = 2
    else:
        slot = 0

    faces = _font_faces()
    bucket = faces.get(family)
    if bucket is None:
        wanted = family.strip().lower()
        for name, styles in faces.items():
            if name.strip().lower() == wanted:
                bucket = styles
                break
    if bucket is None:
        wanted = family.strip().lower().replace(" ", "")
        for name, styles in faces.items():
            if name.strip().lower().replace(" ", "") == wanted:
                bucket = styles
                break

    candidates: list[Path] = []
    if bucket:
        style_key = STYLE_SLOTS[slot]
        for key in (style_key, "regular", "bold", "italic", "bold-italic", "any"):
            path = bucket.get(key)
            if path is not None and path not in candidates:
                candidates.append(path)
    else:
        files = FONT_FILES.get(family)
        index = _font_index()
        if files:
            candidates.extend(path for name in files if (path := index.get(name.lower())) is not None)
        slug = family.replace(" ", "").lower()
        candidates.extend(path for name, path in index.items() if slug in name.replace(" ", ""))

    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size_px)
        except OSError:
            continue
    index = _font_index()
    if index:
        return ImageFont.truetype(str(next(iter(index.values()))), size_px)
    return ImageFont.load_default(size=size_px)


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm * dpi / MM_PER_INCH))


@dataclass
class RenderedLabel:
    """A rendered label: 1-bit image plus the geometry needed for printing."""

    image: Image.Image
    width_px: int
    height_px: int
    source_width_px: int
    warnings: list[str] = field(default_factory=list)

    @property
    def cols(self) -> int:
        """Width rounded up to a whole byte, as required by the printer."""
        return (self.width_px + 7) // 8 * 8


# --------------------------------------------------------------------- helpers
def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}".strip() if current else word
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            while draw.textlength(current, font=font) > max_width and len(current) > 1:
                cut = len(current)
                while cut > 1 and draw.textlength(current[:cut], font=font) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines


def _draw_text(
    layer: Image.Image,
    element: Element,
    dpi: int,
    warnings: list[str],
    label: str,
) -> None:
    width, height = layer.size
    size_pt = max(1.0, element.font_size_pt)
    line_height = 0
    lines: list[str] = []
    font = load_font(element.font, 10)

    for _ in range(80):
        font = load_font(element.font, round(size_pt * dpi / 72), element.bold, element.italic)
        measure = ImageDraw.Draw(Image.new("L", (1, 1)))
        lines = _wrap(measure, element.text, font, width)
        ascent, descent = font.getmetrics()
        line_height = round((ascent + descent) * element.line_spacing)
        fits = line_height * max(1, len(lines)) <= height and all(
            measure.textlength(line, font=font) <= width + 0.5 for line in lines
        )
        if fits:
            break
        if not element.auto_fit:
            warnings.append(f"“{label}”: the text does not fit the box.")
            break
        if size_pt <= 2.0:
            warnings.append(f"“{label}”: the text is too long and was shrunk to the minimum.")
            break
        size_pt *= 0.92

    ascent, descent = font.getmetrics()
    line_height = round((ascent + descent) * element.line_spacing)
    total_height = line_height * max(1, len(lines))

    if element.valign == "top":
        y = 0
    elif element.valign == "bottom":
        y = height - total_height
    else:
        y = (height - total_height) / 2

    size_px = round(size_pt * dpi / 72)
    block = _text_block(
        (width, height),
        lines,
        element.font,
        size_px,
        bold=element.bold,
        italic=element.italic,
        line_height=line_height,
        start_y=y,
        align=element.align,
    )
    layer.alpha_composite(block)


def _barcode_pattern(fmt: str, data: str) -> tuple[str, str]:
    import barcode as pybarcode

    try:
        cls = pybarcode.get_barcode_class(fmt)
    except Exception as exc:  # pragma: no cover - guarded by the UI
        raise ValueError(f"unsupported barcode format: {fmt}") from exc
    try:
        kwargs = {"add_checksum": False} if fmt.lower() == "code39" else {}
        instance = cls(data, **kwargs)
    except TypeError:
        instance = cls(data)
    except Exception as exc:
        raise ValueError(f"invalid data for barcode {fmt}: {exc}") from exc
    pattern = "".join(instance.build())
    human = data
    getter = getattr(instance, "get_fullcode", None)
    if callable(getter):
        try:
            human = getter()
        except Exception:  # pragma: no cover - defensive
            human = data
    return pattern, human


def _draw_barcode(layer: Image.Image, element: Element, dpi: int, warnings: list[str]) -> None:
    width, height = layer.size
    data = (element.text or "").strip()
    if not data:
        return
    pattern, human = _barcode_pattern(element.barcode_format, data)
    modules = len(pattern)
    if modules == 0:
        return

    text_height = 0
    font = None
    if element.show_text:
        font = load_font(element.font, max(8, round(height * 0.18)), element.bold, element.italic)
        text_height = font.getmetrics()[0] + font.getmetrics()[1]

    usable = max(1, height - text_height)
    # Whole pixels per module keep the bars crisp; a fractional module width
    # smears the pattern at 203 dpi and makes scanners unreliable.
    module_px = int(width // modules)
    if module_px >= 1:
        module_w = module_px
        offset_x = int((width - module_px * modules) // 2)
    else:
        module_w = width / modules
        offset_x = 0
        warnings.append(
            f"Barcode {element.barcode_format} has {modules} modules — too wide for this box "
            f"({width:.0f} px). Increase the width or shorten the data."
        )

    draw = ImageDraw.Draw(layer)
    start = None
    for index, bit in enumerate(pattern + "0"):
        if bit == "1":
            if start is None:
                start = index
        elif start is not None:
            x0 = offset_x + int(round(start * module_w))
            x1 = offset_x + int(round(index * module_w))
            if x1 <= x0:
                x1 = x0 + 1
            draw.rectangle([x0, 0, x1 - 1, usable - 1], fill=(0, 0, 0, 255))
            start = None

    if element.show_text and font is not None:
        text_px = max(8, round(height * 0.18))
        block = _text_block(
            (width, height),
            [human],
            element.font,
            text_px,
            bold=element.bold,
            italic=element.italic,
            line_height=text_height,
            start_y=usable,
            align="center",
        )
        layer.alpha_composite(block)


_EC_LEVELS = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}


# Glyphs are rasterised this many times larger and averaged down before the 1-bit threshold.
# At 203 dpi (8 dots/mm) a straight 1:1 raster loses thin strokes and stair-steps curves;
# the supersampled block keeps the letterforms readable on the finished label.
TEXT_SUPERSAMPLE = 4


def _text_block(
    size: tuple[int, int],
    lines: list[str],
    family: str,
    size_px: int,
    *,
    bold: bool = False,
    italic: bool = False,
    line_height: int = 0,
    start_y: float = 0.0,
    align: str = "left",
) -> Image.Image:
    """Draw text lines into a transparent block, supersampled and averaged back down."""
    width, height = max(1, size[0]), max(1, size[1])
    scale = TEXT_SUPERSAMPLE
    block = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(block)
    font = load_font(family, max(1, int(round(size_px * scale))), bold, italic)
    y = start_y
    for line in lines:
        line_width = draw.textlength(line, font=font)
        if align == "center":
            x = (width * scale - line_width) / 2
        elif align == "right":
            x = width * scale - line_width
        else:
            x = 0.0
        draw.text((x, y * scale), line, font=font, fill=(0, 0, 0, 255), anchor="la")
        y += line_height
    return block.resize((width, height), Image.LANCZOS)


def _draw_qr(layer: Image.Image, element: Element, warnings: list[str]) -> None:
    data = (element.text or "").strip()
    if not data:
        return
    qr = qrcode.QRCode(error_correction=_EC_LEVELS.get(element.ec_level.upper(), ERROR_CORRECT_M), border=0)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    count = len(matrix)
    if count == 0:
        return
    width, height = layer.size
    # Integer module size: no rounding gaps between adjacent modules.
    module = max(1, min(width, height) // count)
    size = module * count
    if size > min(width, height):
        warnings.append("The QR code is too dense for this box — make it larger.")
        size = min(width, height)
        module = max(1, size // count)
        size = module * count
    offset_x = (width - size) // 2
    offset_y = (height - size) // 2
    draw = ImageDraw.Draw(layer)
    for row, row_data in enumerate(matrix):
        for col, value in enumerate(row_data):
            if not value:
                continue
            x0 = offset_x + col * module
            y0 = offset_y + row * module
            draw.rectangle([x0, y0, x0 + module - 1, y0 + module - 1], fill=(0, 0, 0, 255))


def _draw_rect(layer: Image.Image, element: Element, dpi: int) -> None:
    width, height = layer.size
    draw = ImageDraw.Draw(layer)
    thickness = max(1, mm_to_px(element.thickness_mm, dpi))
    if element.fill:
        draw.rectangle([0, 0, width - 1, height - 1], fill=(0, 0, 0, 255))
    else:
        draw.rectangle([0, 0, width - 1, height - 1], outline=(0, 0, 0, 255), width=thickness)


def _draw_line(layer: Image.Image, element: Element, dpi: int) -> None:
    width, height = layer.size
    draw = ImageDraw.Draw(layer)
    thickness = max(1, mm_to_px(element.thickness_mm, dpi))
    if width >= height:
        y = height // 2
        draw.rectangle([0, y - thickness // 2, width - 1, y - thickness // 2 + thickness - 1], fill=(0, 0, 0, 255))
    else:
        x = width // 2
        draw.rectangle([x - thickness // 2, 0, x - thickness // 2 + thickness - 1, height - 1], fill=(0, 0, 0, 255))


def _halftone(image: Image.Image) -> Image.Image:
    """Flatten *image* onto white and dither it into an ink mask (255 = dot)."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    gray = Image.new("L", image.size, 255)
    gray.paste(image.convert("L"), (0, 0), image.split()[3])
    mono = gray.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
    return mono.point(lambda value: 0 if value else 255)


def _draw_image(
    layer: Image.Image,
    element: Element,
    loader: Callable[[str], Path | None] | None,
    dpi: int = 203,
) -> None:
    if loader is None or not element.image_id:
        return
    path = loader(element.image_id)
    if path is None or not path.is_file():
        return
    try:
        source = Image.open(path)
        source.load()
    except Exception:
        return
    source = source.convert("RGBA")
    width, height = layer.size
    fit = element.image_fit if element.image_fit in ("cover", "contain", "stretch") else "contain"

    def place(picture: Image.Image, x: float, y: float) -> None:
        if element.image_dither:
            _paste_clipped_mask(layer, _halftone(picture), int(round(x)), int(round(y)))
        else:
            _paste_clipped(layer, picture, int(round(x)), int(round(y)))

    if fit == "stretch":
        scaled = source.resize((max(1, width), max(1, height)), Image.LANCZOS)
        place(scaled, 0, 0)
        return

    zoom = max(0.05, float(element.image_zoom or 1.0))
    base = max(width / source.width, height / source.height) if fit == "cover" else min(
        width / source.width, height / source.height
    )
    scale = base * zoom
    new_size = (max(1, int(round(source.width * scale))), max(1, int(round(source.height * scale))))
    scaled = source.resize(new_size, Image.LANCZOS)

    offset_x = mm_to_px(element.image_offset_x or 0.0, dpi)
    offset_y = mm_to_px(element.image_offset_y or 0.0, dpi)
    left = (width - new_size[0]) / 2 + offset_x
    top = (height - new_size[1]) / 2 + offset_y
    if fit == "cover":
        # Never show a gap: the crop window stays inside the picture.
        left = min(0, max(width - new_size[0], left))
        top = min(0, max(height - new_size[1], top))
    place(scaled, left, top)


def _paste_clipped(target: Image.Image, picture: Image.Image, x: int, y: int) -> None:
    """Composite *picture* at (x, y) without leaving the target bounds."""
    left = max(0, -x)
    top = max(0, -y)
    right = min(picture.width, target.width - x)
    bottom = min(picture.height, target.height - y)
    if right <= left or bottom <= top:
        return
    target.alpha_composite(picture.crop((left, top, right, bottom)), (x + left, y + top))


def _paste_clipped_mask(target: Image.Image, mask: Image.Image, x: int, y: int) -> None:
    """Paint black through *mask* at (x, y), clipped to the target bounds."""
    left = max(0, -x)
    top = max(0, -y)
    right = min(mask.width, target.width - x)
    bottom = min(mask.height, target.height - y)
    if right <= left or bottom <= top:
        return
    patch = mask.crop((left, top, right, bottom))
    target.paste(Image.new("RGBA", patch.size, (0, 0, 0, 255)), (x + left, y + top), patch)


def _blend(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    left = max(0, -x)
    top = max(0, -y)
    right = min(layer.width, canvas.width - x)
    bottom = min(layer.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(layer.crop((left, top, right, bottom)), dest=(x + left, y + top))


def _draw_element(
    canvas: Image.Image,
    element: Element,
    dpi: int,
    warnings: list[str],
    image_loader: Callable[[str], Path | None] | None,
) -> None:
    box_w = max(1, mm_to_px(element.w, dpi))
    box_h = max(1, mm_to_px(element.h, dpi))
    rotation = element.rotation % 360

    if rotation in (90, 270):
        content_w, content_h = box_h, box_w
    else:
        content_w, content_h = box_w, box_h

    layer = Image.new("RGBA", (content_w, content_h), (255, 255, 255, 0))
    label = (element.text or element.type).strip().replace("\n", " ")[:24]

    if element.type == "text":
        _draw_text(layer, element, dpi, warnings, label)
    elif element.type == "barcode":
        _draw_barcode(layer, element, dpi, warnings)
    elif element.type == "qr":
        _draw_qr(layer, element, warnings)
    elif element.type == "rect":
        _draw_rect(layer, element, dpi)
    elif element.type == "line":
        _draw_line(layer, element, dpi)
    elif element.type == "image":
        _draw_image(layer, element, image_loader, dpi)

    if rotation:
        layer = layer.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    _blend(canvas, layer, mm_to_px(element.x, dpi), mm_to_px(element.y, dpi))


# ----------------------------------------------------------------------- API
def _render_canvas(
    spec: LabelSpec,
    dpi: int,
    printhead_px: int,
    image_loader: Callable[[str], Path | None] | None = None,
) -> tuple[Image.Image, list[str], int, int, int]:
    """Draw every element onto an RGBA canvas; returns canvas, warnings and geometry."""
    warnings: list[str] = []
    source_width = max(1, mm_to_px(spec.width_mm, dpi))
    height = max(1, mm_to_px(spec.height_mm, dpi))
    width = min(source_width, printhead_px)
    if source_width > printhead_px:
        cut_mm = (source_width - printhead_px) * MM_PER_INCH / dpi
        warnings.append(
            f"The label is wider than the printhead — the right {cut_mm:.1f} mm will be cut off."
        )

    canvas = Image.new("RGBA", (source_width, height), (255, 255, 255, 255))
    for element in spec.elements:
        if element.visible is False:
            continue
        _draw_element(canvas, element, dpi, warnings, image_loader)
    return canvas, warnings, source_width, width, height


def render_label(
    spec: LabelSpec,
    dpi: int = 203,
    printhead_px: int = 384,
    image_loader: Callable[[str], Path | None] | None = None,
) -> RenderedLabel:
    canvas, warnings, source_width, width, height = _render_canvas(spec, dpi, printhead_px, image_loader)
    gray = canvas.convert("L")
    if width != source_width:
        gray = gray.crop((0, 0, width, height))
    bitmap = gray.point(lambda value: 0 if value < 128 else 255, mode="L").convert("1", dither=Image.NONE)
    return RenderedLabel(image=bitmap, width_px=width, height_px=height, source_width_px=source_width, warnings=warnings)


def preview_payload(
    spec: LabelSpec,
    dpi: int = 203,
    printhead_px: int = 384,
    image_loader: Callable[[str], Path | None] | None = None,
    scale: int = 3,
) -> dict:
    """Screen preview plus the exact 1-bit print bitmap.

    The preview is rendered natively at ``scale`` × the print resolution and left in
    greyscale, so the browser can show smooth glyphs instead of the stair-stepped 1-bit
    bitmap blown up 2-3 times.
    """
    rendered = render_label(spec, dpi, printhead_px, image_loader)
    canvas, warnings, source_width, width, height = _render_canvas(
        spec, dpi * scale, printhead_px * scale, image_loader
    )
    if width != source_width:
        canvas = canvas.crop((0, 0, width, height))
    preview = canvas.convert("L")
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    print_buffer = io.BytesIO()
    rendered.image.save(print_buffer, format="PNG")
    payload = {
        "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "png_print": base64.b64encode(print_buffer.getvalue()).decode("ascii"),
        "width_px": rendered.width_px,
        "height_px": rendered.height_px,
        "display_scale": scale,
        "print_cols": rendered.cols,
        "cols": rendered.cols,
        "rows": rendered.height_px,
        "dpi": dpi,
        "printed_width_mm": round(rendered.width_px * MM_PER_INCH / dpi, 1),
        "printed_height_mm": round(rendered.height_px * MM_PER_INCH / dpi, 1),
        "warnings": warnings or rendered.warnings,
    }
    return payload
