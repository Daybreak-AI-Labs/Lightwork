"""Offline native document work in a separate, private sandbox workspace."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from importlib import resources
from pathlib import Path

from ..config import get_document_tools
from ..file_lock import ensure_private_directory
from ..paths import data_dir
from ..sandbox.docker import DockerBackend
from ..sandbox.kubernetes import KubernetesBackend
from ..sandbox.podman import PodmanBackend
from ..sandbox.workspace_transfer import _read


def verify_release_document_runtime() -> None:
    """Check real parser imports and worker source in wheel/frozen artifacts.

    This does not start an engine or claim a configured Docker/Kubernetes host.
    """
    for name, attribute in (("openpyxl", "load_workbook"), ("lxml.etree", "fromstring"),
                            ("pypdf", "PdfReader"), ("pdfplumber", "open")):
        if not callable(getattr(importlib.import_module(name), attribute, None)):
            raise RuntimeError(f"Document parser {name}.{attribute} is unavailable")
    source = resources.files("maverick.sandbox").joinpath("document_engine.py").read_bytes()
    if not source:
        raise RuntimeError("Document worker source is missing from this release")
    compile(source, "document_engine.py", "exec")


@contextmanager
def _worker(sandbox, request):
    cfg = get_document_tools()
    if not isinstance(sandbox, (DockerBackend, PodmanBackend, KubernetesBackend)):
        raise RuntimeError("Native document processing needs a Docker, Podman or Kubernetes sandbox; see docs/document-processing.md")
    parent = ensure_private_directory(data_dir("document-processing"))
    with tempfile.TemporaryDirectory(prefix="job-", dir=parent) as directory:
        stage = Path(directory)
        if stage.resolve().parent != parent.resolve():
            raise RuntimeError("Document staging directory is outside private storage")
        changes = {"workdir": stage, "allow_network": False, "image": cfg["engine_image"], "timeout": cfg["timeout"]}
        if isinstance(sandbox, DockerBackend):
            changes.update(reuse_container=False, read_only_paths=())
        worker = replace(sandbox, **changes)
        (stage / "engine.py").write_bytes(resources.files("maverick.sandbox").joinpath("document_engine.py").read_bytes())
        (stage / "request.json").write_text(json.dumps(request), encoding="utf-8")
        yield stage, worker


def _execute(stage, worker):
    result = worker.exec("/usr/bin/python3 engine.py", timeout=get_document_tools()["timeout"])
    try:
        raw = _read(stage, "result.json")
    except FileNotFoundError as exc:
        raise RuntimeError("Document engine produced no result; build/configure its image and check the sandbox. "
                           + result.stderr[-400:]) from exc
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("Document engine result exceeds its output limit")
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise RuntimeError("Document engine result is malformed")
    if result.exit_code or response.get("error"):
        raise RuntimeError(str(response.get("error") or "Document engine failed; original retained"))
    return response


def ocr_pdf(data: bytes, pages: str, sandbox) -> str:
    cfg = get_document_tools()
    if not cfg["pdf_ocr"]:
        raise RuntimeError("Scanned PDF OCR is disabled; enable documents.pdf_ocr and configure the document engine image")
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("OCR input exceeds the 64 MiB limit; split the PDF")
    request = {"operation": "ocr", "pages": pages, "language": cfg["ocr_language"], "max_pages": cfg["ocr_max_pages"]}
    with _worker(sandbox, request) as (stage, worker):
        (stage / "input.pdf").write_bytes(data)
        response = _execute(stage, worker)
        if not isinstance(response.get("text"), str) or not response["text"].strip():
            raise RuntimeError("OCR returned no readable text")
        return response["text"]


def recalculate(path: Path, sandbox) -> str:
    from .office_package import WorkbookPackage
    from .spreadsheet import _atomic_destination, _openpyxl

    if not get_document_tools()["native_recalculation"]:
        raise RuntimeError("Native recalculation is disabled; enable documents.native_recalculation and configure the document engine image")
    with path.open("rb") as stream:
        original_bytes = stream.read(64 * 1024 * 1024 + 1)
    package = WorkbookPackage(original_bytes)
    package.check_recalculable()
    formulas = package.formulas()
    if not any(formulas.values()):
        return "No formulas to recalculate"
    original = hashlib.sha256(original_bytes).digest()
    with _worker(sandbox, {"operation": "recalculate", "formulas": formulas}) as (stage, worker):
        # The engine receives an ordinary XLSX copy without VBA; the original
        # ZIP and all VBA/formatting parts stay in the trusted harness.
        workbook = _openpyxl().load_workbook(io.BytesIO(original_bytes), keep_vba=False, keep_links=False)
        try:
            workbook.save(stage / "input.xlsx")
        finally:
            workbook.close()
        _execute(stage, worker)
        count = package.import_caches(WorkbookPackage(_read(stage, "calculated.xlsx")))
        if hashlib.sha256(path.read_bytes()).digest() != original:
            raise RuntimeError("Workbook changed during recalculation; newer file retained")
        with _atomic_destination(path) as temporary:
            package.write(temporary)
    return f"Recalculated {count} formula cell(s) with LibreOffice Calc; original formulas, formatting and VBA preserved"
