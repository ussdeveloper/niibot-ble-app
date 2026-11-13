"""Label document model shared by the API, the renderer and the UI."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

ELEMENT_TYPES = ("text", "barcode", "qr", "rect", "line", "image")

BARCODE_FORMATS = ("code128", "code39", "ean13", "ean8", "upca", "itf")

#: How an inserted picture is fitted into its box.
IMAGE_FITS = ("cover", "contain", "stretch")


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class Element(BaseModel):
    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=new_id)
    type: str = "text"
    x: float = 0.0
    y: float = 0.0
    w: float = 30.0
    h: float = 10.0
    rotation: int = 0
    #: Layer visibility (eye icon in the layers panel).
    visible: bool = True
    #: Optional custom layer name; falls back to the element content.
    label: str | None = None

    # text
    text: str = ""
    font: str = "Arial"
    font_size_pt: float = 9.0
    bold: bool = False
    italic: bool = False
    align: str = "left"
    valign: str = "middle"
    line_spacing: float = 1.15
    auto_fit: bool = True

    # barcode / qr
    barcode_format: str = "code128"
    show_text: bool = False
    ec_level: str = "M"

    # shapes
    fill: bool = False
    thickness_mm: float = 0.3

    # image
    image_id: str | None = None
    #: How the picture fills its box: ``cover`` crops, ``contain`` letterboxes, ``stretch`` distorts.
    image_fit: str = "cover"
    #: Extra magnification on top of the fit (1.0 = exactly the fit size).
    image_zoom: float = 1.0
    #: Crop window offset in millimetres — moves the picture inside the box.
    image_offset_x: float = 0.0
    image_offset_y: float = 0.0
    #: Halftone the picture instead of hard-thresholding it — keeps gradients readable.
    image_dither: bool = False

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if value not in ELEMENT_TYPES:
            raise ValueError(f"unknown element type: {value}")
        return value

    @field_validator("image_fit")
    @classmethod
    def _check_image_fit(cls, value: str) -> str:
        if value not in IMAGE_FITS:
            raise ValueError(f"unknown image fit: {value}")
        return value

    @field_validator("rotation")
    @classmethod
    def _check_rotation(cls, value: int) -> int:
        return (round(int(value) / 90) * 90) % 360


class SheetColumn(BaseModel):
    """A sheet column bound to one element of the label."""

    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=new_id)
    element_id: str
    name: str = ""


class SheetData(BaseModel):
    """Data sheet attached to a label: columns, extra print rows and mirror options."""

    model_config = {"extra": "ignore"}

    columns: list[SheetColumn] = Field(default_factory=list)
    #: extra rows printed after the current design; keys are column ids
    rows: list[dict[str, str]] = Field(default_factory=list)
    #: fill regions 2..N with a copy of region 1 while printing
    mirror: bool = False
    #: mirror = vertical flip instead of a plain copy
    mirror_flip: bool = False


class LabelSpec(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = "New label"
    width_mm: float = 50.0
    height_mm: float = 30.0
    gap_mm: float = 2.0
    label_type: int = 1
    density: int = 3
    copies: int = 1
    elements: list[Element] = Field(default_factory=list)
    #: optional data sheet (columns + print rows) shipped with the label
    sheet: SheetData | None = None


def default_spec() -> LabelSpec:
    return LabelSpec(
        name="Business card",
        width_mm=48,
        height_mm=30,
        elements=[
            Element(
                type="text",
                x=2,
                y=2,
                w=44,
                h=9,
                text="Company name",
                font="Arial",
                font_size_pt=14,
                bold=True,
                align="center",
            ),
            Element(
                type="text",
                x=2,
                y=11,
                w=44,
                h=6,
                text="John Smith",
                font_size_pt=10,
                align="center",
            ),
            Element(
                type="text",
                x=2,
                y=17,
                w=44,
                h=5,
                text="tel. 555 010 200",
                font_size_pt=8,
                align="center",
            ),
            Element(
                type="barcode",
                x=3,
                y=22,
                w=42,
                h=6,
                text="1234567890",
                barcode_format="code128",
            ),
        ],
    )


TEMPLATES: dict[str, LabelSpec] = {
    "wizytowka": default_spec(),
    "adres": LabelSpec(
        name="Address label",
        width_mm=48,
        height_mm=30,
        elements=[
            Element(type="text", x=2, y=2, w=44, h=5, text="Recipient", font_size_pt=9, bold=True),
            Element(type="text", x=2, y=7, w=44, h=11, text="ACME Components Ltd\n12 Example Street\n00-001 Sample City", font_size_pt=8),
            Element(type="qr", x=32, y=19, w=13, h=10, text="https://example.com/paczka/123"),
        ],
    ),
    "kabel": LabelSpec(
        name="Cable marker",
        width_mm=12.5,
        height_mm=74,
        elements=[
            Element(type="text", x=0, y=2, w=12.5, h=30, text="PATCH\n01", font_size_pt=12, bold=True, align="center", rotation=0),
            Element(type="text", x=0, y=40, w=12.5, h=30, text="SRV-A / eth0", font_size_pt=8, align="center"),
        ],
    ),
    "qr": LabelSpec(
        name="QR label",
        width_mm=30,
        height_mm=30,
        elements=[
            Element(type="qr", x=3, y=3, w=24, h=24, text="https://niimbot.example"),
        ],
    ),
    # 40 × 30 mm roll (two 40 × 15 mm strips) — the most common B1 consumable
    "produkt40": LabelSpec(
        name="Product 40×30",
        width_mm=40,
        height_mm=30,
        elements=[
            Element(type="text", x=2, y=2, w=36, h=6, text="PRODUCT NAME", font="Arial Black", font_size_pt=11, align="center", auto_fit=True),
            Element(type="text", x=2, y=8.5, w=36, h=4.5, text="SKU 0001 · Batch 01/26", font_size_pt=7.5, align="center", auto_fit=True),
            Element(type="barcode", x=3, y=14, w=34, h=7, text="SKU-0001", barcode_format="code128", show_text=True, font_size_pt=7),
        ],
    ),
    "adres40": LabelSpec(
        name="Address 40×30",
        width_mm=40,
        height_mm=30,
        elements=[
            Element(type="text", x=2, y=2, w=36, h=4.5, text="RECIPIENT", font_size_pt=8, bold=True, auto_fit=True),
            Element(type="text", x=2, y=6.5, w=25, h=12, text="ACME Components Ltd\n12 Example Street\n00-001 Sample City", font_size_pt=7, line_spacing=1.15, auto_fit=True),
            Element(type="qr", x=28, y=7, w=10, h=10, text="https://example.com/parcel/0001"),
            Element(type="text", x=2, y=19, w=36, h=3.5, text="Order 0001 · 1.25 kg", font_size_pt=7, align="center", auto_fit=True),
        ],
    ),
}
