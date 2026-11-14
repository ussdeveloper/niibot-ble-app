"""Gap calibration helpers.

The Niimbot family reports the label geometry (``GetLabelSize``), so the app can simply ask.
The LuckPrinter/Fichero family reports nothing of the sort: its firmware finds the gap with its
own sensor every time a form feed (``1D 0C``) is sent, and the app only needs the **label height
in dots** for the raster. That is exactly what the vendor app asks the user for — and it is why
"calibration" there means: put the printer in *gap* mode, feed until the printer stops at a gap,
and make sure the height in the app matches the stock.

This module builds the measuring strip that makes the last step measurable on paper: a scale in
millimetres along the direction the paper travels. Print it on the real roll, look at where the
next label's gap/edge falls on the scale, and type that number in as the label height.
"""

from __future__ import annotations

from .spec import Element, LabelSpec

#: Long tick every 5 mm, with the number printed next to it.
MAJOR_STEP_MM = 5
MINOR_STEP_MM = 1


def measuring_strip(width_mm: float, height_mm: float, note: str = "") -> LabelSpec:
    """A label with a millimetre scale along the paper direction (Y).

    The numbers grow downwards, the way the paper leaves the printer, so the value printed at the
    fold/gap line is the pitch to enter as the label height.
    """
    width = max(8.0, round(float(width_mm), 1))
    height = max(20.0, round(float(height_mm), 1))
    elements: list[Element] = []

    # Ticks: every millimetre on the left edge, every 5 mm a long tick and a number.
    y = 0.0
    index = 0
    while y <= height - 0.4:
        major = index % MAJOR_STEP_MM == 0
        tick_w = 4.0 if major else 1.8
        elements.append(Element(
            type="line", x=0.6, y=round(y, 2), w=tick_w, h=0.25, thickness_mm=0.25,
            label=f"{int(round(y))} mm",
        ))
        if major:
            elements.append(Element(
                type="text", x=0.6 + tick_w + 0.3, y=round(max(0.0, y - 1.6), 2),
                w=8.5, h=3.2, text=str(int(round(y))), font_size_pt=6,
                bold=False, align="left", valign="middle",
            ))
        y += MINOR_STEP_MM
        index += 1

    # A centre line and a hint so the strip is recognisable on a roll of identical labels.
    short_note = width < 16
    elements.append(Element(
        type="text", x=0.6 + 5.5, y=1.0, w=max(3.0, width - 6.5), h=4.0,
        text=note or ("GAP CAL." if short_note else "Gap calibration — read mm at the fold"),
        font_size_pt=6, align="left", valign="top",
    ))
    elements.append(Element(
        type="line", x=width * 0.32, y=6.0, w=0.3, h=max(1.0, height - 7.0), thickness_mm=0.3,
        label="centre line",
    ))
    return LabelSpec(
        name="Gap calibration strip",
        width_mm=width,
        height_mm=height,
        label_type=1,
        gap_mm=2.0,
        elements=elements,
    )
