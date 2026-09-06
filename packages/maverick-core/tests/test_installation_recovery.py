from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from maverick import backup, config, control_plane_lease
from maverick import installation_recovery as maintenance


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.delenv("MAVERICK_ALLOW_MULTIPLE_CONTROL_PLANES", raising=False)
    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(maintenance, "blockers", list)
    monkeypatch.setattr(maintenance.workspace_recovery, "version", lambda: "1.0.0")
    installed = tmp_path / "installed"
    installed.mkdir()
    runtime = installed / "maverick.exe"
    runtime.write_bytes(b"old-runtime")
    (installed / "maverick-desktop.exe").write_bytes(b"native-ui-fixture")
    active = [runtime]
    monkeypatch.setattr(maintenance, "_runtime", lambda: active[0])
    monkeypatch.setattr(maintenance, "_native_channel", lambda runtime: {"native_sha256": digest(b"native-ui-fixture"), "public_key": "fixture-only"})
    (home / "config.toml").write_text("# local configuration\n")
    for tenant in ("alpha", "beta"):
        root = home / "tenants" / tenant
        root.mkdir(parents=True)
        (root / "ledger.json").write_text("old-state")
        (root / "backups").mkdir()
        (root / "backups" / "operator.key").write_text("separate-custody")
    control_plane_lease.release()
    yield home, runtime, active
    control_plane_lease.release()


def staged(installation, monkeypatch, *, prepare=True):
    home, runtime, active = installation
    grant = maintenance.authorize(actor="global-admin", isolated=True, confirm_all_workspaces=True)
    directory = maintenance._directory(grant["id"])
    metadata = {"public_key": "fixture-only", "old": {"version": "1.0.0", "runtime_sha256": digest(b"old-runtime")},
                "new": {"version": "1.0.1", "runtime_sha256": digest(b"new-runtime")}}
    (directory / "downloads.json").write_text(json.dumps(metadata))
    for name in ("old", "new"):
        (directory / (name + ".msi")).write_bytes(name.encode())
    # Signature primitives are exercised separately with real Ed25519. This
    # transaction test substitutes only the external Windows trust/MSI engine.
    monkeypatch.setattr(maintenance, "_installer", lambda directory, metadata, label: {
        **metadata[label], "sha256": backup._sha256(directory / (label + ".msi")), "product_code": label,
        "publisher_thumbprint": "c" * 40})
    if prepare:
        maintenance.prepare(grant["authorization"])
        active[0] = directory / "maverick.exe"
    return grant, directory


def fake_installer(runtime, events):
    def install(directory, action, label):
        events.append((action, label))
        if action == "/i":
            runtime.write_bytes((label + "-runtime").encode())
        else:
            runtime.unlink(missing_ok=True)
    return install


def test_completed_upgrade_rollback_reinstalls_exact_old_binary_before_old_state(installation, monkeypatch):
    home, runtime, active = installation
    grant, directory = staged(installation, monkeypatch)
    events = []
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, events))
    assert maintenance.execute(grant["authorization"])["status"] == "upgraded"
    active[0] = runtime
    (home / "tenants" / "alpha" / "ledger.json").write_text("new-v2-state")
    (home / "tenants" / "beta" / "after-upgrade.json").write_text("new file")
    rollback = maintenance.authorize(actor="global-admin", isolated=True, confirm_all_workspaces=True, rollback_id=grant["id"])
    maintenance.prepare(rollback["authorization"])
    with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
        maintenance.assert_normal_start_allowed()
    active[0] = directory / "maverick.exe"
    original_restore = backup.restore_backup
    def checked_restore(*args, **kwargs):
        assert runtime.read_bytes() == b"old-runtime"
        events.append("restore")
        return original_restore(*args, **kwargs)
    monkeypatch.setattr(backup, "restore_backup", checked_restore)
    assert maintenance.execute(rollback["authorization"])["status"] == "rolled_back"
    assert events == [("/i", "new"), ("/x", "new"), ("/i", "old"), "restore"]
    assert (home / "tenants" / "alpha" / "ledger.json").read_text() == "old-state"
    assert not (home / "tenants" / "beta" / "after-upgrade.json").exists()
    assert (home / "tenants" / "beta" / "backups" / "operator.key").read_text() == "separate-custody"
    record = maintenance._read(directory / "record.json")
    assert (directory / record["safety"]["name"]).is_file()
    maintenance.assert_normal_start_allowed()


def test_failed_old_install_does_not_restore_state_or_allow_normal_start_and_can_resume(installation, monkeypatch):
    home, runtime, active = installation
    grant, directory = staged(installation, monkeypatch)
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, []))
    maintenance.execute(grant["authorization"])
    active[0] = runtime
    (home / "tenants" / "alpha" / "ledger.json").write_text("new-v2-state")
    rollback = maintenance.authorize(actor="global-admin", isolated=True, confirm_all_workspaces=True, rollback_id=grant["id"])
    maintenance.prepare(rollback["authorization"])
    active[0] = directory / "maverick.exe"
    def fail(*_):
        raise maintenance.MaintenanceError("synthetic MSI failure")
    monkeypatch.setattr(maintenance, "_msiexec", fail)
    with pytest.raises(maintenance.MaintenanceError, match="synthetic"):
        maintenance.execute(rollback["authorization"])
    assert (home / "tenants" / "alpha" / "ledger.json").read_text() == "new-v2-state"
    with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
        maintenance.assert_normal_start_allowed()
    token = maintenance.resume(grant["id"])
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, []))
    assert maintenance.execute(token)["status"] == "rolled_back"


def test_only_one_concurrent_execution_consumes_review(installation, monkeypatch):
    _, runtime, _ = installation
    grant, _ = staged(installation, monkeypatch)
    calls = []
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, calls))
    def attempt():
        try:
            return maintenance.execute(grant["authorization"])["status"]
        except maintenance.MaintenanceError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["refused", "upgraded"]
    assert calls == [("/i", "new")]


def test_live_control_plane_and_missing_explicit_all_workspace_review_are_refused(installation):
    with pytest.raises(maintenance.MaintenanceError, match="global administrator"):
        maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=False)
    grant = maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True)
    control_plane_lease.acquire(role="workspace-recovery")
    with pytest.raises(maintenance.MaintenanceError, match="Pause"):
        maintenance.prepare(grant["authorization"])


def test_changed_source_and_expired_grant_refuse_before_installers(installation, monkeypatch):
    _, runtime, _ = installation
    grant = maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True)
    runtime.write_bytes(b"changed")
    with pytest.raises(maintenance.MaintenanceError, match="source application changed"):
        maintenance.prepare(grant["authorization"])
    monkeypatch.setattr(maintenance.time, "time", lambda: grant["expires_at"] + 1)
    with pytest.raises(maintenance.MaintenanceError, match="expired"):
        maintenance.inspect_grant(grant["authorization"])


def test_whole_home_archive_excludes_live_lease_and_nested_recovery_keys(installation):
    home, _, _ = installation
    with maintenance._locked(), maintenance._quiesced():
        directory = maintenance._store()
        maintenance._snapshot(directory, "scope-test", digest(b"old-runtime"))
        with maintenance._scope():
            manifest = backup.verify_backup(directory / "scope-test.tgz")
    names = set(manifest["files"])
    assert "config.toml" in names
    assert {"tenants/alpha/ledger.json", "tenants/beta/ledger.json"} <= names
    assert not any("backups/" in name or "control-plane" in name for name in names)
    assert (home / "control-plane-holder.json").is_file()


def test_two_grants_cannot_replace_an_active_operation_or_clear_its_startup_block(installation, monkeypatch):
    # Both approvals happen before either operation pauses the application.
    earlier = maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True)
    winner, _ = staged(installation, monkeypatch)
    with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
        maintenance.prepare(earlier["authorization"])
    with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
        maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True)
    assert maintenance._read(maintenance._store() / "active.json")["id"] == winner["id"]


def test_execution_cannot_clear_another_operation_marker(installation, monkeypatch):
    grant, _ = staged(installation, monkeypatch)
    marker = {"schema": 1, "target": maintenance._target(), "id": "b" * 32}
    maintenance._write(maintenance._store() / "active.json", marker)
    with pytest.raises(maintenance.MaintenanceError, match="Another reviewed operation"):
        maintenance.execute(grant["authorization"])
    assert maintenance._read(maintenance._store() / "active.json")["id"] == "b" * 32


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_crash_after_completion_receipt_requires_verified_finalization(installation, monkeypatch, rollback, changed):
    home, runtime, active = installation
    grant, directory = staged(installation, monkeypatch)
    events = []
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, events))
    if rollback:
        maintenance.execute(grant["authorization"])
        active[0] = runtime
        grant = maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True, rollback_id=grant["id"])
        maintenance.prepare(grant["authorization"])
        active[0] = directory / "maverick.exe"
    outcome = "rolled_back" if rollback else "upgraded"
    original = maintenance._write
    def crash(path, record):
        original(path, record)
        if record.get("status") == outcome:
            raise SystemExit("synthetic process exit after durable receipt")
    monkeypatch.setattr(maintenance, "_write", crash)
    with pytest.raises(SystemExit):
        maintenance.execute(grant["authorization"])
    monkeypatch.setattr(maintenance, "_write", original)
    count = len(events)
    if changed:
        (home / "tenants" / "beta" / "ledger.json").write_text("unexpected state")
    authorization = maintenance.resume(grant["id"])
    if changed:
        with pytest.raises(backup.BackupError, match="post-restore"):
            maintenance.execute(authorization)
        with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
            maintenance.assert_normal_start_allowed()
    else:
        assert maintenance.execute(authorization)["status"] == outcome
        maintenance.assert_normal_start_allowed()
    assert len(events) == count, "finalization must not execute the installers again"


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("drift", [False, True])
def test_terminal_finalization_with_real_committed_wal_database(installation, monkeypatch, rollback, drift):
    home, runtime, active = installation
    db = home / "tenants" / "alpha" / "reviews.sqlite3"
    script = "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA wal_autocheckpoint=0'); c.execute('CREATE TABLE reviews (value TEXT)'); c.execute(\"INSERT INTO reviews VALUES ('captured')\"); c.commit(); os._exit(0)"
    subprocess.run([sys.executable, "-c", script, str(db)], check=True)
    assert Path(str(db) + "-wal").stat().st_size > 0
    grant, directory = staged(installation, monkeypatch)
    events = []
    monkeypatch.setattr(maintenance, "_msiexec", fake_installer(runtime, events))
    if rollback:
        maintenance.execute(grant["authorization"])
        active[0] = runtime
        grant = maintenance.authorize(actor="admin", isolated=True, confirm_all_workspaces=True, rollback_id=grant["id"])
        maintenance.prepare(grant["authorization"])
        active[0] = directory / "maverick.exe"
    outcome = "rolled_back" if rollback else "upgraded"
    original = maintenance._write
    def crash(path, record):
        original(path, record)
        if record.get("status") == outcome:
            raise SystemExit("synthetic terminal receipt crash with committed WAL")
    monkeypatch.setattr(maintenance, "_write", crash)
    with pytest.raises(SystemExit):
        maintenance.execute(grant["authorization"])
    monkeypatch.setattr(maintenance, "_write", original)
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT value FROM reviews").fetchall() == [("captured",)]
    if drift:
        connection.execute("INSERT INTO reviews VALUES ('changed after snapshot')")
        connection.commit()
    connection.close()
    count = len(events)
    authorization = maintenance.resume(grant["id"])
    if drift:
        with pytest.raises((maintenance.MaintenanceError, backup.BackupError)):
            maintenance.execute(authorization)
        assert (maintenance._store() / "active.json").exists()
    else:
        assert maintenance.execute(authorization)["status"] == outcome
        maintenance.assert_normal_start_allowed()
    assert len(events) == count


@pytest.mark.parametrize("changed", ["key", "native", "publisher"])
def test_staging_cannot_replace_the_installed_release_trust_identity(installation, monkeypatch, changed):
    _, runtime, _ = installation
    grant, directory = staged(installation, monkeypatch, prepare=False)
    metadata_path = directory / "downloads.json"
    metadata = json.loads(metadata_path.read_text())
    if changed == "key":
        metadata["public_key"] = "different-publisher-key"
        metadata_path.write_text(json.dumps(metadata))
    elif changed == "native":
        (runtime.parent / "maverick-desktop.exe").write_bytes(b"different-native-channel")
    else:
        original = maintenance._installer
        monkeypatch.setattr(maintenance, "_installer", lambda directory, metadata, label:
                            original(directory, metadata, label) | {"publisher_thumbprint": label})
    with pytest.raises(maintenance.MaintenanceError, match="key|native application changed|publisher changed"):
        maintenance.prepare(grant["authorization"])
    assert not (maintenance._store() / "active.json").exists()


def test_native_logs_are_bounded_and_never_accept_a_client_path(installation, monkeypatch):
    grant, directory = staged(installation, monkeypatch)
    (directory / "i-new.log").write_bytes(b"\xff\xfe" + ("fixture " * 5000 + "finished").encode("utf-16-le"))
    result = maintenance.logs(grant["id"])
    assert result["details"].endswith("finished")
    assert len(result["details"]) < 17000
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.logs("../config.toml")
