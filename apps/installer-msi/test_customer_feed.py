from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("customer_feed", Path(__file__).with_name("customer_feed.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_signature():
    # Shape-only fixture. Cryptographic acceptance uses real ephemeral keys,
    # the updater's own verifier, and tampered installer bytes in Windows CI.
    text = "untrusted comment: test\n" + base64.b64encode(bytes(74)).decode()
    text += "\ntrusted comment: test\n" + base64.b64encode(bytes(64)).decode() + "\n"
    return base64.b64encode(text.encode()).decode()


def inventory():
    return {"schema": "lightwork.windows-customer-build.v1", "version": "0.1.7",
            "acceptance_fixture": False, "complete_release_cohort": True, "runtime": {"sha256": "a" * 64}}


def test_feed_uses_exact_signature_version_and_immutable_artifact_address():
    signature = fixture_signature()
    result = module.feed(inventory(), signature, "https://updates.example.test/{version}/Lightwork.msi")
    assert result["version"] == "0.1.7"
    assert result["platforms"] == {"windows-x86_64": {
        "signature": signature, "url": "https://updates.example.test/0.1.7/Lightwork.msi"}}


@pytest.mark.parametrize("url", [
    "https://updates.example.test/latest.msi", "http://updates.example.test/{version}/x",
    "https://user@updates.example.test/{version}/x", "https://updates.example.test/{version}/x#f",
    "https://updates.example.test/{version}/{other}", "https://updates.example.test/{version}/ x",
])
def test_feed_rejects_mutable_or_unsafe_installer_address(url):
    with pytest.raises(ValueError):
        module.feed(inventory(), fixture_signature(), url)


def test_acceptance_fixture_never_becomes_an_update_offer():
    metadata = dict(inventory(), acceptance_fixture=True)
    with pytest.raises(ValueError, match="complete customer"):
        module.feed(metadata, fixture_signature(), "https://updates.example.test/{version}/Lightwork.msi")


def test_missing_or_malformed_signature_never_becomes_an_update_offer():
    with pytest.raises(ValueError, match="verified Tauri"):
        module.feed(inventory(), "malformed", "https://updates.example.test/{version}/Lightwork.msi")


def test_repeated_same_release_is_idempotent_and_conflict_preserves_latest(tmp_path):
    source = inventory()
    (tmp_path / "runtime-inventory.json").write_text(json.dumps(source))
    (tmp_path / "Lightwork.msi.sig").write_text(fixture_signature())
    (tmp_path / "Lightwork.msi").write_bytes(b"test-only package")
    url = "https://updates.example.test/{version}/Lightwork.msi"
    module.write_feed(tmp_path, url)
    latest = (tmp_path / "latest.json").read_bytes()
    immutable = (tmp_path / "versions" / "0.1.7.json").read_bytes()
    module.write_feed(tmp_path, url)
    assert (tmp_path / "latest.json").read_bytes() == latest
    assert (tmp_path / "versions" / "0.1.7.json").read_bytes() == immutable
    source["runtime"]["sha256"] = "b" * 64
    (tmp_path / "runtime-inventory.json").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="immutable"):
        module.write_feed(tmp_path, url)
    assert (tmp_path / "latest.json").read_bytes() == latest
    assert (tmp_path / "versions" / "0.1.7.json").read_bytes() == immutable
