"""Start / stop / restart Lightwork's services from Settings > Maintenance.

Real supervised child processes (a small stand-in program replaces the
``maverick`` command so the test does not start a real server), one instance
per kind, global-admin only (a workspace administrator is refused), confirmed
stops, activity-record rows, and cleanup on dashboard shutdown."""
from __future__ import annotations

import sys
import time

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
BASE = "/api/v1/admin/services"
_CHILD = ("import sys, time\n"
          "print('service ready', flush=True)\n"
          "print('token sk-ant-api03-' + 'x' * 40, flush=True)\n"
          "time.sleep(60)\n")


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import service_supervisor
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT", "MAVERICK_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    monkeypatch.setattr(service_supervisor, "command", lambda kind: [sys.executable, "-c", _CHILD])
    yield TestClient(dash_app.app, headers=ORIGIN)
    service_supervisor.stop_all(timeout=5.0)
    config.reset_config_cache()
    audit_writer._defaults.clear()


def _as(monkeypatch, role: str, who: str = "user:someone"):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def _audit_verbs(monkeypatch):
    from maverick.audit import writer
    seen: list[dict] = []
    real = writer.audit_event

    def spy(kind, **payload):
        seen.append(payload)
        return real(kind, **payload)
    monkeypatch.setattr(writer, "audit_event", spy)
    import maverick.audit as audit_pkg
    monkeypatch.setattr(audit_pkg, "audit_event", spy)
    return seen


def _row(client, kind):
    return next(r for r in client.get(BASE).json()["services"] if r["kind"] == kind)


def _eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_the_real_command_is_a_fixed_argument_list():
    from maverick_dashboard import service_supervisor
    assert service_supervisor.command("worker")[-1] == "worker"
    assert service_supervisor.command("channels")[-1] == "serve"
    tools = service_supervisor.command("tools")
    assert tools[0] == sys.executable and tools[1:3] == ["-m", "maverick.cli"]
    assert tools[3:] == ["mcp", "--http", "--host", "127.0.0.1", "--port", "8771"]


@pytest.mark.parametrize("who", ["operator", "workspace admin"])
def test_only_an_installation_administrator_controls_services(client, monkeypatch, who):
    from maverick_dashboard import auth
    _as(monkeypatch, "operator" if who == "operator" else "viewer")
    if who == "workspace admin":
        monkeypatch.setattr(auth, "role_for_principal", lambda principal: "admin")
    assert client.get(BASE).status_code == 403
    for action in ("start", "stop", "restart"):
        assert client.post(f"{BASE}/{action}", json={"service": "worker", "confirm": True}).status_code == 403
    assert _row_raw_not_running()


def _row_raw_not_running():
    from maverick_dashboard import service_supervisor
    return not any(r["running"] for r in service_supervisor.status())


def test_start_stop_restart_one_instance_with_confirmation_and_records(client, monkeypatch):
    seen = _audit_verbs(monkeypatch)
    assert TestClient(client.app).post(f"{BASE}/start", json={"service": "worker"}).status_code == 403
    assert client.post(f"{BASE}/start", json={"service": "nope"}).status_code == 422
    started = client.post(f"{BASE}/start", json={"service": "worker"})
    assert started.status_code == 200 and started.json()["service"]["running"] is True
    assert client.post(f"{BASE}/start", json={"service": "worker"}).status_code == 409  # one per kind
    assert _eventually(lambda: any("service ready" in line for line in _row(client, "worker")["log"]))
    log = _row(client, "worker")["log"]
    assert not any("sk-ant-api03-" + "x" * 40 in line for line in log)  # secrets masked
    assert client.post(f"{BASE}/stop", json={"service": "worker"}).status_code == 409  # needs confirming
    assert _row(client, "worker")["running"] is True
    restarted = client.post(f"{BASE}/restart", json={"service": "worker", "confirm": True})
    assert restarted.status_code == 200 and restarted.json()["service"]["running"] is True
    stopped = client.post(f"{BASE}/stop", json={"service": "worker", "confirm": True})
    assert stopped.status_code == 200 and stopped.json()["service"]["running"] is False
    assert _row(client, "worker")["last_error"] == ""  # a requested stop is not an error
    assert client.post(f"{BASE}/stop", json={"service": "worker", "confirm": True}).status_code == 409
    verbs = [p.get("verb") for p in seen]
    assert verbs.count("service_start") == 2 and "service_restart" in verbs and "service_stop" in verbs


def test_an_unexpected_exit_is_reported_plainly(client, monkeypatch):
    from maverick_dashboard import service_supervisor
    monkeypatch.setattr(service_supervisor, "command",
                        lambda kind: [sys.executable, "-c", "import sys; print('bad setting'); sys.exit(3)"])
    client.post(f"{BASE}/start", json={"service": "channels"})
    assert _eventually(lambda: not _row(client, "channels")["running"] and _row(client, "channels")["last_error"])
    error = _row(client, "channels")["last_error"]
    assert error.startswith("Stopped unexpectedly (exit code 3).") and "bad setting" in error


def test_dashboard_shutdown_stops_every_owned_service(client):
    from maverick_dashboard import service_supervisor
    client.post(f"{BASE}/start", json={"service": "tools"})
    process = service_supervisor._SERVICES["tools"].process
    service_supervisor.stop_all(timeout=0, wait=False)  # the shutdown signal never blocks
    service_supervisor.stop_all(timeout=5.0)
    assert _eventually(lambda: process.proc.poll() is not None)
    assert _eventually(lambda: not _row(client, "tools")["running"])


def test_shutdown_supervisor_knows_about_the_services():
    from maverick_dashboard import _lifecycle

    class Recorder:
        def __init__(self):
            self.names = []

        def register(self, name, stop, alive):
            self.names.append(name)

        def register_final_resource(self, name, stop, alive):
            self.names.append(name)
    recorder = Recorder()

    class App:
        class state:
            pass
    _lifecycle.register_main_components(App, recorder)
    assert "services" in recorder.names
