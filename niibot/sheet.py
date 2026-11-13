"""Data sheet helpers: column binding, mirroring multi-region labels, print queue."""

from __future__ import annotations

from .spec import Element, LabelSpec, SheetColumn

#: element types whose value can come from a sheet column
VALUE_TYPES = ("text", "barcode", "qr")


def _value_elements(spec: LabelSpec) -> list[Element]:
    return [element for element in spec.elements if element.type in VALUE_TYPES]


def sync_columns(spec: LabelSpec) -> list[SheetColumn]:
    """Return columns for every value-bearing element, keeping the user's names."""
    existing = {column.element_id: column for column in (spec.sheet.columns if spec.sheet else [])}
    columns: list[SheetColumn] = []
    for element in _value_elements(spec):
        column = existing.get(element.id)
        if column is None:
            column = SheetColumn(
                id=element.id,
                element_id=element.id,
                name=_default_column_name(element),
            )
        columns.append(column)
    return columns


def _default_column_name(element: Element) -> str:
    if element.label:
        return element.label
    if element.type == "text":
        return (element.text or "Tekst").split("\n")[0][:24] or "Tekst"
    if element.type == "barcode":
        return "Kod kreskowy"
    return "Kod QR"


def apply_row(spec: LabelSpec, columns: list[SheetColumn], row: dict[str, str]) -> LabelSpec:
    """Return a copy of *spec* with the row values written into the bound elements."""
    bindings = {column.id: column.element_id for column in columns}
    update: dict[str, str] = {}
    for column_id, value in (row or {}).items():
        element_id = bindings.get(column_id)
        if element_id and value is not None:
            update[element_id] = str(value)
    if not update:
        return spec.model_copy(deep=True)

    elements = []
    for element in spec.elements:
        if element.id in update and element.type in VALUE_TYPES:
            elements.append(element.model_copy(update={"text": update[element.id]}))
        else:
            elements.append(element.model_copy(deep=True))
    return spec.model_copy(update={"elements": elements}, deep=False)


def apply_mirror(spec: LabelSpec, regions: int, flip: bool = False) -> LabelSpec:
    """Fill regions 2..N with a copy (or a vertical mirror) of region 1.

    Everything that already lives in the lower regions is replaced — with mirror on,
    the first region is the only thing the user edits.
    """
    regions = max(1, int(regions))
    if regions < 2 or spec.height_mm <= 0:
        return spec

    band = spec.height_mm / regions
    template: list[Element] = []
    untouched: list[Element] = []
    for element in spec.elements:
        center = element.y + element.h / 2
        if center < band:
            template.append(element)
        elif element.visible is False:
            untouched.append(element)

    copies: list[Element] = []
    for index in range(1, regions):
        offset = index * band
        for element in template:
            if flip:
                y = offset + (band - element.y - element.h)
                rotation = (180 - element.rotation) % 360
            else:
                y = element.y + offset
                rotation = element.rotation
            copies.append(
                element.model_copy(
                    update={
                        "id": f"{element.id}-r{index}",
                        "y": round(y, 3),
                        "rotation": rotation,
                    },
                    deep=True,
                )
            )

    return spec.model_copy(update={"elements": [*untouched, *template, *copies]}, deep=False)


def build_sheet_specs(
    spec: LabelSpec,
    columns: list[SheetColumn],
    rows: list[dict[str, str]],
    *,
    include_current: bool = True,
    regions: int = 1,
    flip: bool = False,
) -> list[LabelSpec]:
    """Specs for a sheet print run: the current design first, then every sheet row."""
    specs: list[LabelSpec] = []

    def prepare(candidate: LabelSpec) -> LabelSpec:
        return apply_mirror(candidate, regions, flip) if regions > 1 else candidate

    if include_current:
        specs.append(prepare(spec.model_copy(deep=True)))
    for row in rows:
        specs.append(prepare(apply_row(spec, columns, row)))
    return specs
