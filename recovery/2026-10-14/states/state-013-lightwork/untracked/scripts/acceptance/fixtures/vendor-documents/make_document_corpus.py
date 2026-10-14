"""Generate the small, authored W6 corpus; optional development libraries only.

No customer data, downloads, macros, external relationships or executable payloads.
Regeneration requires python-docx, openpyxl, ReportLab and Pillow; extraction tests
consume the checked-in bytes and do not require these generator dependencies.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import docx
import openpyxl
import PIL
import reportlab
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent
STAMP = datetime(2026, 9, 1, tzinfo=timezone.utc)
POLICY = "Model provenance is documented with provider and version."
RETENTION = "Customer data is deleted within 30 days."
FONT_TEXT = "Mapped glyph canary: café, naïve, Ω."
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def normalized_package(raw, changes=None):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == "docProps/core.xml":
                node = ET.fromstring(content)
                for key in ("created", "modified"):
                    item = node.find("{http://purl.org/dc/terms/}" + key)
                    if item is not None:
                        item.text = "2026-09-01T00:00:00Z"
                content = ET.tostring(node, encoding="utf-8", xml_declaration=True)
            if changes and name in changes:
                content = changes[name](content)
            entry = zipfile.ZipInfo(name, date_time=(2026, 9, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(entry, content)
    return target.getvalue()


def office():
    word = Document()
    word.core_properties.author = "Lightwork authored corpus; no customer data"
    word.core_properties.created = word.core_properties.modified = STAMP
    word.add_paragraph("SYNTHETIC CORPUS WORD BODY")
    word.add_paragraph(POLICY)
    table = word.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Retention table label"
    table.cell(0, 1).text = RETENTION
    visible = word.add_paragraph("Visible paragraph remains.")
    visible.add_run(" HIDDEN WORD CANARY: never visible.").font.hidden = True
    word.sections[0].header.paragraphs[0].text = "HEADER CANARY: outside body scope."
    word.sections[0].footer.paragraphs[0].text = "FOOTER CANARY: outside body scope."
    stream = io.BytesIO()
    word.save(stream)
    raw = normalized_package(stream.getvalue())
    (ROOT / "corpus-word-layout.docx").write_bytes(raw)

    def tracked(content):
        node = ET.fromstring(content)
        paragraph = node.find("./" + W + "body/" + W + "p")
        change = ET.SubElement(paragraph, W + "ins", {W + "id": "1", W + "author": "Authored corpus", W + "date": "2026-09-01T00:00:00Z"})
        ET.SubElement(ET.SubElement(change, W + "r"), W + "t").text = "TRACKED CHANGE CANARY"
        return ET.tostring(node, encoding="utf-8", xml_declaration=True)
    (ROOT / "corpus-word-tracked.docx").write_bytes(normalized_package(raw, {"word/document.xml": tracked}))

    book = Workbook()
    book.properties.creator = "Lightwork authored corpus; no customer data"
    sheet = book.active
    sheet.title = "Visible controls"
    sheet.append(["SYNTHETIC CORPUS SHEET", "Statement", "Hidden column"])
    sheet.append(["Provenance", POLICY, "HIDDEN COLUMN CANARY"])
    sheet.append(["Retention", RETENTION])
    sheet.append(["Hidden row", "HIDDEN ROW CANARY"])
    sheet.row_dimensions[4].hidden = True
    sheet.column_dimensions["C"].hidden = True
    sheet.append(["Deliberately stale stored formula", "=1+2"])
    sheet.append(["Uncached formula", "=100+200"])
    hidden = book.create_sheet("Hidden controls")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "HIDDEN SHEET CANARY"
    stream = io.BytesIO()
    book.save(stream)

    def cached(content):
        node = ET.fromstring(content)
        for cell in node.iter(S + "c"):
            if cell.get("r") == "B5":
                cell.find(S + "v").text = "9"
        return ET.tostring(node, encoding="utf-8", xml_declaration=True)
    (ROOT / "corpus-sheet-visibility.xlsx").write_bytes(normalized_package(stream.getvalue(), {"xl/worksheets/sheet1.xml": cached}))


def pdfs():
    font_path = Path(reportlab.__file__).parent / "fonts/Vera.ttf"
    pdfmetrics.registerFont(TTFont("CorpusVera", str(font_path)))

    def create(name):
        pdf = canvas.Canvas(str(ROOT / name), pagesize=(612, 792), invariant=1, pageCompression=0)
        pdf.setAuthor("Lightwork authored corpus; no customer data")
        pdf.setTitle(name)
        pdf.setFont("Helvetica", 12)
        return pdf

    pdf = create("corpus-digital-pages.pdf")
    pdf.drawString(40, 700, POLICY)
    pdf.showPage()
    pdf.showPage()
    pdf.drawString(40, 700, RETENTION)
    pdf.save()
    pdf = create("corpus-multicolumn.pdf")
    pdf.setFont("Helvetica", 8)
    # Deliberately paint rows across columns. Text presence/page provenance does
    # not establish logical column reading order or reconstructed layout.
    for text, x, y in ((POLICY, 20, 720), ("RIGHT COLUMN FIRST", 320, 720),
                       (RETENTION, 20, 680), ("RIGHT COLUMN SECOND", 320, 680)):
        pdf.drawString(x, y, text)
    pdf.save()
    pdf = create("corpus-fontmapped.pdf")
    pdf.setFont("CorpusVera", 12)
    pdf.drawString(40, 700, POLICY)
    pdf.drawString(40, 670, FONT_TEXT)
    pdf.save()
    pdf = create("corpus-rotated.pdf")
    pdf.setPageRotation(90)
    pdf.drawString(40, 500, POLICY)
    pdf.save()
    pdf = create("corpus-blank.pdf")
    pdf.showPage()
    pdf.save()

    raster = Image.new("RGB", (1400, 260), "white")
    draw = ImageDraw.Draw(raster)
    font = ImageFont.truetype(str(font_path), 28)
    draw.text((25, 25), "AUTHORED SCAN; NOT A CUSTOMER RECORD", font=font, fill="black")
    draw.text((25, 105), POLICY, font=font, fill="black")
    for name, image in (("corpus-image-only.pdf", raster),
                        ("corpus-lowquality.pdf", ImageEnhance.Contrast(raster.resize((280, 52), Image.Resampling.BILINEAR)).enhance(0.3).filter(ImageFilter.GaussianBlur(0.7)))):
        pdf = create(name)
        pdf.drawImage(ImageReader(image), 20, 540, width=560, height=104)
        pdf.save()
    (ROOT / "corpus-malformed.pdf").write_bytes(b"%PDF-1.7\nAuthored malformed input: no catalog, pages, xref or trailer.\n%%EOF\n")
    return hashlib.sha256(font_path.read_bytes()).hexdigest()


def main():
    office()
    font_hash = pdfs()
    generated = {"python_docx": docx.__version__, "openpyxl": openpyxl.__version__,
        "reportlab": reportlab.Version, "pillow": PIL.__version__, "font_sha256": font_hash}
    write_manifest(generated)
    print(json.dumps({"generator": generated, "files": [{"file": path.name, "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(ROOT.glob("corpus-*"))]}, indent=2))


def write_manifest(generated):
    word_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    sheet_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    cases = [
        {"id": "word-layout", "file": "corpus-word-layout.docx", "mime": word_mime,
         "categories": ["word_body", "word_table", "word_header_omission", "word_footer_omission", "word_hidden_omission"],
         "ground_truth": [{"text": POLICY, "location": {"paragraph": 2}}, {"text": RETENTION, "location": {"paragraph": 4}}],
         "excluded": ["HEADER CANARY", "FOOTER CANARY", "HIDDEN WORD CANARY"],
         "baseline": {"status": "available", "method": "word_body_text", "found": [True, True], "cached_formula_values": False}},
        {"id": "word-tracked", "file": "corpus-word-tracked.docx", "mime": word_mime,
         "categories": ["word_tracked_change_refusal"], "ground_truth": [{"text": POLICY}], "excluded": [],
         "baseline": {"status": "refused", "error": "tracked changes require a reviewed plain-text copy", "found": [False]}},
        {"id": "sheet-visibility", "file": "corpus-sheet-visibility.xlsx", "mime": sheet_mime,
         "categories": ["excel_cells", "excel_hidden_row_omission", "excel_hidden_column_omission", "excel_hidden_sheet_omission", "excel_cached_value", "excel_uncached_omission"],
         "ground_truth": [{"text": POLICY, "location": {"sheet": "Visible controls", "cell": "B2"}},
                          {"text": RETENTION, "location": {"sheet": "Visible controls", "cell": "B3"}},
                          {"text": "9", "location": {"sheet": "Visible controls", "cell": "B5"}}],
         "excluded": ["HIDDEN ROW CANARY", "HIDDEN COLUMN CANARY", "HIDDEN SHEET CANARY", "100+200", "1+2"],
         "excluded_locations": [{"sheet": "Visible controls", "cell": "B6"}],
         "baseline": {"status": "available", "method": "excel_visible_cell_text", "found": [True, True, True], "cached_formula_values": True}},
        {"id": "digital-reference", "file": "northstar-policy.pdf", "mime": "application/pdf",
         "categories": ["pdf_digital_reference", "pdf_blank_middle_page"],
         "ground_truth": [{"text": POLICY, "pages": [1]}, {"text": "Retention policy requires deletion after 30 days.", "pages": [3]}], "excluded": [],
         "baseline": {"status": "available", "method": "pdf_page_content_streams", "found": [True, True], "page_count": 3}},
    ]
    profiles = [
        ("digital-pages", ["pdf_digital_reportlab", "pdf_blank_middle_page"], [{"text": POLICY, "pages": [1]}, {"text": RETENTION, "pages": [3]}]),
        ("multicolumn", ["pdf_multicolumn", "pdf_paint_order_not_reading_order"], [{"text": POLICY, "pages": [1]}, {"text": RETENTION, "pages": [1]}, {"text": "RIGHT COLUMN FIRST"}, {"text": "RIGHT COLUMN SECOND"}]),
        ("fontmapped", ["pdf_font_mapping", "pdf_unicode"], [{"text": POLICY, "pages": [1]}, {"text": FONT_TEXT, "pages": [1]}]),
        ("rotated", ["pdf_rotated_digital"], [{"text": POLICY, "pages": [1]}]),
        ("image-only", ["pdf_image_only", "ocr_not_evaluated"], [{"text": POLICY, "pages": [1]}]),
        ("lowquality", ["pdf_lowquality_scan", "ocr_not_evaluated"], [{"text": POLICY, "pages": [1]}]),
        ("blank", ["pdf_blank"], []),
        ("malformed", ["pdf_malformed"], []),
    ]
    for name, categories, truth in profiles:
        cases.append({"id": name, "file": "corpus-" + name + ".pdf", "mime": "application/pdf", "categories": categories,
                      "ground_truth": truth, "excluded": [],
                      "baseline": {"status": "extraction_failed", "method": "pdf_page_content_streams", "found": [False] * len(truth)},
                      "limitation": "Legacy page-stream parser cannot extract this producer/layout; no rendering or OCR result is claimed."})
    for name, filename, mime, length in (("pdf-oversize", "corpus-blank.pdf", "application/pdf", 16 * 1024 * 1024 + 1),
                                       ("office-oversize", "corpus-word-layout.docx", word_mime, 8 * 1024 * 1024 + 1)):
        cases.append({"id": name, "file": filename, "mime": mime, "categories": [name.replace("-", "_")],
                      "ground_truth": [], "excluded": [], "append_spaces_to_bytes": length,
                      "baseline": {"status": "refused", "error": "supported byte limit", "found": []}})
    for case in cases:
        raw = (ROOT / case["file"]).read_bytes()
        case.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if "append_spaces_to_bytes" in case:
            raw += b" " * (case["append_spaces_to_bytes"] - len(raw))
            case["derived_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest = {"schema": "lightwork.authored-vendor-document-corpus.v1", "authored_noncustomer": True,
                "profile": "legacy_page_stream_and_office_body_cells", "generator": generated,
                "limits": ["Authored selected cases, not representative accuracy or customer acceptance.",
                           "Omitted headers/hidden cells and cached formulas must be reviewed against originals.",
                           "Text and citation matches do not prove semantic reading order, compliance or OCR quality.",
                           "Renderer/OCR acceptance is a separate explicit backend profile."], "cases": cases}
    (ROOT / "document-corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
