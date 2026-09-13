"""Real offline engines. Required in the document-engines CI job; opt-in locally."""
from __future__ import annotations

import os
import zipfile

import pytest
from maverick import config
from maverick.sandbox.docker import DockerBackend
from maverick.tools import pdf_reader, spreadsheet

_IMAGE = os.environ.get("MAVERICK_TEST_DOCUMENT_IMAGE")
pytestmark = pytest.mark.skipif(not _IMAGE, reason="requires the real document engine image")


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {"documents": {
        "native_recalculation": True, "pdf_ocr": True, "macro_cell_edits": True,
        "engine_image": _IMAGE, "timeout": 120}})
    return DockerBackend(workdir=tmp_path / "workspace", image=_IMAGE)


def test_real_formula_calculation_across_sheets_and_unchanged_original_parts(worker):
    import openpyxl

    path = worker.workdir / "orders.xlsx"
    workbook = openpyxl.Workbook()
    orders = workbook.active
    orders.title = "Orders"
    orders.append(["Quantity", "Unit Price", "Net"])
    orders.append([10, 25, "=A2*B2"])
    orders.append([4, 12.5, "=A3*B3"])
    summary = workbook.create_sheet("Summary")
    summary["A1"] = "=SUM(Orders!C2:C3)"
    summary["A2"] = '=IF(A1>200,"Ready","Review")'
    summary["A3"] = "=ROUND(A1*0.07,2)"
    workbook.save(path)
    workbook.close()
    with zipfile.ZipFile(path) as archive:
        before = {name: archive.read(name) for name in archive.namelist()}
    result = spreadsheet._run({"op": "recalculate", "path": "orders.xlsx"}, worker)
    assert result.startswith("Recalculated 5"), result
    values = openpyxl.load_workbook(path, data_only=True)
    try:
        assert values["Orders"]["C2"].value == 250
        assert values["Summary"]["A1"].value == 300
        assert values["Summary"]["A2"].value == "Ready"
        assert values["Summary"]["A3"].value == 21
    finally:
        values.close()
    with zipfile.ZipFile(path) as archive:
        for name, raw in before.items():
            if not name.startswith("xl/worksheets/"):
                assert archive.read(name) == raw, name
    # Editing inputs invalidates old caches; a second real calculation must
    # use the new values rather than returning the first result again.
    assert spreadsheet._run({"op": "set_cell", "path": "orders.xlsx", "cell": "A2", "value": 20}, worker).startswith("set")
    assert spreadsheet._run({"op": "recalculate", "path": "orders.xlsx"}, worker).startswith("Recalculated")
    values = openpyxl.load_workbook(path, data_only=True)
    try:
        assert values["Summary"]["A1"].value == 550
    finally:
        values.close()


def test_real_formula_error_preserves_original(worker):
    import openpyxl
    path = worker.workdir / "invalid.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "=1/0"
    workbook.save(path)
    workbook.close()
    original = path.read_bytes()
    result = spreadsheet._run({"op": "recalculate", "path": "invalid.xlsx"}, worker)
    assert result.startswith("ERROR:") and "calculation errors" in result, result
    assert path.read_bytes() == original


def test_real_scanned_pdf_ocr_reads_invoice_numbers(worker):
    from PIL import Image, ImageDraw, ImageFont
    path = worker.workdir / "invoice.pdf"
    raster = Image.new("RGB", (1400, 800), "white")
    font = ImageFont.truetype("DejaVuSans.ttf", 44)
    ImageDraw.Draw(raster).multiline_text((70, 80), "INVOICE 1042\nQuantity 10\nUnit price 25.00\nTotal 250.00",
                                         font=font, fill="black", spacing=35)
    raster.save(path, "PDF", resolution=150)
    result = pdf_reader._run_read_pdf({"source": "invoice.pdf", "pages": "1", "ocr": True}, worker)
    assert "[OCR]" in result and "1042" in result and "250.00" in result, result


def test_real_macro_preserving_edit_and_calculation(worker):
    import openpyxl
    path = worker.workdir / "macro.xlsm"
    wb = openpyxl.Workbook()
    wb.active["A1"] = 10
    wb.active["B1"] = "=A1*25"
    wb.save(path)
    wb.close()
    # Opaque binary preservation fixture. This does not claim execution or
    # compatibility of a customer's real VBA program.
    macro = b"opaque-VBA-preservation-fixture"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("xl/vbaProject.bin", macro)
    assert spreadsheet._run({"op": "set_cell", "path": "macro.xlsm", "cell": "A1", "value": 20}, worker).startswith("set")
    result = spreadsheet._run({"op": "recalculate", "path": "macro.xlsm"}, worker)
    assert result.startswith("Recalculated 1"), result
    with zipfile.ZipFile(path) as archive:
        assert archive.read("xl/vbaProject.bin") == macro
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        assert wb.active["B1"].value == 500
    finally:
        wb.close()
