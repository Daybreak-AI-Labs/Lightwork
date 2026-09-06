"""Native acceptance control tests: no installer, executable, trust or host mutation."""
from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return (importlib.import_module("native_webview_journey"),
            importlib.import_module("native_webview_driver"))


@pytest.mark.parametrize("mode", ["rejected", "accepted", "missing-claim", "timeout", "malformed"])
def test_first_owner_keeps_acceptance_and_records_only_bounded_diagnostics(tmp_path, modules, mode):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.directory = journey.state = tmp_path
    journey.password = "synthetic-private-form-value"  # pragma: allowlist secret - noncredential redaction fixture
    journey.report = {"checks": []}
    if mode == "accepted":
        (tmp_path / "dashboard-owner.json").write_text("synthetic claim", encoding="utf-8")
    screenshots, observations = [], []

    class View:
        session = "/session/synthetic"

        def call(self, method, path, payload=None):
            if method == "GET":
                return "http://127.0.0.1:8765/start"
            assert path.endswith("/execute/sync") and payload["args"] == []
            observations.append(True)
            if mode == "malformed":
                return {"private": journey.password}
            return {"ready": mode != "timeout", "path": "/auth/local-owner" if mode == "rejected" else "/start",
                    "form_rejected": mode == "rejected", "rejection": "origin_rejected" if mode == "rejected" else "none"}

        def wait(self, condition, **_kwargs):
            value = condition()
            if value:
                return value
            raise RuntimeError("synthetic private driver error " + journey.password)

        def navigate(self, _address):
            pass

        def click(self, _selector):
            pass

        def fill(self, _selector, _value):
            pass

        def keyboard(self, _selector, _value):
            pass

        def screenshot(self, path):
            screenshots.append(path)

    journey.view = View()
    if mode == "accepted":
        journey.first_owner()
        assert journey.report["checks"] == ["first-launch-and-named-owner"] and len(screenshots) == 1
    else:
        with pytest.raises(RuntimeError) as error:
            journey.first_owner()
        assert journey.password not in str(error.value)
        assert screenshots == [] and journey.report["checks"] == []
    diagnostic = journey.report["owner_setup"]
    assert set(diagnostic) == {"ready", "path", "form_rejected", "rejection", "claim_exists"}
    assert diagnostic["claim_exists"] == (mode == "accepted")
    assert journey.password not in json.dumps(journey.report)
    if mode == "rejected":
        assert diagnostic["rejection"] == "origin_rejected" and len(observations) == 1
    if mode == "malformed":
        assert diagnostic["path"] == "unavailable"


def test_owner_diagnostic_executes_real_js_without_exporting_private_page_data(tmp_path, modules):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed only for the actual browser-script contract check")
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.state = tmp_path
    scripts = []

    class View:
        session = "/session/synthetic"

        def call(self, _method, _path, payload):
            scripts.append(payload["script"])
            return None

    journey.view = View()
    journey.owner_diagnostic()
    source = """
const assert = require('node:assert/strict');
const inspect = new Function(SCRIPT);
global.location = {pathname:'/auth/local-owner', search:'?private-query', hash:'#private-fragment'};
global.document = {readyState:'complete', get cookie(){throw Error('cookie access forbidden')},
  querySelector(selector){assert.equal(selector,'[role="alert"]');return {textContent:message}}};
let message = 'Open this Lightwork page before submitting the form.';
assert.equal(inspect().rejection,'origin_rejected');
message = 'private input should never leave the page';
assert.equal(inspect().rejection,'unclassified_form_rejection');
assert.equal(JSON.stringify(inspect()).includes('private'),false);
location.pathname = '/private-path';
assert.deepEqual(inspect(),{ready:true,path:'other',form_rejected:false,rejection:'none'});
""".replace("SCRIPT", json.dumps(scripts[0]))
    result = subprocess.run([node, "-e", source], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr


def test_startup_probe_restores_runtime_and_preserves_primary_failure(tmp_path, modules):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.install = tmp_path
    journey.runtime = tmp_path / "maverick.exe"
    journey.runtime.write_bytes(b"synthetic runtime; never executed")
    journey.report = {"cleanup_errors": []}
    journey.record = lambda *_args, **_kwargs: None
    stops = []

    def stop():
        stops.append(True)
        if len(stops) > 1:
            raise RuntimeError("synthetic stop failure")

    def start():
        raise ValueError("original startup failure")

    journey.stop, journey.start = stop, start
    with pytest.raises(ValueError, match="original startup failure"):
        journey.startup_failure()
    assert journey.runtime.read_bytes() == b"synthetic runtime; never executed"
    assert not (tmp_path / "maverick.exe.acceptance-retained").exists()
    assert journey.report["cleanup_errors"] == ["startup-probe-stop"]


def test_driver_cleanup_timeout_keeps_original_failure_and_writes_report(tmp_path, modules):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.directory, journey.base, journey.candidate = tmp_path, Path("base"), Path("next")
    journey.thumbprint, journey.password = "", "synthetic private passphrase"
    journey.report = {"passed": False, "checks": [], "cleanup_errors": []}
    journey.stop = lambda: None
    calls = []

    def msi(package, action):
        calls.append((package, action))
        if action == "/i":
            raise ValueError("original installer failure")

    class Driver:
        def kill(self):
            pass

        def wait(self, *, timeout):
            raise subprocess.TimeoutExpired("synthetic driver", timeout)

    journey.msi, journey.driver, journey.driver_log = msi, Driver(), None
    with pytest.raises(ValueError, match="original installer failure"):
        journey.run()
    assert calls == [(Path("base"), "/i"), (Path("next"), "/x"), (Path("base"), "/x")]
    assert journey.report["failed_phase"] == "install-signed-base"
    assert journey.report["cleanup_errors"] == ["webview-driver"]
    assert journey.password == ""
    assert (tmp_path / "acceptance.json").is_file()


def test_click_reacquires_element_after_page_replaces_disabled_control(modules):
    _, module = modules
    view = module.WebView(1)
    view.session = "/session/synthetic"
    finds, clicks = [], []

    def call(method, path, payload=None):
        if path.endswith("/execute/sync"):
            return True
        if path.endswith("/element"):
            finds.append(True)
            return {module.ELEMENT: "old" if len(finds) == 1 else "current"}
        if path.endswith("/enabled"):
            return "/current/" in path
        if path.endswith("/click"):
            clicks.append(path)

    def wait(condition, **_kwargs):
        for _ in range(3):
            if result := condition():
                return result
        raise AssertionError("The control was never reacquired after replacement")

    view.call, view.wait = call, wait
    view.click("#continue")
    assert clicks == ["/session/synthetic/element/current/click"]


def test_host_guard_precedes_any_journey_directory_or_process_creation(tmp_path, modules, monkeypatch):
    module, _ = modules
    def deny():
        raise RuntimeError("hosted runner required")
    monkeypatch.setattr(module, "hosted_only", deny)
    with pytest.raises(RuntimeError, match="hosted runner required"):
        module.Journey(tmp_path, tmp_path, tmp_path / "base", tmp_path / "next")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("selected,runtime,output,accepted", [
    ("140.0.3485.50", "140.0.3485.50", "MSEdgeDriver 140.0.3485.50", True),
    ("140.0.3485.50", "140.0.3485.52", "MSEdgeDriver 140.0.3485.50", True),
    ("140.0.3486.50", "140.0.3485.52", "MSEdgeDriver 140.0.3486.50", False),
    ("140.0.3485.50", "140.0.3485.52", "MSEdgeDriver 140.0.3485.51", False),
    ("140.0.3485.50", "140.0.3485.52", "140.0.3485.50 140.0.3485.52", False),
    ("140.0.3485", "140.0.3485.52", "MSEdgeDriver 140.0.3485.50", False),
])
def test_observed_driver_is_exact_download_and_same_runtime_build(tmp_path, modules, selected, runtime, output, accepted):
    module, _ = modules
    (tmp_path / "driver-version.txt").write_text(selected, encoding="utf-8")
    if accepted:
        assert module.verified_driver_version(tmp_path, runtime, output) == selected
    else:
        with pytest.raises(RuntimeError, match="downloaded version and runtime build"):
            module.verified_driver_version(tmp_path, runtime, output)


@pytest.mark.parametrize("leak,changed", [(False, False), (True, False), (False, True)])
def test_invalid_grant_journey_requires_cleared_input_and_unchanged_bytes(tmp_path, modules, monkeypatch, leak, changed):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.directory = tmp_path
    journey.binary, journey.runtime = tmp_path / "native.exe", tmp_path / "runtime.exe"
    journey.binary.write_bytes(b"synthetic native")
    journey.runtime.write_bytes(b"synthetic runtime")
    journey.report = {"checks": []}
    monkeypatch.setattr(module, "app_windows", lambda _path: [(123, 456)])
    actions = []

    class View:
        def fill(self, selector, value):
            actions.append(("fill", selector))
            assert re.fullmatch(r"0{32}:0{64}", value)

        def click(self, selector):
            actions.append(("click", selector))
            if changed:
                journey.runtime.write_bytes(b"unexpected changed runtime")

        def wait(self, condition):
            assert condition()

        def text(self, selector):
            assert selector == "#maintenance-status"
            return "Lightwork could not complete that step."

        def property(self, selector, name):
            return ("private fixture input" if leak else "") if name == "value" else True

        def screenshot(self, path):
            actions.append(("screenshot", path.name))

    journey.view = View()
    if leak or changed:
        with pytest.raises(RuntimeError, match="preserve the installation and clear private input"):
            journey.reject_invalid_authorization()
        assert not any(action[0] == "screenshot" for action in actions)
    else:
        journey.reject_invalid_authorization()
        assert actions == [("fill", "#maintenance-authorization"), ("click", "#install-update"),
                           ("screenshot", "invalid-maintenance-authorization.png")]
        assert journey.report["checks"] == ["invalid-maintenance-authorization-refused"]


def test_journey_id_selectors_exist_in_actual_bundled_and_dashboard_pages(modules):
    module, _ = modules
    root = Path(__file__).parents[2]
    ids = set()

    class Page(HTMLParser):
        def handle_starttag(self, _tag, attrs):
            for key, value in attrs:
                if key == "id":
                    ids.add(value)

    parser = Page()
    for path in (root / "apps/desktop/ui/index.html", root / "apps/desktop/ui/maintenance.html",
                 root / "apps/desktop/ui/recovery.html",
                 root / "packages/maverick-dashboard/maverick_dashboard/templates/support.html",
                 root / "packages/maverick-dashboard/maverick_dashboard/templates/workspace_recovery.html"):
        parser.feed(path.read_text(encoding="utf-8"))
    selectors = set(re.findall(r'["\']#([a-z][a-z0-9-]*)', Path(module.__file__).read_text(encoding="utf-8")))
    assert selectors and selectors <= ids, f"Missing real controls: {selectors - ids}"


def test_failed_final_report_does_not_replace_original_error(tmp_path, modules, monkeypatch):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.directory, journey.base, journey.candidate = tmp_path, Path("base"), Path("next")
    journey.thumbprint, journey.password = "", "synthetic passphrase"
    journey.driver = journey.driver_log = None
    journey.report = {"passed": False, "checks": [], "cleanup_errors": []}
    journey.stop = lambda: None
    def msi(_package, action):
        if action == "/i":
            raise ValueError("original failure")
    def record(phase):
        journey.report["phase"] = phase
        if phase == "failed":
            raise OSError("synthetic read-only report destination")
    journey.msi, journey.record = msi, record
    with pytest.raises(ValueError, match="original failure"):
        journey.run()
    assert journey.password == ""
    assert journey.report["cleanup_errors"] == ["acceptance-report"]
    assert "synthetic passphrase" not in json.dumps(journey.report)


@pytest.mark.parametrize("quit_fails,close_fails", [(False, False), (True, False), (True, True)])
def test_stop_quits_owned_native_window_before_closing_driver(modules, monkeypatch, quit_fails, close_fails):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.binary = Path("synthetic-owned-app.exe")
    journey.report = {"cleanup_errors": []}
    calls = []
    running = True

    def windows(binary):
        assert binary == journey.binary
        calls.append("window-present" if running else "window-exited")
        return [(123, 456)] if running else []

    def quit_menu(binary, label):
        nonlocal running
        assert binary == journey.binary and label == "Quit Lightwork"
        calls.append("native-quit")
        if quit_fails:
            raise ValueError("original Quit failure")
        running = False

    class View:
        def close(self):
            calls.append("driver-close")
            if close_fails:
                raise RuntimeError("secondary driver failure")

    journey.view = View()
    monkeypatch.setattr(module, "app_windows", windows)
    monkeypatch.setattr(module, "menu", quit_menu)
    if quit_fails:
        with pytest.raises(ValueError, match="original Quit failure"):
            journey.stop()
    else:
        journey.stop()
        assert "window-exited" in calls
        assert calls.index("window-exited") < calls.index("driver-close")
    assert calls.index("native-quit") < calls.index("driver-close")
    assert calls[-1] == "driver-close"
    assert journey.report["cleanup_errors"] == (["webview-session"] if close_fails else [])


@pytest.mark.parametrize("failure", [None, "missing-package", "not-installed", "wrong-version", "extra-package",
                                     "cohort-mismatch", "incomplete-build", "manifest-duplicate", "private-control",
                                     "private-grant", "private-password", "download-changed", "unconfirmed-download"])
def test_normal_support_journey_reviews_exact_cohort_and_download(tmp_path, modules, failure):
    module, _ = modules
    journey = module.Journey.__new__(module.Journey)
    journey.workspace = Path(__file__).parents[2]
    journey.directory, journey.downloads = tmp_path, tmp_path / "downloads"
    journey.downloads.mkdir()
    journey.password = "synthetic-private-passphrase"  # pragma: allowlist secret - noncredential redaction fixture
    journey.report = {"checks": []}
    cohort = module.tomllib.loads((journey.workspace / "release-cohort.toml").read_text(encoding="utf-8"))
    journey.metadata = {"complete_release_cohort": failure != "incomplete-build", "cohort_version": cohort["version"], "packages": cohort["packages"]}
    report = {"versions": {row["distribution"]: cohort["version"] for row in cohort["packages"]}, "config_redacted": {}}
    if failure == "missing-package":
        del report["versions"]["maverick-knowledge"]
    elif failure == "not-installed":
        report["versions"]["maverick-knowledge"] = "not installed"
    elif failure == "wrong-version":
        report["versions"]["maverick-installer"] = "99.0.0"
    elif failure == "extra-package":
        report["versions"]["unexpected"] = cohort["version"]
    elif failure == "cohort-mismatch":
        journey.metadata["cohort_version"] = "99.0.0"
    elif failure == "manifest-duplicate":
        journey.metadata["packages"] = [cohort["packages"][0]] * 8
    elif failure == "private-grant":
        report["config_redacted"]["fixture"] = "a" * 32 + ":" + "b" * 64
    elif failure == "private-password":
        report["config_redacted"]["fixture"] = journey.password
    actions = []

    class View:
        def navigate(self, address):
            actions.append(("navigate", address))

        def click(self, selector):
            actions.append(("click", selector))
            if selector == "#support-download":
                value = dict(report)
                if failure == "download-changed":
                    value["unexpected"] = "unreviewed"
                (journey.downloads / ("Lightwork-support-" + "a" * 32 + ".json")).write_text(json.dumps(value), encoding="utf-8")

        def wait(self, condition, **_kwargs):
            result = condition()
            assert result
            return result

        def property(self, selector, name):
            if selector == "#support-result":
                assert name == "hidden"
                return False
            assert selector == "#support-download" and name == "disabled"
            return failure != "unconfirmed-download"

        def text(self, selector):
            return json.dumps(report) if selector == "#support-preview" else "The reviewed report was downloaded."

        def call(self, method, path, payload):
            assert method == "POST" and path.endswith("/elements")
            assert payload["using"] == "css selector"
            return [{"synthetic-private-control": True}] if failure == "private-control" else []

        def screenshot(self, path):
            actions.append(("screenshot", path.name))

        session = "/session/synthetic"

    journey.view = View()
    if failure:
        with pytest.raises(RuntimeError):
            journey.normal_support()
        assert not any(action[0] == "screenshot" for action in actions)
        assert "normal-support-eight-package-inventory" not in journey.report["checks"]
    else:
        journey.normal_support()
        assert actions == [("navigate", "http://127.0.0.1:8765/help"), ("click", "#support-prepare"),
                           ("click", "#support-result details summary"), ("click", "#support-confirm"),
                           ("click", "#support-download"), ("screenshot", "normal-support.png")]
        assert journey.report["support_inventory"]["package_count"] == 8
        assert journey.report["support_inventory"]["versions"] == report["versions"]
        assert journey.report["checks"] == ["normal-support-eight-package-inventory"]
