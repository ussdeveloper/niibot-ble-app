"""How small a QR code or barcode may get before it stops being scannable.

A code is drawn from whole modules (dark/light squares of the same width) and the printer
can only place whole dots, so the physical size is quantised:

    module_px = floor(box_px / modules)

Below :data:`HARD_MODULE_DOTS` the pattern either smears across pixels (fractional modules) or
is clipped by the box, and nothing can scan it — that is the floor the editor blocks at.
Between one and :data:`GOOD_MODULE_DOTS` dots the code still prints, but the modules are so
fine that many phones give up, so the editor warns and offers the roomier size.
"""

from __future__ import annotations

#: Dots per module below which a code cannot be printed at all.
HARD_MODULE_DOTS = 1

#: Dots per module that scans reliably on thermal label stock (0.25 mm at 203 dpi).
GOOD_MODULE_DOTS = 2

#: A QR code wants a light margin (the quiet zone) at least this many modules wide.
QR_QUIET_ZONE = 4

#: Barcodes want a quiet zone this many narrow modules wide on each side.
BARCODE_QUIET_ZONE = 10


def qr_module_count(text: str, ec_level: str = "M") -> int:
    """Modules per side of the QR matrix (without the quiet zone)."""
    import qrcode

    levels = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }
    code = qrcode.QRCode(
        error_correction=levels.get(str(ec_level or "M").upper(), qrcode.constants.ERROR_CORRECT_M),
        border=0,
    )
    code.add_data(str(text or ""))
    code.make(fit=True)
    return len(code.get_matrix())


def barcode_module_count(fmt: str, text: str) -> int:
    """Narrow modules in the barcode pattern (bars + spaces)."""
    import barcode as pybarcode

    cls = pybarcode.get_barcode_class(str(fmt or "code128"))
    try:
        kwargs = {"add_checksum": False} if str(fmt).lower() == "code39" else {}
        instance = cls(str(text or ""), **kwargs)
    except TypeError:
        instance = cls(str(text or ""))
    return len("".join(instance.build()))


def mm_for(dots: int, dpi: int) -> float:
    return round(dots * 25.4 / max(1, int(dpi)), 2)


def minimum_size(
    kind: str,
    dpi: int,
    text: str = "",
    ec_level: str = "M",
    barcode_format: str = "code128",
) -> dict:
    """Smallest printable box, and the roomier size scanners are happy with.

    ``min_*`` is the hard floor the editor blocks at (one dot per module);
    ``recommended_*`` keeps two dots per module, which is 0.25 mm at 203 dpi.
    """
    dpi = max(1, int(dpi))
    if kind == "qr":
        modules = qr_module_count(text, ec_level)
        quiet = QR_QUIET_ZONE
        square = True
    else:
        modules = barcode_module_count(barcode_format, text)
        quiet = BARCODE_QUIET_ZONE
        square = False

    def size(count: int, dots: int) -> float:
        return mm_for(count * dots, dpi)

    hard = size(modules, HARD_MODULE_DOTS)
    comfy = size(modules + quiet * 2, GOOD_MODULE_DOTS)
    if square:
        min_h, comfy_h = hard, comfy
    else:
        # Bars only need enough height for the pattern plus the human-readable line.
        min_h = mm_for(20 * HARD_MODULE_DOTS, dpi)
        comfy_h = mm_for(40 * GOOD_MODULE_DOTS, dpi)

    return {
        "kind": kind,
        "dpi": dpi,
        "modules": modules,
        "span": modules + quiet * 2,
        "quiet_zone": quiet,
        "hard_module_mm": mm_for(HARD_MODULE_DOTS, dpi),
        "module_mm": mm_for(GOOD_MODULE_DOTS, dpi),
        "min_w_mm": hard,
        "min_h_mm": min_h,
        "recommended_w_mm": comfy,
        "recommended_h_mm": comfy_h,
        "square": square,
    }
