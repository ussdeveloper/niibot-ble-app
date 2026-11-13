"""Fit an incoming document onto the label format detected in the printer.

The Windows print job can be any size (A4 page, photo, PDF). We scale it into the
label area measured/detected for the roll, with a selectable fit mode plus manual
offset, rotation and margin, then convert to the 1-bit bitmap the printer wants.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from PIL import Image

FIT_MODES = ("contain", "cover", "stretch")


@dataclass
class FitOptions:
    """How a document is placed on the label."""

    #: ``contain`` keeps the aspect ratio and leaves background,
    #: ``cover`` fills the label and crops the overflow,
    #: ``stretch`` distorts to the label size.
    mode: str = "contain"
    #: Shift in millimetres; positive X moves right, positive Y moves down.
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    #: Rotate the document before fitting (0/90/180/270).
    rotation: int = 0
    #: Extra scale on top of the fit mode, 1.0 = as computed.
    scale: float = 1.0
    #: Uniform margin kept free inside the label, in millimetres.
    margin_mm: float = 0.0
    #: Threshold for turning the result into black/white dots.
    threshold: int = 128
    #: Use Floyd–Steinberg dithering (photos) instead of a hard threshold.
    dither: bool = False
    #: Invert light-on-dark artwork.
    invert: bool = False

    def normalized(self) -> "FitOptions":
        mode = self.mode if self.mode in FIT_MODES else "contain"
        rotation = int(round(self.rotation / 90) * 90) % 360
        return FitOptions(
            mode=mode,
            offset_x_mm=float(self.offset_x_mm),
            offset_y_mm=float(self.offset_y_mm),
            rotation=rotation,
            scale=max(0.05, float(self.scale)),
            margin_mm=max(0.0, float(self.margin_mm)),
            threshold=max(1, min(254, int(self.threshold))),
            dither=bool(self.dither),
            invert=bool(self.invert),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm * dpi / 25.4))


def fit_to_label(
    source: Image.Image,
    width_px: int,
    height_px: int,
    dpi: int,
    options: FitOptions | None = None,
    paper: str = "white",
) -> Image.Image:
    """Return a ``1``-bit image of exactly ``width_px × height_px``."""
    opts = (options or FitOptions()).normalized()
    width_px = max(1, int(width_px))
    height_px = max(1, int(height_px))

    content = source
    if opts.rotation:
        content = content.rotate(-opts.rotation, expand=True)
    if content.mode not in ("L", "RGB", "RGBA"):
        content = content.convert("RGBA" if "A" in content.mode else "RGB")

    background = 255 if paper != "black" else 0
    canvas = Image.new("L", (width_px, height_px), background)

    margin = mm_to_px(opts.margin_mm, dpi)
    box_w = max(1, width_px - 2 * margin)
    box_h = max(1, height_px - 2 * margin)

    src_w, src_h = content.size
    if src_w <= 0 or src_h <= 0:
        return canvas.convert("1", dither=Image.NONE)

    if opts.mode == "stretch":
        target_w, target_h = box_w, box_h
    else:
        ratio_w = box_w / src_w
        ratio_h = box_h / src_h
        factor = min(ratio_w, ratio_h) if opts.mode == "contain" else max(ratio_w, ratio_h)
        target_w = max(1, int(round(src_w * factor)))
        target_h = max(1, int(round(src_h * factor)))

    target_w = max(1, int(round(target_w * opts.scale)))
    target_h = max(1, int(round(target_h * opts.scale)))

    resample = Image.LANCZOS if (target_w < src_w or target_h < src_h) else Image.NEAREST
    scaled = content.resize((target_w, target_h), resample)
    if scaled.mode == "RGBA":
        # Composite transparency onto the paper colour so cut-outs stay white.
        plate = Image.new("L", scaled.size, background)
        plate.paste(scaled.convert("L"), (0, 0), scaled.getchannel("A"))
        scaled = plate
    else:
        scaled = scaled.convert("L")

    offset_x = mm_to_px(opts.offset_x_mm, dpi)
    offset_y = mm_to_px(opts.offset_y_mm, dpi)

    if opts.mode == "cover":
        # Centre the overflow, then let the manual offset move the window.
        left = (target_w - box_w) // 2 - offset_x
        top = (target_h - box_h) // 2 - offset_y
        window = scaled.crop((left, top, left + box_w, top + box_h))
        canvas.paste(window, (margin, margin))
    else:
        x = margin + (box_w - target_w) // 2 + offset_x
        y = margin + (box_h - target_h) // 2 + offset_y
        canvas.paste(scaled, (x, y))

    if opts.invert:
        canvas = canvas.point(lambda value: 255 - value)

    if opts.dither:
        return canvas.convert("1", dither=Image.FLOYDSTEINBERG)

    threshold = opts.threshold
    return canvas.point(lambda value: 0 if value < threshold else 255, mode="L").convert("1", dither=Image.NONE)
