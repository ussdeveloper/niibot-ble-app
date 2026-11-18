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
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

from .spec import Element, LabelSpec
from .paths import data_dir
from . import codes
from .codes import minimum_size

MM_PER_INCH = 25.4
EPS = 1e-6

#: How each ink is drawn and shown. Red is the printer's second, lower-energy dot: the artwork is
#: drawn in this colour so the preview matches the paper, and the renderer splits the two inks
#: from the colour of each dot before sending anything.
INK_RGB: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "red": (198, 32, 38),
}

#: How dark each ink reads on white paper; a dot is half-covered at half this value.
INK_LUMINANCE: dict[str, int] = {
    ink: (299 * red + 587 * green + 114 * blue) // 1000 for ink, (red, green, blue) in INK_RGB.items()
}

#: A dot is red when the red channel beats both others by this much. Anti-aliased edges of red
#: glyphs stay red instead of drifting towards grey.
RED_DOMINANCE = 40

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

    #: Every dot, whichever ink — the shape a printer that has one colour can send.
    image: Image.Image
    width_px: int
    height_px: int
    source_width_px: int
    warnings: list[str] = field(default_factory=list)
    #: The same dots split by ink. ``image`` is their union, so mono output is unchanged.
    black: Image.Image | None = None
    red: Image.Image | None = None

    @property
    def cols(self) -> int:
        """Width rounded up to a whole byte, as required by the printer."""
        return (self.width_px + 7) // 8 * 8

    @property
    def uses_red(self) -> bool:
        """True when any dot is red — only then does the page need the two-colour mode."""
        if self.red is None:
            return False
        return self.red.getextrema()[0] == 0


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
    # Whole pixels per module keep the bars crisp; a fractional module width smears the
    # pattern and whole-dot printers make it worse, so below the limit the code is refused
    # instead of printed unreadably.
    quiet = codes.BARCODE_QUIET_ZONE
    # Give the code the quiet zone scanners need when there is room, otherwise use the box.
    module_px = int(width // (modules + quiet * 2))
    if module_px < codes.GOOD_MODULE_DOTS:
        module_px = int(width // modules)
    if module_px < codes.HARD_MODULE_DOTS:
        limit = minimum_size("barcode", dpi, data, barcode_format=element.barcode_format)
        warnings.append(
            f"Barcode {element.barcode_format} needs {limit['min_w_mm']} mm for {modules} modules "
            f"at {dpi} dpi — this box is {width * MM_PER_INCH / dpi:.1f} mm, so it was not printed. "
            f"Widen the box or shorten the data."
        )
        return
    if module_px < codes.GOOD_MODULE_DOTS:
        limit = minimum_size("barcode", dpi, data, barcode_format=element.barcode_format)
        warnings.append(
            f"Barcode modules are {limit['hard_module_mm']} mm (one dot) — scanners may struggle. "
            f"{limit['recommended_w_mm']} mm gives two dots per module."
        )
    module_w = module_px
    offset_x = int((width - module_px * modules) // 2)

    draw = ImageDraw.Draw(layer)
    start = None
    for index, bit in enumerate(pattern + "0"):
        if bit == "1":
            if start is None:
                start = index
        elif start is not None:
            x0 = offset_x + start * module_w
            x1 = offset_x + index * module_w
            draw.rectangle([x0, 0, max(x0, x1 - 1), usable - 1], fill=(0, 0, 0, 255))
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


def _draw_qr(layer: Image.Image, element: Element, dpi: int, warnings: list[str]) -> None:
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
    limit = minimum_size("qr", dpi, data, element.ec_level)
    # Each module is a whole block of dots. The matrix is centred, and a quiet zone is added
    # when it fits; when the box is tight the matrix may use all of it, because a fractional
    # or sub-dot module is what makes a printed QR unscannable.
    shortest = min(width, height)
    module = shortest // limit["span"]
    if module < codes.GOOD_MODULE_DOTS:
        module = shortest // count
    if module < codes.HARD_MODULE_DOTS:
        warnings.append(
            f"QR code needs {limit['min_w_mm']} mm at {dpi} dpi "
            f"({count} modules) — this box is {shortest * MM_PER_INCH / dpi:.1f} mm, "
            f"so it was not printed. Enlarge the box or shorten the text."
        )
        return
    if module < codes.GOOD_MODULE_DOTS:
        warnings.append(
            f"QR modules are {limit['hard_module_mm']} mm (one dot) — scanners may struggle. "
            f"{limit['recommended_w_mm']} mm gives two dots per module."
        )
    body = module * count
    offset_x = (width - body) // 2
    offset_y = (height - body) // 2
    draw = ImageDraw.Draw(layer)
    for row, row_data in enumerate(matrix):
        y0 = offset_y + row * module
        # Paint each horizontal run as one rectangle: no seams between neighbours.
        col = 0
        while col < count:
            if not row_data[col]:
                col += 1
                continue
            start = col
            while col < count and row_data[col]:
                col += 1
            x0 = offset_x + start * module
            draw.rectangle([x0, y0, x0 + (col - start) * module - 1, y0 + module - 1], fill=(0, 0, 0, 255))


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


def _tone(image: Image.Image, brightness: float) -> Image.Image:
    """Shift the picture's luminance before it reaches the 1-bit threshold.

    Thermal paper only knows "dot" and "no dot", so a photo is decided by where the threshold
    falls. Lifting the whole picture (keeping its alpha) prints it lighter, which is what a dark
    photo needs to stay readable — the same slider serves dithering and hard thresholding.
    """
    amount = max(-100.0, min(100.0, float(brightness or 0.0)))
    if abs(amount) < 0.5:
        return image
    offset = amount * 2.55
    alpha = image.getchannel("A")
    gray = image.convert("L").point(lambda value: max(0, min(255, int(round(value + offset)))))
    toned = Image.merge("RGB", (gray, gray, gray))
    toned.putalpha(alpha)
    return toned


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
    brightness = float(element.image_brightness or 0.0)

    def place(picture: Image.Image, x: float, y: float) -> None:
        picture = _tone(picture, brightness)
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


def _in_red(layer: Image.Image) -> Image.Image:
    """Repaint a black-on-transparent layer in the second ink, keeping its alpha."""
    alpha = layer.getchannel("A")
    tinted = ImageOps.colorize(layer.convert("L"), black=INK_RGB["red"], white=(255, 255, 255)).convert("RGBA")
    tinted.putalpha(alpha)
    return tinted


def _split_inks(canvas: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Split a coloured canvas into its black and red dot planes (``0`` = a dot).

    The darkness threshold is half of each ink's own darkness, so a half-covered pixel of either
    ink is a dot: switching an item to red keeps every dot and only changes the colour it
    develops. Red is a lighter ink on paper, which is why it cannot reuse the black threshold.
    """
    rgb = canvas.convert("RGB")
    red_channel, green_channel, blue_channel = rgb.split()
    luminance = rgb.convert("L")
    dark = ImageOps.invert(luminance)
    warmer = ImageChops.multiply(
        ImageChops.subtract(red_channel, green_channel).point(lambda value: 255 if value > RED_DOMINANCE else 0),
        ImageChops.subtract(red_channel, blue_channel).point(lambda value: 255 if value > RED_DOMINANCE else 0),
    )
    threshold = Image.composite(
        Image.new("L", rgb.size, (255 - INK_LUMINANCE["red"]) // 2),
        Image.new("L", rgb.size, (255 - INK_LUMINANCE["black"]) // 2),
        warmer,
    )
    dots = ImageChops.subtract(dark, threshold).point(lambda value: 255 if value > 0 else 0)
    red_dots = ImageChops.multiply(dots, warmer)
    black_dots = ImageChops.subtract(dots, red_dots)
    return (
        ImageOps.invert(black_dots).convert("1", dither=Image.NONE),
        ImageOps.invert(red_dots).convert("1", dither=Image.NONE),
    )


def colourise(black: Image.Image | None, red: Image.Image | None) -> Image.Image:
    """Draw the two ink planes in paper colours — the bitmap the screen should show."""
    size = (black or red).size
    out = Image.new("RGB", size, (255, 255, 255))
    for plane, ink in ((black, "black"), (red, "red")):
        if plane is None:
            continue
        dots = ImageOps.invert(plane.convert("L"))
        out.paste(Image.new("RGB", size, INK_RGB[ink]), (0, 0), dots)
    return out


def separate_inks(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Grey copies of a picture, one per ink, with the other ink erased to white.

    Used where the artwork arrives as a finished image rather than as elements: each copy is
    placed with the same fit, so the two inks stay in register.
    """
    rgb = image.convert("RGB")
    red_channel, green_channel, blue_channel = rgb.split()
    grey = rgb.convert("L")
    warmer = ImageChops.multiply(
        ImageChops.subtract(red_channel, green_channel).point(lambda value: 255 if value > RED_DOMINANCE else 0),
        ImageChops.subtract(red_channel, blue_channel).point(lambda value: 255 if value > RED_DOMINANCE else 0),
    )
    black_only = Image.composite(Image.new("L", grey.size, 255), grey, warmer)
    red_only = Image.composite(grey, Image.new("L", grey.size, 255), warmer)
    return black_only, red_only


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
        _draw_qr(layer, element, dpi, warnings)
    elif element.type == "rect":
        _draw_rect(layer, element, dpi)
    elif element.type == "line":
        _draw_line(layer, element, dpi)
    elif element.type == "image":
        _draw_image(layer, element, image_loader, dpi)

    if rotation:
        layer = layer.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    if element.ink == "red":
        layer = _in_red(layer)

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
    if width != source_width:
        canvas = canvas.crop((0, 0, width, height))
    black, red = _split_inks(canvas)
    return RenderedLabel(
        image=ImageChops.darker(black, red),
        width_px=width,
        height_px=height,
        source_width_px=source_width,
        warnings=warnings,
        black=black,
        red=red,
    )


def preview_payload(
    spec: LabelSpec,
    dpi: int = 203,
    printhead_px: int = 384,
    image_loader: Callable[[str], Path | None] | None = None,
    scale: int = 3,
) -> dict:
    """Screen preview plus the exact print bitmap, both in the label's inks.

    The preview is rendered natively at ``scale`` × the print resolution, so the browser can
    show smooth glyphs instead of the stair-stepped 1-bit bitmap blown up 2-3 times; the print
    bitmap is the same picture after the threshold, coloured like the paper.
    """
    rendered = render_label(spec, dpi, printhead_px, image_loader)
    canvas, warnings, source_width, width, height = _render_canvas(
        spec, dpi * scale, printhead_px * scale, image_loader
    )
    if width != source_width:
        canvas = canvas.crop((0, 0, width, height))
    preview = canvas.convert("RGB")
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    print_buffer = io.BytesIO()
    colourise(rendered.black, rendered.red).save(print_buffer, format="PNG")
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
        "uses_red": rendered.uses_red,
        "warnings": warnings or rendered.warnings,
    }
    return payload
