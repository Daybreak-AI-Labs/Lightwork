"""Editable Word answers using Chat's current, selected-answer release policy."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from maverick.docx_redline import build_docx

from .goal_export_ops_api import _current_export, _export_source

router = APIRouter(tags=["chat-answer-exports"])
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML = "http://www.w3.org/XML/1998/namespace"
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_HEADING = re.compile(r"^(#{1,6}) ([^#].*)$")
_BULLET = re.compile(r"^[-+*] (\S.*)$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})([^`~]*)$")


def _runs(text):
    # Inline code, links and nested emphasis remain literal instead of guessed.
    if "`" in text:
        return [(text, False)]
    runs, start = [], 0
    for match in _BOLD.finditer(text):
        runs.extend([(text[start:match.start()], False), (match[1], {"bold": True})])
        start = match.end()
    return runs + [(text[start:], False)]


def _blocks(text, title):
    blocks = [("title", [(title, False)])] if title else []
    fence, opening = None, None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        marker = _FENCE.fullmatch(line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                blocks[opening] = ("skip", [])
                fence = None
                continue
            blocks.append(("code", [(line, False)]))
            continue
        if marker:
            fence = marker[1]
            opening = len(blocks)
            blocks.append(("code", [(line, False)]))
            continue
        heading, bullet = _HEADING.fullmatch(line), _BULLET.fullmatch(line)
        if heading:
            blocks.append(("heading", _runs(heading[2])))
        elif bullet:
            blocks.append(("bullet", _runs(bullet[1])))
        else:
            # Paragraph-per-line retains blanks, indentation and complex layouts.
            blocks.append(("para", _runs(line)))
    return [block for block in blocks if block[0] != "skip"]


def word_answer(text: str, title: str) -> bytes:
    """Basic business formatting; exact original Markdown retained in the ZIP."""
    if any(not (char in "\t\n\r" or 32 <= ord(char) <= 0xD7FF
                or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
           for char in text + title):
        raise HTTPException(409, "This answer contains characters Word cannot display. Download Markdown to keep the complete original.")
    blocks = _blocks(text, title)
    original = build_docx(blocks)
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    settings = ET.fromstring(parts["word/settings.xml"])
    for node in list(settings):
        if node.tag in {f"{{{_W}}}trackRevisions", f"{{{_W}}}trackChanges", f"{{{_W}}}documentProtection"}:
            settings.remove(node)
    parts["word/settings.xml"] = ET.tostring(settings, encoding="utf-8", xml_declaration=True)
    styles = ET.fromstring(parts["word/styles.xml"])
    for node in styles.iter(f"{{{_W}}}color"):
        node.set(f"{{{_W}}}val", "000000")
    for paragraph in styles.iter(f"{{{_W}}}pPr"):
        for node in list(paragraph):
            if node.tag == f"{{{_W}}}pBdr":
                paragraph.remove(node)
    parts["word/styles.xml"] = ET.tostring(styles, encoding="utf-8", xml_declaration=True)
    document = ET.fromstring(parts["word/document.xml"])
    body = document.find(f"{{{_W}}}body")
    paragraphs = body.findall(f"{{{_W}}}p")
    for paragraph, block in zip(paragraphs, blocks, strict=True):
        if block[0] == "code":
            ppr = ET.Element(f"{{{_W}}}pPr")
            paragraph.insert(0, ppr)
            ET.SubElement(ppr, f"{{{_W}}}spacing", {f"{{{_W}}}after": "0"})
            for run in paragraph.findall(f"{{{_W}}}r"):
                props = ET.Element(f"{{{_W}}}rPr")
                run.insert(0, props)
                ET.SubElement(props, f"{{{_W}}}rFonts", {f"{{{_W}}}ascii": "Consolas", f"{{{_W}}}hAnsi": "Consolas"})
                ET.SubElement(props, f"{{{_W}}}sz", {f"{{{_W}}}val": "19"})
        for run in paragraph.findall(f"{{{_W}}}r"):
            for node in list(run):
                if node.tag != f"{{{_W}}}t" or "\t" not in (node.text or ""):
                    continue
                position = list(run).index(node)
                for index, value in enumerate((node.text or "").split("\t")):
                    if index:
                        run.insert(position, ET.Element(f"{{{_W}}}tab"))
                        position += 1
                    segment = ET.Element(f"{{{_W}}}t", {f"{{{_XML}}}space": "preserve"})
                    segment.text = value
                    run.insert(position, segment)
                    position += 1
                run.remove(node)
    section = body.find(f"{{{_W}}}sectPr")
    for node in list(section):
        if node.tag == f"{{{_W}}}headerReference":
            section.remove(node)
    parts["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    # Keep a neutral page footer; no report, legal-review or redline branding.
    footer = ET.fromstring(parts["word/footer1.xml"])
    first_text = footer.find(f".//{{{_W}}}t")
    if first_text is not None:
        first_text.text = ""
    parts["word/footer1.xml"] = ET.tostring(footer, encoding="utf-8", xml_declaration=True)
    parts["lightwork/source.md"] = text.encode("utf-8")
    types = ET.fromstring(parts["[Content_Types].xml"])
    ET.SubElement(types, f"{{{_TYPES}}}Default", {"Extension": "md", "ContentType": "text/markdown"})
    parts["[Content_Types].xml"] = ET.tostring(types, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


@router.get("/chat/goal/{goal_id}/download.docx")
def download_word_answer(request: Request, goal_id: int, turn_id: int | None = None) -> Response:
    from .app import download_chat_answer

    _world, goal, _artifacts, version = _export_source(request, goal_id)
    selected = download_chat_answer(request, goal_id, turn_id)
    text = selected.body.decode("utf-8")
    document = word_answer(text, goal.title)
    _current_export(request, goal_id, version)
    current = download_chat_answer(request, goal_id, turn_id)
    if current.body != selected.body:
        raise HTTPException(409, "This answer changed. Reload before downloading its Word document.")
    _current_export(request, goal_id, version)
    suffix = f"-message-{turn_id}" if turn_id is not None else ""
    return Response(document, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={
        "Content-Disposition": f'attachment; filename="lightwork-answer-{goal_id}{suffix}.docx"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })
