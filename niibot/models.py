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


MODELS: tuple[PrinterModel, ...] = (
    PrinterModel("B1", "B1", (4096,), 203, 384, "b1"),
    PrinterModel("B1_SE", "B1 SE", (4098,), 203, 384, "b1"),
    PrinterModel("B1_PRO", "B1 Pro", (4097,), 300, 567, "v4"),
    PrinterModel("M2_H", "M2-H", (4608,), 300, 567, "b1"),
    PrinterModel("N1", "N1", (3586,), 203, 96, "b1"),
    PrinterModel("D101", "D101", (2560,), 203, 192, "b1"),
    PrinterModel("D110_M", "D110_M", (2320,), 203, 96, "b1"),
    PrinterModel("B21_C2B", "B21 (C2B)", (771, 775), 203, 384, "b1"),
    PrinterModel("B21_PRO", "B21 Pro", (785,), 300, 591, "v4"),
    PrinterModel("D11_H", "D11_H", (528,), 300, 142, "v4"),
    PrinterModel("C1", "C1", (5120,), 300, 178, "v4"),
    PrinterModel("EP1C", "EP1C", (5121,), 300, 178, "v4"),
    PrinterModel("B21", "B21", (768,), 203, 384),
    PrinterModel("B21S", "B21S", (777, 776), 203, 384),
    PrinterModel("D11", "D11", (512,), 203, 96),
    PrinterModel("D11S", "D11S", (514,), 203, 96),
    PrinterModel("D110", "D110", (2304, 2305), 203, 96),
    PrinterModel("B18", "B18", (3584,), 203, 96),
    PrinterModel("B2", "B2", (6913,), 203, 384),
    PrinterModel("B2_PRO", "B2 Pro", (6912,), 300, 567),
)

MODEL_BY_ID: dict[int, PrinterModel] = {model_id: model for model in MODELS for model_id in model.ids}

UNKNOWN_MODEL = PrinterModel("UNKNOWN", "unknown", (), 203, 384, "b1")

#: Models whose firmware inverts the "lid closed" bit of the heartbeat.
INVERTED_LID_MODELS = frozenset({512, 513, 514, 272, 273, 274, 1792, 2304, 2560, 3584, 3840, 4352, 5120})

#: Models whose ``GetPaperInfo`` total height already includes the gap.
TOTAL_HEIGHT_INCLUDES_GAP = frozenset({785})


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
        }
