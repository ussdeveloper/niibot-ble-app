"""Tiny spreadsheet reader: XLSX (zip + XML) and CSV, no third-party dependencies."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _column_index(reference: str) -> int:
    letters = re.match(r"([A-Z]+)", reference or "A")
    index = 0
    for char in letters.group(1) if letters else "A":
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def read_xlsx(payload: bytes) -> list[list[str]]:
    """Return the first worksheet as a list of rows (all cells are strings)."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{NS}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{NS}t")))

        sheets = sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
        if not sheets:
            return []
        root = ElementTree.fromstring(archive.read(sheets[0]))

    rows: list[list[str]] = []
    for row in root.iter(f"{NS}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{NS}c"):
            index = _column_index(cell.get("r", "A1"))
            kind = cell.get("t")
            value_node = cell.find(f"{NS}v")
            inline = cell.find(f"{NS}is")
            if kind == "s" and value_node is not None:
                try:
                    value = shared[int(value_node.text or "0")]
                except (ValueError, IndexError):
                    value = ""
            elif kind == "inlineStr" and inline is not None:
                value = "".join(node.text or "" for node in inline.iter(f"{NS}t"))
            else:
                value = value_node.text if value_node is not None else ""
            cells[index] = (value or "").strip()
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(position, "") for position in range(width)])
    return rows


def read_csv(payload: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 always decodes
        text = payload.decode("latin-1", "replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [[cell.strip() for cell in row] for row in reader]


def read_table(filename: str, payload: bytes) -> list[list[str]]:
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return read_xlsx(payload)
    if name.endswith(".csv") or name.endswith(".txt"):
        return read_csv(payload)
    raise ValueError("Unsupported file type — use .xlsx or .csv")
