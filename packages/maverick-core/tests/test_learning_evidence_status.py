"""Customer-facing status must not invent a completed evidence write."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from maverick import audit, dreaming, earned_autonomy, hindsight
from maverick.audit import AuditWriteRefused
from maverick.cli import _learning_groups


@pytest.mark.parametrize("failure", [False, OSError("unavailable"), AuditWriteRefused("refused")])
def test_hindsight_incomplete_audit_is_not_success(tmp_path, monkeypatch, failure):
    def record(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(audit, "record", record)
    path = tmp_path / "summary.ndjson"
    report = hindsight.HindsightReport(n_goals=1, covered_now=1)
    assert hindsight.write_ledger(report, before_label="snapshot", path=path) is False
    # The already-committed summary is retained. Failure is not a rollback.
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["n_goals"] == 1


@pytest.mark.parametrize("written", [False, True])
def test_hindsight_cli_reports_actual_write_result(tmp_path, monkeypatch, written):
    import maverick.cli as cli

    monkeypatch.setattr(dreaming, "list_snapshots", lambda: ["snapshot"])
    monkeypatch.setattr(dreaming, "snapshots_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "open_world", lambda path: object())
    monkeypatch.setattr(hindsight, "replay", lambda *args, **kwargs: hindsight.HindsightReport())
    monkeypatch.setattr(hindsight, "write_ledger", lambda *args, **kwargs: written)
    result = CliRunner().invoke(_learning_groups.hindsight, ["--ledger"], obj={"db": "unused"})
    assert (result.exit_code == 0) is written, result.output
    assert "signed hindsight ledger" not in result.output
    if not written:
        assert "incomplete" in result.output.lower()
        assert "before retrying" in result.output.lower()


def test_default_revoker_requires_durable_withdrawal(monkeypatch):
    from maverick.safety import consent

    calls = []
    monkeypatch.setattr(consent, "revoke", lambda action, **kwargs: calls.append((action, kwargs)))
    earned_autonomy._default_revoke_fn("shell")
    assert calls == [("shell", {"strict": True, "write_tombstone": True})]


def test_failed_grant_withdrawal_cannot_report_success(tmp_path):
    calls = []
    audits = []

    def fail_revoke(action):
        calls.append(action)
        raise OSError("consent store unavailable")

    ledger = earned_autonomy.TrustLedger(path=tmp_path / "trust.ndjson")
    assert ledger.record_event("graduate", "shell", ts=1.0)
    engine = earned_autonomy.EarnedAutonomyEngine(
        cards=earned_autonomy.CardStore(path=tmp_path / "cards.ndjson"),
        ledger=ledger, revoke_fn=fail_revoke,
        audit_fn=lambda kind, **kwargs: audits.append((kind, kwargs)),
    )
    assert engine.revoke("shell") is False
    assert calls == ["shell"]
    # Still demote the local authority; do not restore it because withdrawal failed.
    assert ledger.state("shell").graduated is False
    assert audits[-1][1]["withdrawal_confirmed"] is False
    assert audits[-1][1]["local_revocation_recorded"] is True
    assert audits[-1][1]["authority_scope"] == "current_identity_and_policy"


def test_revoke_ledger_failure_does_not_undo_real_withdrawal(tmp_path, monkeypatch):
    from maverick.safety import consent

    consent.grant_persistent("shell")
    audits = []
    engine = earned_autonomy.EarnedAutonomyEngine(
        cards=earned_autonomy.CardStore(path=tmp_path / "cards.ndjson"),
        ledger=earned_autonomy.TrustLedger(path=tmp_path / "trust.ndjson"),
        audit_fn=lambda kind, **kwargs: audits.append((kind, kwargs)),
    )
    monkeypatch.setattr(engine.ledger, "record_event", lambda *args, **kwargs: False)
    assert engine.revoke("shell") is False
    assert ("shell", "") not in consent.list_grants()
    assert audits[-1][1]["withdrawal_confirmed"] is True
    assert audits[-1][1]["local_revocation_recorded"] is False


def test_revoke_cli_does_not_claim_unconfirmed_withdrawal(monkeypatch):
    engine = SimpleNamespace(revoke=lambda action: False, status=list)
    monkeypatch.setattr(earned_autonomy, "shared", lambda: engine)
    result = CliRunner().invoke(_learning_groups.earned_autonomy, ["--revoke", "shell"])
    assert result.exit_code != 0
    assert "not confirmed" in result.output.lower()
    assert "Revoked earned" not in result.output
    assert "grant withdrawn" not in result.output


@pytest.mark.parametrize("storage_fails", [False, True])
def test_default_revoker_reports_real_consent_storage_result(tmp_path, monkeypatch, storage_fails):
    from maverick.safety import consent

    consent.grant_persistent("shell")
    assert ("shell", "") in consent.list_grants()
    if storage_fails:
        def fail_append(*args, **kwargs):
            raise OSError("revocation append unavailable")

        monkeypatch.setattr(consent, "_append_record_unlocked", fail_append)
    engine = earned_autonomy.EarnedAutonomyEngine(
        cards=earned_autonomy.CardStore(path=tmp_path / "cards.ndjson"),
        ledger=earned_autonomy.TrustLedger(path=tmp_path / "trust.ndjson"),
        audit_fn=lambda *args, **kwargs: None,
    )
    assert engine.revoke("shell") is not storage_fails
    assert (("shell", "") in consent.list_grants()) is storage_fails
    if not storage_fails:
        # An already absent grant is still a successful idempotent withdrawal.
        assert engine.revoke("shell") is True


def test_revoke_cli_discloses_principal_scope_with_other_live_grant(tmp_path, monkeypatch):
    from maverick.connections import bind_principal
    from maverick.safety import consent

    engine = earned_autonomy.EarnedAutonomyEngine(
        cards=earned_autonomy.CardStore(path=tmp_path / "cards.ndjson"),
        ledger=earned_autonomy.TrustLedger(path=tmp_path / "trust.ndjson"),
        audit_fn=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(earned_autonomy, "shared", lambda: engine)
    with bind_principal("user:alice"):
        consent.grant_persistent("shell")
    with bind_principal("local"):
        result = CliRunner().invoke(_learning_groups.earned_autonomy, ["--revoke", "shell"])
        assert result.exit_code == 0, result.output
        assert "current identity and policy" in result.output
        assert "Other identities' standing grants are not withdrawn" in result.output
    with bind_principal("user:alice"):
        assert ("shell", "") in consent.list_grants()
