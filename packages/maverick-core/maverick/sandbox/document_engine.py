"""Standalone helper copied into an isolated, offline document worker.

Requires Debian's python3-uno, libreoffice-calc, poppler-utils and tesseract-ocr.
Never import this helper to start a process in the credential-bearing harness.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _run(argv, timeout=30):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed with exit code {result.returncode}")
    return result.stdout


def _pages(spec, total, limit):
    if not spec:
        if total > limit:
            raise ValueError(f"OCR is limited to {limit} pages per call; select a page range")
        return list(range(1, total + 1))
    selected = set()
    for part in spec.split(","):
        match = re.fullmatch(r"\s*([1-9][0-9]*)(?:-([1-9][0-9]*)?)?\s*", part)
        if match is None:
            raise ValueError("Invalid OCR page range")
        start = int(match[1])
        end = int(match[2]) if match[2] else (total if "-" in part else start)
        end = min(total, end)
        if end - start + 1 > limit:
            raise ValueError(f"OCR is limited to {limit} pages per call")
        selected.update(range(start, end + 1))
        if len(selected) > limit:
            raise ValueError(f"OCR is limited to {limit} pages per call")
    if not selected:
        raise ValueError("No PDF pages match the selection")
    return sorted(selected)


def ocr(request):
    info = _run(["pdfinfo", "input.pdf"])
    match = re.search(r"^Pages:\s+([0-9]+)\s*$", info, re.MULTILINE)
    if match is None:
        raise ValueError("PDF page count is unavailable")
    total = int(match[1])
    language = request["language"]
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ValueError("Invalid OCR language selection")
    chunks = []
    for page in _pages(request["pages"], total, request["max_pages"]):
        _run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile",
              "-scale-to", "2200", "-png", "input.pdf", "page"])
        try:
            text = _run(["tesseract", "page.png", "stdout", "-l", language]).strip()
        finally:
            Path("page.png").unlink(missing_ok=True)
        chunks.append(f"=== Page {page} of {total} [OCR] ===\n{text}")
        if sum(map(len, chunks)) > 1_000_000:
            raise ValueError("OCR text exceeds the one-million character limit")
    if not any(chunk.split("===\n", 1)[-1].strip() for chunk in chunks):
        raise ValueError("OCR found no readable text in the selected pages")
    return {"text": "\n\n".join(chunks), "engine": "Tesseract", "pages": len(chunks)}


def _property(uno, name, value):
    prop = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    prop.Name, prop.Value = name, value
    return prop


def _connect(uno, pipe):
    context = uno.getComponentContext()
    resolver = context.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", context)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            return resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
        except Exception:
            time.sleep(.1)
    raise RuntimeError("LibreOffice did not start within 30 seconds")


def recalculate(request):
    import uno

    pipe = "lightwork_" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="lightwork-office-") as profile:
        args = ["soffice", "--headless", "--norestore", "--nodefault", "--nofirststartwizard",
                "-env:UserInstallation=" + Path(profile).as_uri(),
                f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        document = None
        try:
            context = _connect(uno, pipe)
            desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
            properties = tuple(_property(uno, name, value) for name, value in [
                ("Hidden", True), ("ReadOnly", False),
                # SDK constants: MacroExecMode.NEVER_EXECUTE = 0;
                # UpdateDocMode.NO_UPDATE = 0. Never execute workbook macros.
                ("MacroExecutionMode", 0), ("UpdateDocMode", 0),
            ])
            document = desktop.loadComponentFromURL(Path("input.xlsx").resolve().as_uri(), "_blank", 0, properties)
            if document is None or not hasattr(document, "calculateAll"):
                raise ValueError("LibreOffice could not open the spreadsheet")
            document.calculateAll()
            errors = []
            for name, cells in request["formulas"].items():
                sheet = document.getSheets().getByName(name)
                for address in cells:
                    cell = sheet.getCellRangeByName(address)
                    if cell.getError():
                        errors.append(f"{name}!{address}: {cell.getString()} (error {cell.getError()})")
                        if len(errors) >= 20:
                            break
            if errors:
                raise ValueError("Formula calculation errors; original retained: " + "; ".join(errors))
            document.storeAsURL(Path("calculated.xlsx").resolve().as_uri(), (
                _property(uno, "FilterName", "Calc MS Excel 2007 XML"), _property(uno, "Overwrite", True),
            ))
            return {"engine": "LibreOffice Calc", "formula_count": sum(map(len, request["formulas"].values()))}
        finally:
            if document is not None:
                document.close(True)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    request = json.loads(Path("request.json").read_text(encoding="utf-8"))
    try:
        result = ocr(request) if request["operation"] == "ocr" else recalculate(request)
    except Exception as exc:
        Path("result.json").write_text(json.dumps({"error": str(exc)[:4000]}), encoding="utf-8")
        return 2
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
