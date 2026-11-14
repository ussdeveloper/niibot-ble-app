"""Item zones: the outlines the calibrator draws for each printable item on a roll.

A zone may be a single rectangle/circle or several outlines that belong to the same
item (a circle with a rectangular tab, for example). :func:`outline_image` renders
just the *merged* border of every zone — the calibrator prints it so the placement
on the physical label can be checked without printing any content.
"""

from __future__ import annotations

from .models import Region


def _odd(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def outline_image(
    width_mm: float,
    height_mm: float,
    regions: list[Region],
    dpi: int = 203,
    *,
    stroke_mm: float = 0.3,
    label_border: bool = True,
):
    """An RGBA image holding only the zone borders (black ink, transparent elsewhere)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    # Render at 2× and downsample so circles and merged joins stay smooth at 203 dpi.
    supersample = 2
    scale = dpi / 25.4 * supersample
    width = max(1, round(width_mm * scale))
    height = max(1, round(height_mm * scale))
    ink = Image.new("L", (width, height), 0)

    def silhouette(shapes) -> Image.Image:
        mask = Image.new("L", (width, height), 0)
        pen = ImageDraw.Draw(mask)
        for shape in shapes:
            box = (
                shape.x * scale,
                shape.y * scale,
                (shape.x + shape.w) * scale,
                (shape.y + shape.h) * scale,
            )
            if shape.kind == "circle":
                pen.ellipse(box, fill=255)
            else:
                pen.rectangle(box, fill=255)
        return mask

    # Each zone gets its own ring, so neighbouring zones never merge into one outline.
    for region in regions:
        shapes = region.outlines()
        if not shapes:
            continue
        mask = silhouette(shapes)
        eroded = mask.filter(ImageFilter.MinFilter(_odd(max(3, round(stroke_mm * scale)))))
        ink = ImageChops.lighter(ink, ImageChops.subtract(mask, eroded))

    if label_border:
        border = Image.new("L", (width, height), 0)
        pen = ImageDraw.Draw(border)
        inset = max(1, round(0.25 * scale))
        pen.rectangle(
            (inset, inset, width - 1 - inset, height - 1 - inset),
            outline=255,
            width=max(1, round(0.2 * scale)),
        )
        ink = ImageChops.lighter(ink, border)

    target = (max(1, round(width_mm * dpi / 25.4)), max(1, round(height_mm * dpi / 25.4)))
    ink = ink.resize(target, Image.LANCZOS).point(lambda value: 255 if value >= 96 else 0)
    rgba = Image.new("RGBA", target, (0, 0, 0, 0))
    rgba.paste(Image.new("RGBA", target, (0, 0, 0, 255)), (0, 0), ink)
    return rgba
