"""Failure uploads retain useful fixture evidence without collecting private state."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = Path(__file__).with_name("collect_acceptance_diagnostics.py")
spec = importlib.util.spec_from_file_location("acceptance_diagnostics", MODULE)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def test_collect_keeps_failed_msi_and_recovery_evidence_but_excludes_keys_and_state(tmp_path):
    runner = tmp_path / "runner"
    artifacts = tmp_path / "artifacts"
    output = tmp_path / "output"
    recovery = runner / "lw-rollback-fixture"
    recovery.mkdir(parents=True)
    artifacts.mkdir()
    (runner / "lightwork-msi-fixture.log").write_text("Error 1324: invalid folder\nAction ended: InstallFiles. Return value 3.\n", encoding="utf-16")
    (recovery / "recovery-error.log").write_text("RuntimeError: fixture startup failed\nAuthorization: Bearer confidential fixture\ntoken=secret fixture value\n" + "a" * 64)
    (recovery / "state.json").write_text("PRIVATE WORKSPACE CONTENT")
    (runner / "acceptance-updater.key").write_text("PRIVATE SIGNING KEY")
    (artifacts / "acceptance.json").write_text('{"install":"passed","repair":"failed"}')
    (artifacts / "Lightwork.msi").write_bytes(b"PRIVATE BINARY")
    publisher = runner / "rollback-signed-fixture"
    publisher.mkdir()
    (publisher / "trust.log").write_text("Trust operation started but did not complete")
    (publisher / "stages.json").write_text('{"phase":"trust","acceptance_passed":false}')
    (publisher / "fixture.cer").write_bytes(b"PRIVATE CERTIFICATE")
    (publisher / "publisher-thumbprint.txt").write_text("PRIVATE IDENTITY")
    report = diagnostics.collect(runner, artifacts, output)
    assert len(report["files"]) == 5
    combined = "\n".join(path.read_text() for path in output.iterdir())
    assert "Error 1324" in combined and "fixture startup failed" in combined
    assert '"repair":"failed"' in combined
    assert "Trust operation started but did not complete" in combined and '"phase":"trust"' in combined
    for excluded in ("PRIVATE", "confidential fixture", "secret fixture value", "a" * 64):
        assert excluded not in combined


def test_collect_bounds_log_tails_and_scrubs_embedded_credentials(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    runner.mkdir()
    (runner / "lightwork-msi-long.log").write_text("old output\n" * 100 + "recent failure")
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 64)
    output = tmp_path / "output"
    report = diagnostics.collect(runner, tmp_path / "missing", output)
    assert report["files"][0]["truncated"] is True
    assert (output / report["files"][0]["file"]).read_text().endswith("recent failure")
    assert diagnostics.scrub("https://example.test/file?signature=private&key=private") == "https://example.test/file?[REDACTED]"
    assert "private" not in diagnostics.scrub("-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----")  # pragma: allowlist secret - synthetic redaction fixture


def test_native_captures_are_exact_allowlisted_and_bounded(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    native = runner / "native-webview-fixture"
    native.mkdir(parents=True)
    header = b"\x89PNG\r\n\x1a\n"
    (native / "first-owner.png").write_bytes(header + b"fixture capture")
    (native / "normal-support.png").write_bytes(header + b"reviewed bundle")
    (native / "signed-native-upgrade.png").write_bytes(header + b"x" * 100)
    (native / "signed-native-rollback.png").write_bytes(b"not a PNG")
    (native / "private-grant.png").write_bytes(header + b"PRIVATE")
    (native / "tls.key").write_bytes(b"PRIVATE")
    monkeypatch.setattr(diagnostics, "MAX_IMAGE_BYTES", 32)
    output = tmp_path / "output"
    report = diagnostics.collect(runner, tmp_path / "missing", output)
    assert len(report["images"]) == 2
    assert report["images"][0]["source"] == "native-webview-fixture/first-owner.png"
    assert (output / report["images"][0]["file"]).read_bytes() == header + b"fixture capture"
    assert report["images"][1]["source"] == "native-webview-fixture/normal-support.png"
    assert report["maximum_images"] == 5
    assert sorted(path.suffix for path in output.iterdir()) == [".json", ".png", ".png"]


def test_transition_reports_survive_log_volume_without_collecting_signing_inputs(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    transition = runner / "identity-transition-fixture"
    transition.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "publisher-transition-acceptance.json").write_text('{"phase":"complete"}')
    (transition / "stages.json").write_text('{"phase":"transition-rollback"}')
    (transition / "transition-rollback.log").write_text("Retained real transition failure")
    (transition / "bridge.json").write_text("PRIVATE POLICY")
    (transition / "bridge-policy.json.sig").write_text("PRIVATE SIGNATURE")
    (transition / "fixture.key").write_text("PRIVATE KEY")
    for index in range(10):
        (runner / f"lightwork-msi-{index:02d}.log").write_text("old MSI output")
    monkeypatch.setattr(diagnostics, "MAX_FILES", 3)
    output = tmp_path / "output"
    report = diagnostics.collect(runner, artifacts, output)
    assert len(report["files"]) == 3
    assert [row["source"] for row in report["files"]] == [
        "publisher-transition-acceptance.json", "identity-transition-fixture/stages.json",
        "identity-transition-fixture/transition-rollback.log",
    ]
    combined = "\n".join(path.read_text() for path in output.iterdir())
    assert "Retained real transition failure" in combined
    assert "PRIVATE" not in combined
