"""Parse an uploaded Test Document (xlsx) into structured scenario rows.

Pure-stdlib implementation (zipfile + ElementTree) so no third-party
dependency is required. Handles both inline strings and the shared-strings
table, iterates every worksheet, and maps the known test-case columns.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Canonical column names found in uploaded test-case workbooks.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "test_id": ("test id", "id", "testcase", "test case id"),
    "test_scenario": ("test scenario", "scenario", "title", "test case"),
    "pre_conditions": ("pre-conditions", "pre conditions", "precondition", "pre-condition", "pre condition"),
    "test_steps": ("test steps", "steps", "test steps / instructions", "step description"),
    "expected_result": ("expected result", "expected outcome", "expected", "result"),
    "priority": ("priority", "severity"),
    "test_type": ("test type", "type", "category"),
}

_OUTPUT_KEYS = ("test_id", "test_scenario", "pre_conditions", "test_steps", "expected_result", "priority", "test_type")


def _cells_in_sheet(sheet_root: ET.Element, shared_strings: list[str]) -> list[dict[str, str]]:
    """Extract a list of {column_ref: value} for every populated row."""
    rows: list[dict[str, str]] = []
    sheet_data = sheet_root.find("./m:sheetData", _XLSX_NS)
    if sheet_data is None:
        return rows

    for row_el in sheet_data.findall("./m:row", _XLSX_NS):
        cells: dict[str, str] = {}
        for cell_el in row_el.findall("./m:c", _XLSX_NS):
            ref = cell_el.get("r", "")
            col = "".join(ch for ch in ref if ch.isalpha()).upper()
            cell_type = cell_el.get("t")
            value = ""
            if cell_type == "inlineStr":
                text_els = cell_el.findall(".//m:t", _XLSX_NS)
                value = "".join(text_el.text or "" for text_el in text_els)
            elif cell_type == "s":
                v_el = cell_el.find("./m:v", _XLSX_NS)
                if v_el is not None and v_el.text is not None:
                    try:
                        value = shared_strings[int(v_el.text)]
                    except (ValueError, IndexError):
                        value = ""
            else:
                v_el = cell_el.find("./m:v", _XLSX_NS)
                value = v_el.text if v_el is not None and v_el.text is not None else ""
            cells[col] = value.strip()
        if any(cells.values()):
            rows.append(cells)
    return rows


def parse_test_document_xlsx(path: str | Path) -> list[dict[str, str]]:
    """Return a flat list of scenario rows parsed from the xlsx file (all sheets)."""
    sheets = parse_test_document_sheets(path)
    return [row for sheet in sheets for row in sheet["items"]]


def parse_test_document_sheets(path: str | Path) -> list[dict[str, str | list[dict[str, str]]]]:
    """Return scenario rows grouped by worksheet.

    Every worksheet is scanned for a header row that contains the known
    test-case column labels. Rows following that header are mapped to the
    canonical output keys and grouped under the sheet name. Sheets with no
    recognized header are excluded.
    """
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        try:
            ss_xml = archive.read("xl/sharedStrings.xml")
            ss_root = ET.fromstring(ss_xml)
            shared_strings = [
                "".join(t.text or "" for t in si.findall(".//m:t", _XLSX_NS))
                for si in ss_root.findall("./m:si", _XLSX_NS)
            ]
        except KeyError:
            pass

        rels: dict[str, str] = {}
        try:
            rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rels_root:
                rels[rel.get("Id", "")] = rel.get("Target", "")
        except KeyError:
            pass

        # Ordered list of (sheet_name, target) resolved through workbook.xml.
        declared_sheets: list[tuple[str, str | None]] = []
        try:
            wb_root = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets_el = wb_root.find("./m:sheets", _XLSX_NS)
            if sheets_el is not None:
                for sheet_el in sheets_el.findall("./m:sheet", _XLSX_NS):
                    name = sheet_el.get("name") or ""
                    rel_id = sheet_el.get(f"{{{_REL_NS}}}id") or ""
                    declared_sheets.append((name, rels.get(rel_id)))
        except KeyError:
            pass

        # Fallback when workbook.xml / rels could not be parsed.
        if not declared_sheets:
            for i in range(1, 32):
                declared_sheets.append((f"Sheet {i}", f"xl/worksheets/sheet{i}.xml"))

        all_sheet_names = set(archive.namelist())

        sheets: list[dict[str, str | list[dict[str, str]]]] = []
        for name, target in declared_sheets:
            if not target:
                continue
            candidate = target.lstrip("/")
            if not candidate.startswith("xl/"):
                candidate = f"xl/{candidate}"
            if candidate not in all_sheet_names:
                resolved = next((n for n in all_sheet_names if n.endswith(target)), None)
                if resolved is None:
                    continue
                candidate = resolved
            try:
                sheet_root = ET.fromstring(archive.read(candidate))
            except ET.ParseError:
                continue
            rows = _cells_in_sheet(sheet_root, shared_strings)
            mapped = _map_sheet_rows(rows)
            if mapped:
                sheets.append({"name": name, "items": _dedupe_rows(mapped)})

    return sheets


def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        key = tuple(row.get(k, "") for k in _OUTPUT_KEYS)
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace("\n", " ")


def _map_sheet_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Map raw cell rows to canonical scenario rows using the header row."""
    if not rows:
        return []

    header_row: dict[str, str] | None = None
    for row in rows:
        labels = [_normalize_header(v) for v in row.values() if v]
        if any(labels):
            header_row = row
            break
    if header_row is None:
        return []

    columns: list[str] = []
    for col in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if col in header_row and header_row[col]:
            columns.append(col)

    # Build column -> canonical key mapping.
    col_to_key: dict[str, str] = {}
    for col in columns:
        normalized = _normalize_header(header_row[col])
        for key, aliases in _COLUMN_ALIASES.items():
            if normalized in aliases:
                col_to_key[col] = key
                break

    # A sheet only "counts" if it has a test id or test scenario column.
    if "test_id" not in col_to_key.values() and "test_scenario" not in col_to_key.values():
        return []

    output: list[dict[str, str]] = []
    for row in rows[1:]:
        mapped: dict[str, str] = {}
        for col, key in col_to_key.items():
            value = row.get(col, "").strip()
            if value:
                mapped[key] = value
        if mapped.get("test_id") or mapped.get("test_scenario"):
            output.append(mapped)
    return output