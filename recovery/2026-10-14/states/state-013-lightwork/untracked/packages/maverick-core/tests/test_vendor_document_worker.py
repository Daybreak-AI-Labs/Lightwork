"""Real bounded child parsing of authored evidence, without provider access."""
import hashlib
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick import vendor_document_worker as worker
from maverick import vendor_onboarding_documents as documents
from maverick.paths import tenant_scope
from maverick.vendor_office_text import DOCX_MIME, XLSX_MIME
from maverick.vendor_onboarding_adapters import EvidenceDocument

FIXTURES = Path(__file__).resolve().parents[3] / "scripts/acceptance/fixtures/vendor-documents"


@pytest.mark.parametrize(("extension", "mime", "method"), [
    ("docx", DOCX_MIME, "word_body_text"),
    ("xlsx", XLSX_MIME, "excel_visible_cell_text"),
    ("pdf", "application/pdf", "pdf_page_content_streams"),
])
def test_vendor_untrusted_parsers_never_run_in_dashboard_process(monkeypatch, extension, mime, method):
    def forbidden(*args, **kwargs):
        pytest.fail("untrusted document was parsed in the dashboard process")

    monkeypatch.setattr(documents, "extract_office", forbidden, raising=False)
    monkeypatch.setattr(documents, "_document_text", forbidden, raising=False)
    content = (FIXTURES / f"northstar-policy.{extension}").read_bytes()
    with tenant_scope(tenant="worker-evidence"):
        result = documents.extract(EvidenceDocument("file", "1", 100, content, mime),
                                   source="upload", name="Authored evidence", now=200, freshness_days=30)
    assert result["status"] == "available"
    assert result["extraction"]["method"] == method
    assert result["extracted"]["answers"]["aiv_provenance"]["citations"]


@pytest.mark.parametrize(("extension", "mime"), [
    ("docx", DOCX_MIME), ("xlsx", XLSX_MIME), ("pdf", "application/pdf"),
])
def test_worker_preserves_exact_existing_text_and_location_metadata(extension, mime):
    from maverick.privacy_ops import _pdf_extraction
    from maverick.vendor_office_text import extract_office

    content = (FIXTURES / f"northstar-policy.{extension}").read_bytes()
    expected = _pdf_extraction(content) if mime == "application/pdf" else extract_office(content, mime)
    assert worker.parse(content, mime) == expected


def test_existing_parser_refusal_remains_useful_and_no_source_echo():
    secret = b"synthetic confidential source should never enter an error"
    with pytest.raises(ValueError, match="damaged or uses unsupported compression") as caught:
        worker.parse(secret, DOCX_MIME)
    assert secret.decode() not in str(caught.value)


def test_oversized_and_unapproved_format_never_start_a_child(monkeypatch):
    monkeypatch.setattr(worker, "_command", lambda *args: pytest.fail("child started"))
    with pytest.raises(ValueError, match="byte limit"):
        worker.parse(b"x" * (worker.MAX_OFFICE_BYTES + 1), DOCX_MIME)
    with pytest.raises(ValueError, match="Unsupported"):
        worker.parse(b"arbitrary code", "python")


def test_process_limit_refuses_instead_of_queueing_more_raw_documents(monkeypatch):
    import threading

    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(worker, "_SLOTS", slots)
    monkeypatch.setattr(worker, "_command", lambda *args: pytest.fail("child started"))
    with pytest.raises(ValueError, match="busy"):
        worker.parse(b"pdf", "application/pdf")


@pytest.mark.parametrize("failure", ["timeout", "stdout", "stderr", "memory", "crash"])
def test_real_child_failure_is_bounded_and_private_directory_is_cleaned(monkeypatch, tmp_path, failure):
    import tempfile

    from maverick import ai_evidence_local_parsers as local

    # Inject only a trusted test command. Production accepts no program/path.
    # Reading stdin ensures containment is applied before the stress operation.
    allocated = tmp_path / "memory-limit-was-not-enforced"
    scripts = {
        "timeout": "import time; time.sleep(30)",
        "stdout": "sys.stdout.buffer.write(b'x' * (20 * 1024 * 1024)); sys.stdout.flush()",
        "stderr": "sys.stderr.buffer.write(b'x' * 10000); sys.stderr.flush()",
        "memory": f"a=bytearray(1024 * 1024 * 1024); open({str(allocated)!r},'w').write('unexpected allocation')",
        "crash": "os._exit(17)",
    }
    probe = "import os,sys; sys.stdin.buffer.read(); " + scripts[failure]
    monkeypatch.setattr(worker, "_command", lambda mime: [sys.executable, "-I", "-c", probe])
    monkeypatch.setattr(worker, "TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    outcomes, bounded = [], local._bounded_process

    def observe(*args, **kwargs):
        try:
            result = bounded(*args, **kwargs)
            outcomes.append(result.returncode)
            return result
        except Exception as exc:
            outcomes.append(type(exc))
            raise

    monkeypatch.setattr(local, "_bounded_process", observe)
    before = time.monotonic()
    with pytest.raises(ValueError, match="could not finish safely"):
        worker.parse(b"%PDF-authored-invalid", "application/pdf")
    assert time.monotonic() - before < 8
    assert list(tmp_path.iterdir()) == []
    if failure == "timeout":
        assert outcomes == [local._ProcessTimeExceeded]
    elif failure in {"stdout", "stderr"}:
        assert outcomes == [local._OutputBoundExceeded]
    elif failure == "memory":
        assert len(outcomes) == 1 and isinstance(outcomes[0], int) and outcomes[0] != 0
    else:
        assert outcomes == [17]
    # A failed child also releases its process slot.
    assert worker._SLOTS.acquire(blocking=False)
    worker._SLOTS.release()


def test_real_child_cannot_inherit_workspace_imports_secrets_or_home(monkeypatch, tmp_path):
    from maverick.ai_evidence_local_parsers import _bounded_process

    marker = tmp_path / "IMPORTED"
    (tmp_path / "maverick").mkdir()
    (tmp_path / "maverick" / "__init__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-worker-environment-sentinel")
    monkeypatch.setenv("LIGHTWORK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    assert worker.parse(b"%PDF-invalid", "application/pdf")[0] == ""
    assert not marker.exists()
    # Inspect the very same helper's actual environment, not a mocked Popen.
    temporary = tmp_path / "neutral"
    temporary.mkdir()
    result = _bounded_process([sys.executable, "-I", "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
        timeout_seconds=5, max_stdout_bytes=8192, max_memory_bytes=worker.MAX_MEMORY_BYTES, cwd=str(temporary))
    environment = json.loads(result.stdout)
    assert "OPENAI_API_KEY" not in environment and "PYTHONPATH" not in environment
    assert "LIGHTWORK_HOME" not in environment and "MAVERICK_CONFIG" not in environment
    assert environment["HOME"] == environment["TEMP"] == str(temporary)


@pytest.mark.parametrize("mutation", ["source", "mime", "metadata", "error", "nonjson"])
def test_bad_child_result_is_refused_without_fallback_or_private_error(monkeypatch, mutation):
    from maverick import ai_evidence_local_parsers as local

    content = b"%PDF-invalid"
    payload = {"protocol": worker.PROTOCOL, "mime": "application/pdf",
               "source_sha256": hashlib.sha256(content).hexdigest(), "text": "",
               "metadata": {"method": "pdf_page_content_streams", "scope": "page_referenced", "confidence": "untrusted", "review_required": True}}
    if mutation == "source":
        payload["source_sha256"] = "0" * 64
    elif mutation == "mime":
        payload["mime"] = DOCX_MIME
    elif mutation == "metadata":
        payload["metadata"]["review_required"] = False
    elif mutation == "error":
        payload.pop("metadata")
        payload.pop("text")
        payload["error"] = ["synthetic-private-error"]
    encoded = b"synthetic-private-error" if mutation == "nonjson" else json.dumps(payload).encode()
    monkeypatch.setattr(local, "_bounded_process", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=encoded, stderr=b"synthetic-private-error"))
    with pytest.raises(ValueError, match="could not finish safely") as caught:
        worker.parse(content, "application/pdf")
    assert "synthetic-private-error" not in str(caught.value)


def test_fixed_frozen_dispatch_never_imports_cli_and_rejects_bad_arguments(monkeypatch):
    entry = Path(__file__).resolve().parents[3] / "build/pyinstaller_entry.py"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker._command(DOCX_MIME) == [sys.executable, worker.WORKER_FLAG, DOCX_MIME]
    monkeypatch.setitem(sys.modules, "maverick.cli", None)
    calls = []
    monkeypatch.setattr(worker, "main", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["maverick.exe", worker.WORKER_FLAG, DOCX_MIME])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(entry), run_name="__main__")
    assert caught.value.code == 0 and calls == [[DOCX_MIME]]


@pytest.mark.parametrize("args", [[], ["python"], [DOCX_MIME, "extra"]])
def test_actual_source_worker_refuses_non_protocol_invocations(args):
    result = subprocess.run([sys.executable, "-I", "-m", "maverick.vendor_document_worker", *args],
                            input=b"untrusted", capture_output=True, timeout=10)
    assert result.returncode == 2 and result.stdout == b"" and result.stderr == b""
