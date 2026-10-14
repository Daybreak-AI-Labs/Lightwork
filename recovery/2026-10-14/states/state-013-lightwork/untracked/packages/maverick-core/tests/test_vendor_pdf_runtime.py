from __future__ import annotations

import hashlib
import json
import os

import pytest
from maverick import vendor_pdf_runtime as module


@pytest.fixture
def installed(tmp_path, monkeypatch):
    executable = "bin/tesseract.exe" if os.name == "nt" else "bin/tesseract"
    contents = {executable: b"synthetic native bytes", "tessdata/eng.traineddata": b"synthetic model",
                "licenses/THIRD_PARTY_NOTICES.txt": b"synthetic test notices"}
    model_hash = hashlib.sha256(contents["tessdata/eng.traineddata"]).hexdigest()
    monkeypatch.setattr(module, "MODEL_SHA256", model_hash)
    entries = []
    for name, data in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"schema": module.SCHEMA, "tesseract_version": module.TESSERACT_VERSION,
                "language": "eng", "model_sha256": model_hash, "files": entries,
                "executable": executable, "tessdata": "tessdata"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest


def test_installation_uses_only_verified_manifest_resources(installed, monkeypatch):
    root, manifest = installed
    monkeypatch.setenv("PATH", "unrelated-customer-programs")
    result = module.runtime(root)
    assert result["executable"] == root / manifest["executable"]
    assert result["tessdata"] == root / "tessdata"
    assert result["manifest"] == manifest


@pytest.mark.parametrize("relative", ["bin/tesseract.exe" if os.name == "nt" else "bin/tesseract", "tessdata/eng.traineddata", "licenses/THIRD_PARTY_NOTICES.txt"])
def test_changed_or_missing_runtime_resource_refuses_before_execution(installed, relative):
    root, _ = installed
    path = root / relative
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="unavailable"):
        module.runtime(root)
    path.unlink()
    with pytest.raises(RuntimeError, match="unavailable"):
        module.runtime(root)


@pytest.mark.parametrize("change", ["outside", "duplicate", "unknown_version", "language", "wrong_model", "incomplete"])
def test_invalid_manifest_never_selects_a_fallback_binary(installed, change):
    root, manifest = installed
    if change == "outside":
        manifest["files"][0]["path"] = "../outside.exe"
    elif change == "duplicate":
        manifest["files"].append(manifest["files"][0])
    elif change == "unknown_version":
        manifest["tesseract_version"] = "unreviewed"
    elif change == "language":
        manifest["language"] = "other"
    elif change == "wrong_model":
        manifest["model_sha256"] = "0" * 64
    else:
        manifest["files"].pop()
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unavailable"):
        module.runtime(root)


def test_source_and_frozen_roots_are_automatic_and_separate(tmp_path, monkeypatch):
    monkeypatch.delattr(module.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(module.sys, "prefix", str(tmp_path / "environment"))
    assert module.directory() == tmp_path / "environment/share/lightwork/vendor-pdf-runtime"
    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path / "frozen"), raising=False)
    assert module.directory() == tmp_path / "frozen/vendor-pdf-runtime"
