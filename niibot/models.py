"""Known Niimbot models, label presets and paper helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import LabelType


@dataclass(frozen=True)
class PrinterModel:
    key: str
    name: str
    ids: tuple[int, ...]
    dpi: int
    printhead_px: int
    #: ``b1`` (B1 style job) or ``v4`` (newer 300 dpi job). ``None`` = untested.
    print_task: str | None = None

    @property
    def mm_per_px(self) -> float:
        return 25.4 / self.dpi

    @property
    def printhead_mm(self) -> float:
        return round(self.printhead_px * self.mm_per_px, 1)


#: Model table (ids / dpi / printhead width) taken from the vendor app's own model library.
#: ``print_task`` is ``None`` for models whose job format is decided by the protocol version.
MODELS: tuple[PrinterModel, ...] = (
    # --- B series (top-facing label printers) ---------------------------------
    PrinterModel("B1", "B1", (4096,), 203, 384, "b1"),
    PrinterModel("B1_SE", "B1 SE", (4098,), 203, 384, "b1"),
    PrinterModel("B1_PRO", "B1 Pro", (4097,), 300, 567, "v4"),
    PrinterModel("B2", "B2", (6913,), 203, 384),
    PrinterModel("B2_PRO", "B2 Pro", (6912,), 300, 567),
    PrinterModel("B203", "B203", (2816,), 203, 400),
    PrinterModel("B21", "B21", (768,), 203, 384),
    PrinterModel("B21_PRO", "B21 Pro", (785,), 300, 591, "v4"),
    PrinterModel("B21_C2B", "B21 (C2B)", (771, 775), 203, 384, "b1"),
    PrinterModel("B21_L2B", "B21 (L2B)", (769,), 203, 384),
    PrinterModel("B21S", "B21S", (777, 776), 203, 384),
    PrinterModel("B3", "B3", (52993,), 203, 600),
    PrinterModel("B31", "B31", (5632,), 203, 600),
    PrinterModel("B32", "B32", (2049,), 300, 851),
    PrinterModel("B32R", "B32R", (2050,), 300, 851),
    PrinterModel("B3S", "B3S", (256, 260, 262), 203, 576),
    PrinterModel("B3S_A", "B3S-A", (275,), 203, 576),
    PrinterModel("B3S_P", "B3S-P", (272,), 203, 576),
    PrinterModel("B4", "B4", (6656,), 203, 832),
    PrinterModel("B4_PRO", "B4 Pro", (6657,), 300, 1229),
    PrinterModel("B11", "B11", (51457,), 203, 384),
    PrinterModel("B16", "B16", (1792,), 203, 96),
    PrinterModel("B18", "B18", (3584,), 203, 96),
    PrinterModel("B18S", "B18S", (3585,), 203, 96),
    PrinterModel("B50", "B50", (51713,), 203, 400),
    PrinterModel("B50W", "B50W", (51714,), 203, 384),
    # --- A series -------------------------------------------------------------
    PrinterModel("A1_PRO", "A1 Pro", (7424,), 300, 296),
    PrinterModel("A20", "A20", (2817,), 203, 400),
    PrinterModel("A203", "A203", (2818,), 203, 400),
    PrinterModel("A63", "A63", (2054,), 300, 851),
    PrinterModel("A8", "A8", (273,), 203, 576),
    # --- narrow / handheld ----------------------------------------------------
    PrinterModel("D11", "D11", (512,), 203, 96),
    PrinterModel("D11S", "D11S", (514,), 203, 96),
    PrinterModel("D11_H", "D11_H", (528,), 300, 142, "v4"),
    PrinterModel("D11_PRO", "D11 Pro", (531,), 300, 142, "v4"),
    PrinterModel("D101", "D101", (2560,), 203, 192, "b1"),
    PrinterModel("D110", "D110", (2304, 2305), 203, 96),
    PrinterModel("D110_M", "D110_M", (2320,), 203, 96, "b1"),
    PrinterModel("D41", "D41", (2816,), 203, 400),
    PrinterModel("H1", "H1", (3840,), 203, 96),
    PrinterModel("H1S", "H1S", (4352,), 203, 96),
    PrinterModel("N1", "N1", (3586,), 203, 96, "b1"),
    PrinterModel("FUST", "Fust", (513,), 203, 96),
    # --- 300 dpi kiosk / industrial ------------------------------------------
    PrinterModel("C1", "C1", (5120,), 300, 178, "v4"),
    PrinterModel("EP1C", "EP1C", (5121,), 300, 178, "v4"),
    PrinterModel("EP2M_H", "EP2M-H", (4610,), 300, 567),
    PrinterModel("EP3M", "EP3M", (6402,), 300, 851),
    PrinterModel("K2", "K2", (6144,), 203, 448),
    PrinterModel("K3", "K3", (4864,), 203, 640),
    PrinterModel("K3_ITD", "K3-ITD", (4868,), 203, 640),
    PrinterModel("K3_W", "K3-W", (4865,), 203, 640),
    PrinterModel("K4", "K4", (7168,), 203, 832),
    PrinterModel("M2_H", "M2-H", (4608,), 300, 567, "b1"),
    PrinterModel("M3", "M3", (6400,), 300, 851),
    PrinterModel("MP3K", "MP3K", (4866,), 203, 640),
    PrinterModel("MP3K_W", "MP3K-W", (4867,), 203, 640),
    PrinterModel("P1", "P1", (1024,), 300, 697),
    PrinterModel("P1S", "P1S", (1025,), 300, 662),
    PrinterModel("P18", "P18", (1026,), 300, 662),
    PrinterModel("S1", "S1", (51458,), 203, 384),
    PrinterModel("S3", "S3", (51460,), 203, 384),
    PrinterModel("S6", "S6", (261, 259, 258, 257), 203, 576),
    PrinterModel("S6_P", "S6-P", (274,), 203, 600),
    PrinterModel("T2S", "T2S", (53250,), 203, 832),
    PrinterModel("T6", "T6", (51715,), 203, 384),
    PrinterModel("T7", "T7", (51717,), 203, 384),
    PrinterModel("T8", "T8", (51718,), 300, 567),
    PrinterModel("T8S", "T8S", (51719,), 300, 567),
    PrinterModel("JC_M90", "JC-M90", (51461,), 203, 384),
    PrinterModel("JCB3S", "JC-B3S", (2561,), 203, 576),
    PrinterModel("Z401", "Z401", (6145,), 203, 448),
)

MODEL_BY_ID: dict[int, PrinterModel] = {}
for _model in MODELS:
    for _model_id in _model.ids:
        # First entry wins: the table is ordered from the most likely model for an id.
        MODEL_BY_ID.setdefault(_model_id, _model)

UNKNOWN_MODEL = PrinterModel("UNKNOWN", "unknown", (), 203, 384, "b1")


def model_for_id(model_id: int | None) -> PrinterModel:
    """The model table entry for a reported id (``UNKNOWN_MODEL`` when unknown)."""
    if model_id is None:
        return UNKNOWN_MODEL
    return MODEL_BY_ID.get(int(model_id), UNKNOWN_MODEL)


def model_for_key(key: str | None) -> PrinterModel | None:
    """The model table entry for a stored ``model_key`` (``None`` when it is not ours)."""
    if not key:
        return None
    wanted = str(key).upper()
    return next((model for model in MODELS if model.key == wanted), None)


def model_with_dpi(dpi: int, printhead_px: int) -> PrinterModel:
    """A stand-in model for a printer we do not know but whose geometry was set by hand."""
    return PrinterModel("CUSTOM", f"custom {dpi} dpi", (), int(dpi), int(printhead_px), None)

#: Models whose firmware inverts the "lid closed" bit of the heartbeat.
INVERTED_LID_MODELS = frozenset({512, 513, 514, 272, 273, 274, 1792, 2304, 2560, 3584, 3840, 4352, 5120})

#: Models whose ``GetPaperInfo`` total height already includes the gap.
TOTAL_HEIGHT_INCLUDES_GAP = frozenset({785})

#: Models the vendor sells for black/red two-colour stock. The roll's RFID tag reports ordinary
#: gap labels, so the stock cannot be detected — but a model outside this set has no red at all,
#: and asking it to print red would give grey or black.
TWO_COLOUR_MODELS = frozenset({"B1_PRO", "B21_PRO", "B2_PRO"})


def model_for_id(model_id: int | None) -> PrinterModel:
    if model_id is None:
        return UNKNOWN_MODEL
    return MODEL_BY_ID.get(model_id, UNKNOWN_MODEL)


@dataclass(frozen=True)
class PaperPreset:
    name: str
    width_mm: float
    height_mm: float
    label_type: int = int(LabelType.WITH_GAPS)
    gap_mm: float = 2.0


#: Common Niimbot roll sizes (name, width mm, height mm).
PAPER_PRESETS: tuple[PaperPreset, ...] = (
    PaperPreset("50 × 30 mm", 50.0, 30.0),
    PaperPreset("50 × 50 mm", 50.0, 50.0),
    PaperPreset("50 × 80 mm", 50.0, 80.0),
    PaperPreset("40 × 60 mm", 40.0, 60.0),
    PaperPreset("40 × 40 mm", 40.0, 40.0),
    PaperPreset("40 × 30 mm", 40.0, 30.0),
    PaperPreset("40 × 20 mm", 40.0, 20.0),
    PaperPreset("30 × 45 mm", 30.0, 45.0),
    PaperPreset("30 × 30 mm", 30.0, 30.0),
    PaperPreset("30 × 20 mm", 30.0, 20.0),
    PaperPreset("30 × 15 mm", 30.0, 15.0),
    PaperPreset("25 × 10 mm", 25.0, 10.0),
    PaperPreset("15 × 30 mm", 15.0, 30.0),
    PaperPreset("12,5 × 74 mm", 12.5, 74.0),
    PaperPreset("12 × 40 mm", 12.0, 40.0),
    PaperPreset("12 × 22 mm", 12.0, 22.0),
    PaperPreset("continuous tape 50 mm", 50.0, 40.0, int(LabelType.CONTINUOUS), 0.0),
)


@dataclass
class ZoneShape:
    """One outline inside an item zone (millimetres from the page corner)."""

    kind: str = "rect"  # rect | circle
    x: float = 0.0
    y: float = 0.0
    w: float = 30.0
    h: float = 15.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneShape":
        return cls(
            kind="circle" if str(data.get("kind")) == "circle" else "rect",
            x=float(data.get("x") or 0.0),
            y=float(data.get("y") or 0.0),
            w=max(0.2, float(data.get("w") or 0.2)),
            h=max(0.2, float(data.get("h") or 0.2)),
        )


@dataclass
class Region:
    """One printable item zone the calibrator drew on the roll.

    A zone is *one* item: it may be made of several outlines (a circle plus a
    rectangle, say) that together form the printable area. ``x``/``y``/``w``/``h``
    are always the bounding box of those outlines, so code that only needs the
    footprint keeps working; ``shapes`` holds the individual outlines.

    ``shape`` is ``rect``, ``circle``, ``mixed`` (several outlines) or the legacy
    ``line``; coordinates are millimetres from the top-left corner of the label.
    """

    shape: str = "rect"
    x: float = 0.0
    y: float = 0.0
    w: float = 30.0
    h: float = 15.0
    label: str = ""
    angle: float = 0.0
    thickness_mm: float = 0.8
    shapes: list[ZoneShape] = field(default_factory=list)

    def outlines(self) -> list[ZoneShape]:
        """The zone's outlines, falling back to the bounding box for old records."""
        if self.shapes:
            return self.shapes
        return [ZoneShape(kind=self.shape, x=self.x, y=self.y, w=self.w, h=self.h)]

    def normalize(self) -> "Region":
        """Derive the bounding box and ``shape`` from the outlines."""
        usable = [item for item in self.shapes if item.w > 0.2 and item.h > 0.2]
        if not usable:
            self.shapes = []
            return self
        self.shapes = usable
        left = min(item.x for item in usable)
        top = min(item.y for item in usable)
        right = max(item.x + item.w for item in usable)
        bottom = max(item.y + item.h for item in usable)
        self.x, self.y = round(left, 3), round(top, 3)
        self.w, self.h = round(right - left, 3), round(bottom - top, 3)
        kinds = {item.kind for item in usable}
        self.shape = "mixed" if len(usable) > 1 else kinds.pop()
        return self

    def to_dict(self) -> dict:
        data = {
            "shape": self.shape,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "label": self.label,
            "angle": self.angle,
            "thickness_mm": self.thickness_mm,
        }
        if self.shapes:
            data["shapes"] = [item.to_dict() for item in self.shapes]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Region":
        shapes = [ZoneShape.from_dict(item) for item in (data.get("shapes") or []) if isinstance(item, dict)]
        region = cls(
            shape=str(data.get("shape") or "rect"),
            x=float(data.get("x") or 0.0),
            y=float(data.get("y") or 0.0),
            w=max(0.2, float(data.get("w") or 0.2)),
            h=max(0.2, float(data.get("h") or 0.2)),
            label=str(data.get("label") or ""),
            angle=float(data.get("angle") or 0.0),
            thickness_mm=float(data.get("thickness_mm") or 0.8),
            shapes=shapes,
        )
        return region.normalize() if shapes else region


@dataclass
class PaperProfile:
    """Remembered label geometry for a physical roll (keyed by RFID barcode)."""

    key: str
    name: str
    width_mm: float
    height_mm: float
    gap_mm: float = 2.0
    label_type: int = int(LabelType.WITH_GAPS)
    capacity: int | None = None
    #: Cells the calibrator drew; empty means "one cell = the whole label".
    regions: list[Region] = field(default_factory=list)
    #: Size of a single sticker on this roll; 0 means "the page is one sticker".
    sticker_w_mm: float = 0.0
    sticker_h_mm: float = 0.0
    row_space_mm: float = 0.0
    col_space_mm: float = 0.0
    #: How the stickers sit on the print page (1 × 1 = the page is one sticker).
    rows: int = 1
    cols: int = 1

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "gap_mm": self.gap_mm,
            "label_type": self.label_type,
            "capacity": self.capacity,
            "regions": [region.to_dict() for region in self.regions],
            "sticker_w_mm": self.sticker_w_mm,
            "sticker_h_mm": self.sticker_h_mm,
            "row_space_mm": self.row_space_mm,
            "col_space_mm": self.col_space_mm,
            "rows": self.rows,
            "cols": self.cols,
        }
