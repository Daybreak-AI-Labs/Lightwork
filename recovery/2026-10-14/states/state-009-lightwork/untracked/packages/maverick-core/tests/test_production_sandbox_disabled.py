"""Production may reject arbitrary execution instead of weakening isolation."""

from __future__ import annotations


def test_disabled_backend_refuses_without_invoking_a_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_SANDBOX_BACKEND", "disabled")
    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda *a, **k: {"sandbox": {"backend": "local"}},
    )
    from maverick.sandbox import DisabledBackend, build_sandbox

    sandbox = build_sandbox(workdir=tmp_path)
    assert isinstance(sandbox, DisabledBackend)

    result = sandbox.exec("touch should-never-exist")

    assert result.exit_code == 126
    assert "disabled" in result.stderr
    assert not (tmp_path / "should-never-exist").exists()


def test_deployment_verifier_treats_disabled_as_no_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda *a, **k: {"sandbox": {"backend": "disabled"}},
    )
    from maverick.deployment import verify_deployment

    checks = {check.name: check for check in verify_deployment()}

    assert checks["Sandbox isolation"].passed is True
    assert "execution is disabled" in checks["Sandbox isolation"].detail
