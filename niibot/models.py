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
class Region:
    """One printable cell the calibrator drew on the roll.

    ``shape`` is ``rect`` or ``circle``; coordinates are millimetres from the
    top-left corner of the label page, exactly as they print.
    """

    shape: str = "rect"
    x: float = 0.0
    y: float = 0.0
    w: float = 30.0
    h: float = 15.0
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "shape": self.shape,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "label": self.label,
        }


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
