"""Phase 6 — per-client export + right-to-erasure. Provably complete because one
deployment = one client = one data root; fail-closed so it never targets the
shared root."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from maverick import client


@pytest.fixture(autouse=True)
def _bound(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    monkeypatch.setenv("MAVERICK_BACKUP_SIGNING_KEY", bytes(range(32)).hex())
    monkeypatch.setenv("MAVERICK_AUDIT_SIGN", "1")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    client.reset_client_cache()
    yield
    client.reset_client_cache()


def _seed():
    import sqlite3

    from maverick.paths import data_dir
    root = data_dir()
    (root / "audit").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(root / "world.db"))  # a real DB (export uses backup API)
    con.execute("CREATE TABLE t (v)")
    con.commit()
    con.close()
    from maverick import audit
    assert audit.record("test_client_seed", agent="test") is True
    (root / "agent_trust.json").write_text("[]")
    return root


def test_erase_removes_everything():
    root = _seed()
    res = client.erase_client()
    assert res["client_id"] == "acme" and res["removed"] >= 3
    assert not (root / "world.db").exists()
    assert not (root / "agent_trust.json").exists()
    assert not (root / "audit").exists()
    receipt, events = _receipt(res["audit_event_id"])
    assert receipt == Path(res["receipt_path"])
    assert [event["kind"] for event in events] == [
        "client_erase_requested", "client_erase_ready", "client_erased",
    ]
    assert events[-1]["status"] == "completed"
    assert events[-1]["removed"] == res["removed"]
    assert events[0]["client_root"] == "tenants/acme"
    assert events[0]["actor"].startswith("local:")


def test_erase_keep_audit():
    root = _seed()
    res = client.erase_client(keep_audit=True, actor="user:offboarding-admin")
    assert not (root / "world.db").exists()
    from maverick import audit
    events = list(audit.iter_events(audit_dir=root / "audit", all_days=True))
    assert [event["kind"] for event in events] == [
        "test_client_seed", "client_erase_requested", "client_erased",
    ]
    assert events[-1]["actor"] == "user:offboarding-admin"
    assert events[-1]["event_id"] == res["audit_event_id"]
    assert all(audit.verify_chain(path) == [] for path in audit.day_files(root / "audit"))
    _receipt(res["audit_event_id"])


def _receipt(receipt_id):
    """Verify using only the surviving artifact, after tenant keys are gone."""
    from maverick.audit import verify_chain
    from maverick.paths import maverick_home
    directory = maverick_home() / "client-erasure" / receipt_id
    path = directory / "events.ndjson"
    public_key = (directory / "verification-key.hex").read_text().strip()
    assert verify_chain(path, public_key) == []
    return path, [json.loads(line) for line in path.read_text().splitlines()]


def test_first_use_signing_leaves_no_client_key_material(monkeypatch):
    import hashlib

    from maverick import client_erasure
    from maverick.paths import data_dir
    root = data_dir()
    root.mkdir(parents=True)
    (root / "customer.txt").write_text("Client content")
    expected = {}
    original = client_erasure.ErasureReceipt.prepare

    def check_inventory(receipt, targets):
        relative = sorted(p.relative_to(root).as_posix() for p in client._erase_targets(root, keep_audit=False))
        expected.update(deletion_target_count=len(relative),
                        deletion_manifest_sha256=hashlib.sha256(json.dumps(relative).encode()).hexdigest())
        assert targets == expected
        original(receipt, targets)

    monkeypatch.setattr(client_erasure.ErasureReceipt, "prepare", check_inventory)
    res = client.erase_client()
    assert list(root.iterdir()) == []
    _, events = _receipt(res["audit_event_id"])
    assert events[-1]["status"] == "completed"
    assert events[0]["preflight_target_count"] == 1
    assert events[1]["deletion_target_count"] > events[0]["preflight_target_count"]
    for event in events[1:]:
        assert {key: event[key] for key in expected} == expected


@pytest.mark.parametrize("keep_audit", [False, True])
def test_missing_signer_refuses_before_deletion(monkeypatch, keep_audit):
    from maverick import client_erasure
    from maverick.audit import AuditRefused
    root = _seed()

    def refuse(_path):
        raise AuditRefused("required signing identity unavailable")

    monkeypatch.setattr(client_erasure, "AuditSigner", refuse)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased"):
        client.erase_client(keep_audit=keep_audit)
    assert (root / "world.db").exists()
    assert (root / "agent_trust.json").exists()


def test_unconfirmed_request_refuses_before_deletion(monkeypatch):
    from maverick import client_erasure
    root = _seed()
    monkeypatch.setattr(client_erasure.AuditSigner, "write", lambda _self, _event: False)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased"):
        client.erase_client()
    assert (root / "world.db").exists()


def test_unconfirmed_retained_request_refuses_before_deletion(monkeypatch):
    from maverick import audit
    root = _seed()
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: False)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased"):
        client.erase_client(keep_audit=True)
    assert (root / "world.db").exists()


def test_disabled_signed_retention_refuses_before_deletion(monkeypatch):
    from maverick import audit
    root = _seed()
    monkeypatch.setattr(audit.default_audit_log(), "_signing_enabled", False)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased") as caught:
        client.erase_client(keep_audit=True)
    assert (root / "world.db").exists()
    assert "signing is disabled" in str(caught.value.__cause__)


def test_unconfirmed_prepared_inventory_refuses_before_deletion(monkeypatch):
    from maverick import client_erasure
    root = _seed()
    original = client_erasure.AuditSigner.write

    def fail_prepare(signer, event, **kwargs):
        return False if event["kind"] == "client_erase_ready" else original(signer, event, **kwargs)

    monkeypatch.setattr(client_erasure.AuditSigner, "write", fail_prepare)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased") as caught:
        client.erase_client()
    assert (root / "world.db").exists()
    _, events = _receipt(caught.value.receipt_id)
    assert [event["status"] for event in events] == ["requested"]


def test_tampered_request_refuses_before_deletion(monkeypatch):
    from maverick import client_erasure
    from maverick.file_lock import atomic_read_text, atomic_write_text
    root = _seed()
    original = client_erasure.AuditSigner.write

    def corrupt_request(signer, event, **kwargs):
        result = original(signer, event, **kwargs)
        if event["kind"] == "client_erase_requested":
            row = json.loads(atomic_read_text(signer._path).splitlines()[0])
            row["actor"] = "forged operator"
            atomic_write_text(signer._path, json.dumps(row) + "\n")
        return result

    monkeypatch.setattr(client_erasure.AuditSigner, "write", corrupt_request)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased"):
        client.erase_client()
    assert (root / "world.db").exists()


def test_receipt_retains_only_minimal_inventory_metadata():
    root = _seed()
    (root / "Sensitive customer name").mkdir()
    (root / "Sensitive customer name" / "private.txt").write_text("private client content")
    res = client.erase_client()
    path, events = _receipt(res["audit_event_id"])
    assert "Sensitive customer name" not in path.read_text()
    assert "private client content" not in path.read_text()
    assert len(events[0]["preflight_inventory_sha256"]) == 64
    assert events[0]["preflight_target_areas"]["(other folders)"] == 1


def _symlink_or_skip(path, target):
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        import os
        import subprocess
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable on this host: {exc}")
        # A junction needs no administrator privilege on Windows and exercises
        # the same no-traversal requirement against real temporary directories.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                       check=True, capture_output=True)


@pytest.mark.parametrize("alias_area", ["client", "receipt"])
def test_redirected_folder_refuses_without_removing_outside_data(tmp_path, alias_area):
    from maverick.paths import data_dir
    outside = tmp_path / "outside-data"
    outside.mkdir()
    (outside / "must-stay.txt").write_text("outside data")
    if alias_area == "client":
        root = data_dir()
        root.parent.mkdir(exist_ok=True)
        _symlink_or_skip(root, outside)
    else:
        root = _seed()
        _symlink_or_skip(tmp_path / "client-erasure", outside)
    with pytest.raises(client.ClientErasureError, match="Nothing was erased"):
        client.erase_client()
    assert (outside / "must-stay.txt").read_text() == "outside data"
    if alias_area == "receipt":
        assert (root / "world.db").exists()


@pytest.mark.parametrize("replacement", ["junction", "ordinary_directory"])
def test_root_replaced_after_preparation_stops_without_deleting_replacement(monkeypatch, tmp_path, replacement):
    from maverick import client_erasure
    root = _seed()
    parked = tmp_path / "parked-original-client"
    outside = tmp_path / "outside-replacement"
    outside.mkdir()
    (outside / "world.db").write_text("outside data must survive")
    original = client_erasure.ErasureReceipt.prepare

    def replace_root(receipt, targets):
        original(receipt, targets)
        root.rename(parked)
        if replacement == "junction":
            _symlink_or_skip(root, outside)
        else:
            root.mkdir()
            (root / "world.db").write_text("replacement data must survive")

    monkeypatch.setattr(client_erasure.ErasureReceipt, "prepare", replace_root)
    with pytest.raises(client.ClientErasureError, match="Erasure is incomplete") as caught:
        client.erase_client()
    assert (parked / "world.db").exists()
    assert (outside / "world.db").read_text() == "outside data must survive"
    if replacement == "ordinary_directory":
        assert (root / "world.db").read_text() == "replacement data must survive"
    assert caught.value.removed == 0
    _, events = _receipt(caught.value.receipt_id)
    assert events[-1]["status"] == "incomplete"


def test_target_moved_out_of_client_root_after_preparation_is_not_claimed_erased(monkeypatch, tmp_path):
    from maverick import client_erasure
    root = _seed()
    parked = tmp_path / "still-existing-client-data.db"
    original = client_erasure.ErasureReceipt.prepare

    def move_prepared_target(receipt, targets):
        original(receipt, targets)
        (root / "world.db").rename(parked)

    monkeypatch.setattr(client_erasure.ErasureReceipt, "prepare", move_prepared_target)
    with pytest.raises(client.ClientErasureError, match="Erasure is incomplete") as caught:
        client.erase_client()
    assert parked.exists()
    _, events = _receipt(caught.value.receipt_id)
    assert events[-1]["status"] == "incomplete"


@pytest.mark.parametrize("keep_audit", [False, True])
def test_partial_deletion_records_incomplete_and_raises(monkeypatch, keep_audit):
    root = _seed()
    original = Path.unlink

    def deny_world(path, *args, **kwargs):
        if path == root / "world.db":
            raise PermissionError("file is still in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_world)
    with pytest.raises(client.ClientErasureError, match="Erasure is incomplete") as caught:
        client.erase_client(keep_audit=keep_audit)
    assert (root / "world.db").exists()
    assert not (root / "agent_trust.json").exists()
    assert caught.value.removed > 0
    _, events = _receipt(caught.value.receipt_id)
    assert events[-1]["kind"] == "client_erase_incomplete"
    assert events[-1]["status"] == "incomplete"
    assert events[-1]["failures"] > 0
    assert all(event["kind"] != "client_erased" for event in events)


def test_final_receipt_failure_never_claims_completed_erasure(monkeypatch):
    from maverick import client_erasure
    root = _seed()
    original = client_erasure.AuditSigner.write

    def fail_completion(signer, event, **kwargs):
        if event["kind"] == "client_erased":
            return False
        return original(signer, event, **kwargs)

    monkeypatch.setattr(client_erasure.AuditSigner, "write", fail_completion)
    with pytest.raises(client.ClientErasureError, match="final activity receipt could not be confirmed") as caught:
        client.erase_client()
    assert not (root / "world.db").exists()
    _, events = _receipt(caught.value.receipt_id)
    assert [event["status"] for event in events] == ["requested", "prepared"]


def test_bound_client_scope_is_restored_and_other_workspace_is_untouched():
    from maverick.paths import current_tenant, data_dir, tenant_scope
    root = _seed()
    with tenant_scope(tenant="other-workspace"):
        other = data_dir()
        other.mkdir(parents=True)
        (other / "customer.txt").write_text("other workspace")
        res = client.erase_client()
        assert current_tenant() == "other-workspace"
        assert Path(res["root"]) == root
        assert (other / "customer.txt").read_text() == "other workspace"
    assert not (root / "world.db").exists()
    _receipt(res["audit_event_id"])


def test_erase_refuses_without_binding(monkeypatch):
    monkeypatch.delenv("MAVERICK_CLIENT_ID", raising=False)
    monkeypatch.setattr(client, "_resolve", lambda: None)
    client.reset_client_cache()
    with pytest.raises(client.ClientBindingError):
        client.erase_client()


def test_erase_targets_client_subtree_only(monkeypatch, tmp_path):
    # A file directly under MAVERICK_HOME (the shared root) is NOT touched —
    # erase only wipes tenants/<client>/.
    _seed()
    shared = tmp_path / "shared.txt"
    shared.write_text("other")
    client.erase_client()
    assert shared.exists()


# ---- CLI ------------------------------------------------------------------


def _run(*args):
    from click.testing import CliRunner
    from maverick.cli import main
    return CliRunner().invoke(main, list(args))


def test_cli_erase_requires_confirm():
    _seed()
    r = _run("client", "erase")
    assert r.exit_code != 0 and "--confirm" in r.output
    from maverick.paths import data_dir
    assert (data_dir() / "world.db").exists()  # nothing erased without --confirm


def test_cli_status_and_erase_confirm():
    _seed()
    r = _run("client", "status")
    assert r.exit_code == 0 and "acme" in r.output
    r = _run("client", "erase", "--confirm")
    assert r.exit_code == 0 and "erased client" in r.output
    assert "Activity receipt:" in r.output and "events.ndjson" in r.output
    from maverick.paths import data_dir
    assert not (data_dir() / "world.db").exists()


def test_cli_reports_partial_erasure_as_failure(monkeypatch):
    root = _seed()
    original = Path.unlink

    def deny_world(path, *args, **kwargs):
        if path == root / "world.db":
            raise PermissionError("file is still in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_world)
    result = _run("client", "erase", "--confirm")
    assert result.exit_code != 0 and "Erasure is incomplete" in result.output
    assert "Activity receipt:" in result.output
    assert "erased client" not in result.output


def test_cli_export():
    _seed()
    r = _run("client", "export")
    assert r.exit_code == 0 and "client export written" in r.output
