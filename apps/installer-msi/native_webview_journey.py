"""Installed WebView2 first-owner, signed update, rollback and startup-help acceptance.

Runs only on the disposable hosted Windows acceptance VM. The driver sends real
WebView input and native menu commands; it never supplies a replacement IPC API.
The signed installers remain same-runtime fixtures, not real release acceptance.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from native_webview_driver import WebView, app_windows, menu
from native_webview_fixture import (
    available_port,
    command,
    digest,
    download_driver,
    hosted_only,
    hosts_mapping,
    make_tls,
    update_server,
    webview_runtime,
)


def verified_driver_version(directory: Path, runtime_version: str, output: str) -> str:
    selected = (directory / "driver-version.txt").read_text(encoding="utf-8").strip()
    observed = re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", output)
    if (not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", selected)
            or observed != [selected]
            or selected.split(".")[:3] != runtime_version.split(".")[:3]):
        raise RuntimeError("The signed WebView driver does not match its downloaded version and runtime build.")
    return selected


def support_inventory(bundle: dict, metadata: dict, cohort: dict) -> dict:
    source = [row["distribution"] for row in cohort["packages"]]
    frozen = [row["distribution"] for row in metadata["packages"]]
    version = cohort["version"]
    if (len(source) != 8 or len(set(source)) != 8 or len(frozen) != 8
            or set(source) != set(frozen) or metadata.get("complete_release_cohort") is not True
            or metadata.get("cohort_version") != version
            or bundle.get("versions") != dict.fromkeys(source, version)):
        raise RuntimeError("The frozen support inventory must contain all eight exact release-cohort versions.")
    return {"package_count": 8, "cohort_version": version, "versions": bundle["versions"]}


class Journey:
    def __init__(self, workspace: Path, runner: Path, base: Path, candidate: Path):
        hosted_only()
        if runner.resolve() != Path(os.environ["RUNNER_TEMP"]).resolve():
            raise RuntimeError("Native acceptance must use the current disposable runner.")
        self.workspace, self.base, self.candidate = workspace, base, candidate
        self.directory = runner / ("native-webview-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.install = self.directory / "app"
        self.state = self.directory / "state"
        self.state.mkdir()
        self.downloads = self.directory / "downloads"
        self.downloads.mkdir()
        self.binary = self.install / "maverick-desktop.exe"
        self.runtime = self.install / "maverick.exe"
        self.metadata = json.loads((workspace / "customer-artifacts/runtime-inventory.json").read_text(encoding="utf-8-sig"))
        self.debugger_port, self.driver_port = available_port(), available_port()
        self.view = WebView(self.driver_port)
        self.driver = None
        self.app = None
        self.driver_log = None
        self.thumbprint = ""
        self.report = {"schema": "lightwork.native-webview-acceptance.v1", "passed": False,
                       "synthetic_fixture_versions": True, "same_frozen_runtime_fixture": True,
                       "native_webview_automated": True, "ipc_mocked": False,
                       "publicly_recognized_publisher": False, "clean_customer_machine": False,
                       "checks": [], "cleanup_errors": []}
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("MAVERICK_", "LIGHTWORK_", "TAURI_SIGNING_"))
                    and key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
        system = os.environ["SystemRoot"]
        self.env.update(MAVERICK_HOME=str(self.state), MAVERICK_CLIENT_ID="native-acceptance",
                        MAVERICK_TENANT="native-acceptance", TAURI_WEBVIEW_AUTOMATION="true",
                        WEBVIEW2_USER_DATA_FOLDER=str(self.directory / "webview-profile"),
                        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={self.debugger_port}",
                        PATH=f"{system}\\System32;{system};{system}\\System32\\Wbem")
        (self.state / "config.toml").write_text("[client]\nid='native-acceptance'\n[features]\nworkspace_recovery=true\n", encoding="utf-8")
        self.password = secrets.token_urlsafe(32)

    def record(self, phase: str, *, passed=False):
        print("Installed native acceptance: " + phase, flush=True)
        self.report["phase"] = phase
        if passed:
            self.report["checks"].append(phase)
        (self.directory / "acceptance.json").write_text(json.dumps(self.report, indent=2), encoding="utf-8")

    def cleanup_step(self, name: str, operation):
        try:
            operation()
            return True
        except Exception:
            self.report["cleanup_errors"].append(name)
            return False

    def trust(self, phase: str):
        command([shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                 str(self.workspace / "apps/installer-msi/native-fixture-trust.ps1"), "-Phase", phase,
                 "-FixtureDirectory", str(self.directory), "-Thumbprint", self.thumbprint], timeout=60)

    def msi(self, package: Path, action: str):
        log = self.directory / ("msi-" + uuid.uuid4().hex + ".log")
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/msiexec.exe"),
            action, str(package.resolve(strict=True)), "/qn", "/norestart", "/L*v", str(log),
            "INSTALLDIR=" + str(self.install)], capture_output=True, timeout=240, check=False)
        allowed = {0, 3010} | ({1605, 1614} if action == "/x" else set())
        if result.returncode not in allowed:
            raise RuntimeError(f"Native fixture installer failed ({result.returncode}); MSI log retained.")

    def start(self):
        self.app = subprocess.Popen([str(self.binary)], env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.attach()

    def attach(self):
        self.view.session = ""
        capabilities = self.view.wait(lambda: self.view.attach(self.debugger_port), timeout=120)
        self.report["webview_version"] = capabilities.get("browserVersion")
        self.view.call("POST", self.view.session + "/ms/cdp/execute", {"cmd": "Browser.setDownloadBehavior",
            "params": {"behavior": "allow", "downloadPath": str(self.downloads)}})

    def menu(self, label: str):
        self.view.wait(lambda: app_windows(self.binary))
        menu(self.binary, label)

    def stop(self):
        primary = False
        try:
            windows = app_windows(self.binary)
            if windows:
                menu(self.binary, "Quit Lightwork")
                deadline = time.monotonic() + 20
                while app_windows(self.binary) and time.monotonic() < deadline:
                    time.sleep(.25)
                if app_windows(self.binary):
                    raise RuntimeError("The owned native app did not stop after Quit Lightwork.")
        except BaseException:
            primary = True
            raise
        finally:
            # Quitting the attached driver can alter its WebView. Dispatch and
            # observe the native app's own Quit action before ending the session.
            try:
                self.view.close()
            except Exception:
                if not primary:
                    raise
                self.report["cleanup_errors"].append("webview-session")

    def recovery(self):
        self.menu("Workspace recovery mode")
        self.view.click("#enter-recovery")
        self.view.wait(lambda: self.view.element("#wrec-installation-confirm"), timeout=120)
        self.view.wait(lambda: self.view.property("#wrec-refresh", "disabled") is False)

    def authorization(self, *, rollback=False) -> str:
        self.recovery()
        self.view.click("#wrec-installation-confirm")
        if rollback:
            # Select the real option with WebDriver key input, not JS mutation.
            self.view.keyboard("#wrec-installation-point", "\ue015")
            self.view.click("#wrec-installation-rollback")
        else:
            self.view.click("#wrec-installation-authorize")
        return self.view.wait(lambda: self.view.property("#wrec-installation-code", "value"))

    def assert_version(self, expected: str):
        self.view.wait(lambda: self.view.text("#installed-version") == expected, timeout=180)
        if digest(self.runtime) != self.metadata["runtime"]["sha256"]:
            raise RuntimeError("The installed runtime differs from the declared fixture bytes.")

    def first_owner(self):
        self.record("first-launch-and-named-owner")
        self.view.wait(lambda: self.view.call("GET", self.view.session + "/url").startswith("http://127.0.0.1:8765/"), timeout=120)
        self.view.navigate("http://127.0.0.1:8765/start")
        self.view.click('a[href="/auth/local-owner"]')
        self.view.fill('input[name="email"]', "native-owner@example.test")
        self.view.fill('input[name="password"]', self.password)
        self.view.click('input[name="confirm"]')
        # Enter activates the real submit button and verifies keyboard use.
        self.view.keyboard('button[type="submit"]', "\ue007")
        try:
            observed = self.view.wait(self.owner_progress)
        except (OSError, RuntimeError, KeyError):
            self.report["owner_setup"] = self.owner_diagnostic()
            self.record("first-launch-and-named-owner")
            raise RuntimeError("Native first-owner setup did not finish; bounded owner_setup diagnostics were retained.") from None
        self.report["owner_setup"] = observed
        if observed["form_rejected"]:
            self.record("first-launch-and-named-owner")
            raise RuntimeError("Native first-owner form was rejected (" + observed["rejection"] + "); bounded owner_setup diagnostics were retained.")
        if not observed["claim_exists"]:
            raise RuntimeError("Native first-owner setup did not create its protected claim.")
        self.view.screenshot(self.directory / "first-owner.png")
        self.record("first-launch-and-named-owner", passed=True)

    def owner_diagnostic(self):
        # Classify in the browser: raw alert text, URLs, inputs and cookies must
        # never leave the page or enter driver/CI artifacts for this diagnostic.
        script = """
const path = ['/start','/auth/local-owner','/auth/local-login'].includes(location.pathname)
  ? location.pathname : 'other';
const alert = path.startsWith('/auth/local-') ? document.querySelector('[role="alert"]') : null;
const text = alert ? (alert.textContent || '').trim().slice(0, 2048) : '';
const reasons = {
  'Open this Lightwork page before submitting the form.': 'origin_rejected',
  "Open owner setup on this computer's loopback address.": 'loopback_required',
  'Owner setup does not accept remote or forwarded requests.': 'forwarded_request_rejected',
  'An owner has already been claimed. Sign in to complete or continue setup.': 'already_claimed',
  'Local owner authority is unavailable.': 'authority_unavailable',
  'Authentication policy is unavailable.': 'policy_unavailable',
  'Check the submitted form values.': 'invalid_form',
  'The email or passphrase was not accepted.': 'credentials_rejected'
};
return {ready: document.readyState === 'complete', path, form_rejected: !!alert,
  rejection: alert ? (reasons[text] || 'unclassified_form_rejection') : 'none'};
"""
        fallback = {"ready": False, "path": "unavailable", "form_rejected": False, "rejection": "unavailable"}
        try:
            result = self.view.call("POST", self.view.session + "/execute/sync", {"script": script, "args": []})
            if (not isinstance(result, dict) or set(result) != {"ready", "path", "form_rejected", "rejection"}
                    or type(result["ready"]) is not bool or type(result["form_rejected"]) is not bool
                    or not isinstance(result["path"], str) or not isinstance(result["rejection"], str)
                    or result["path"] not in {"/start", "/auth/local-owner", "/auth/local-login", "other"}
                    or result["rejection"] not in {"none", "origin_rejected", "loopback_required", "forwarded_request_rejected",
                        "already_claimed", "authority_unavailable", "policy_unavailable", "invalid_form", "credentials_rejected",
                        "unclassified_form_rejection"}):
                result = fallback
        except (OSError, RuntimeError, KeyError):
            result = fallback
        return {**result, "claim_exists": (self.state / "dashboard-owner.json").is_file()}

    def owner_progress(self):
        observed = self.owner_diagnostic()
        if observed["ready"] and (observed["path"] == "/start" or observed["form_rejected"]):
            return observed
        return None

    def normal_support(self):
        self.record("normal-support-eight-package-inventory")
        self.view.navigate("http://127.0.0.1:8765/help")
        self.view.click("#support-prepare")
        self.view.wait(lambda: self.view.property("#support-result", "hidden") is False)
        if self.view.property("#support-download", "disabled") is not True:
            raise RuntimeError("Normal support download did not require review and consent.")
        self.view.click("#support-result details summary")
        value = self.view.wait(lambda: self.view.text("#support-preview"))
        if len(value.encode("utf-8")) > 2 * 1024 * 1024:
            raise RuntimeError("The native support preview exceeds its supported size.")
        report = json.loads(value)
        cohort = tomllib.loads((self.workspace / "release-cohort.toml").read_text(encoding="utf-8"))
        inventory = support_inventory(report, self.metadata, cohort)
        if self.password in value or re.search(r"[0-9a-f]{32}:[0-9a-f]{64}", value):
            raise RuntimeError("The native support preview exposed excluded private input.")
        if self.view.call("POST", self.view.session + "/elements", {
                "using": "css selector", "value": "#maintenance-authorization,#wrec-installation-code"}):
            raise RuntimeError("Private maintenance inputs must not appear on the support page.")
        before = set(self.downloads.glob("Lightwork-support-*.json"))
        self.view.click("#support-confirm")
        self.view.click("#support-download")
        self.view.wait(lambda: "reviewed report was downloaded" in self.view.text("#support-status"))

        def downloaded():
            files = set(self.downloads.glob("Lightwork-support-*.json")) - before
            if len(files) != 1:
                return None
            path = files.pop()
            return path if re.fullmatch(r"Lightwork-support-[0-9a-f]{32}\.json", path.name) and path.stat().st_size else None

        path = self.view.wait(downloaded)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError("The native support download exceeds its supported size.")
        text = path.read_text(encoding="utf-8")
        if (json.loads(text) != report or self.password in text
                or re.search(r"[0-9a-f]{32}:[0-9a-f]{64}", text)):
            raise RuntimeError("The native support download differs from the reviewed redacted report.")
        # The actual page verifies API-preview SHA256 against received bytes
        # before saving. Record those saved bytes and compare their full JSON
        # with the visible review without intercepting fetch or replacing IPC.
        inventory["download_sha256"] = digest(path)
        self.report["support_inventory"] = inventory
        self.view.screenshot(self.directory / "normal-support.png")
        self.record("normal-support-eight-package-inventory", passed=True)

    def transition(self, *, rollback=False):
        name = "signed-native-rollback" if rollback else "signed-native-upgrade"
        self.record(name)
        authorization = self.authorization(rollback=rollback)
        self.menu("Install & updates")
        version = self.metadata["version"]
        pieces = version.split(".")
        newer = ".".join(pieces[:2] + [str(int(pieces[2]) + 1)])
        self.assert_version(newer if rollback else version)
        if not rollback:
            self.view.click("#check-update")
            self.view.wait(lambda: newer in self.view.text("#update-version"))
            if self.view.property("#install-update", "disabled") is not True:
                raise RuntimeError("Native upgrade did not require the reviewed administrator authorization.")
            self.reject_invalid_authorization()
            self.record(name)
            # A refused installation consumes the review, not the legitimate
            # administrator grant. Obtain a fresh review with the real control.
            self.view.click("#check-update")
            self.view.wait(lambda: newer in self.view.text("#update-version"))
        self.view.fill("#maintenance-authorization", authorization)
        authorization = ""
        old_pids = {pid for _hwnd, pid in app_windows(self.binary)}
        # Never capture the private grant in a screenshot or test log.
        self.view.click("#rollback-install" if rollback else "#install-update")
        # The real retained helper waits for this native parent, replaces the
        # installer and starts its result window. Reattach to that actual window.
        self.view.wait(lambda: (windows := app_windows(self.binary)) and
                       all(pid not in old_pids for _hwnd, pid in windows), timeout=600)
        self.view.close()
        self.attach()
        self.assert_version(version if rollback else newer)
        text = "returned to " + version if rollback else "updated to " + newer
        self.view.wait(lambda: text in self.view.text("#maintenance-recovery-result"), timeout=600)
        self.view.screenshot(self.directory / (name + ".png"))
        self.record(name, passed=True)

    def reject_invalid_authorization(self):
        self.record("invalid-maintenance-authorization-refused")
        pids = {pid for _hwnd, pid in app_windows(self.binary)}
        before = (digest(self.binary), digest(self.runtime))
        if not pids:
            raise RuntimeError("The native fixture is not running for its denial check.")
        self.view.fill("#maintenance-authorization", "0" * 32 + ":" + "0" * 64)
        self.view.click("#install-update")
        self.view.wait(lambda: "could not complete that step" in self.view.text("#maintenance-status"))
        if (self.view.property("#maintenance-authorization", "value") != ""
                or self.view.property("#install-update", "disabled") is not True
                or {pid for _hwnd, pid in app_windows(self.binary)} != pids
                or (digest(self.binary), digest(self.runtime)) != before):
            raise RuntimeError("The refused native grant did not preserve the installation and clear private input.")
        self.view.screenshot(self.directory / "invalid-maintenance-authorization.png")
        self.record("invalid-maintenance-authorization-refused", passed=True)

    def startup_failure(self):
        self.record("startup-support-with-missing-packaged-runtime")
        self.stop()
        retained = self.install / "maverick.exe.acceptance-retained"
        self.runtime.rename(retained)
        primary = None
        try:
            self.start()
            self.view.wait(lambda: self.view.property("#trouble", "offsetHeight") > 0, timeout=30)
            self.view.click("#trouble details:last-of-type summary")
            self.view.click("#prepare-startup-report")
            value = self.view.wait(lambda: self.view.text("#startup-report"))
            report = json.loads(value)
            if report["schema"] != "lightwork.startup-support.v1" or report["packaged_runtime_available"] is not False:
                raise RuntimeError("Native startup diagnostics did not describe the missing runtime.")
            if self.password in value or str(self.state) in value:
                raise RuntimeError("Native startup report exposed excluded fixture data.")
            self.view.click("#download-startup-report")
            self.view.wait(lambda: "downloaded" in self.view.text("#startup-report-status"))
            downloaded = self.downloads / "Lightwork-startup-support.json"
            self.view.wait(lambda: downloaded.is_file() and downloaded.stat().st_size > 0)
            if json.loads(downloaded.read_text(encoding="utf-8")) != report:
                raise RuntimeError("Downloaded native support bytes differ from the reviewed report.")
            self.report["startup_support_sha256"] = digest(downloaded)
            self.view.screenshot(self.directory / "startup-support.png")
            self.record("startup-support-with-missing-packaged-runtime", passed=True)
        except BaseException as error:
            primary = error
            raise
        finally:
            stopped = self.cleanup_step("startup-probe-stop", self.stop)
            restored = self.cleanup_step("startup-probe-runtime", lambda: retained.rename(self.runtime))
            if primary is None and not (stopped and restored):
                raise RuntimeError("Native startup probe cleanup failed; exact resource names are in its report.")

    def run(self):
        primary = None
        try:
            self.record("install-signed-base")
            self.msi(self.base, "/i")
            version, runtime_directory = webview_runtime()
            self.env["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(runtime_directory)
            driver_path = download_driver(self.directory, version)
            self.trust("verify-driver")
            observed_driver = verified_driver_version(self.directory, version, command([str(driver_path), "--version"], timeout=15))
            self.report.update(driver_version=observed_driver, webview_runtime_version=version, driver_sha256=digest(driver_path),
                               base_msi_sha256=digest(self.base), candidate_msi_sha256=digest(self.candidate))
            self.driver_log = (self.directory / "driver.log").open("wb")
            self.driver = subprocess.Popen([str(driver_path), f"--port={self.driver_port}"], env=self.env,
                stdin=subprocess.DEVNULL, stdout=self.driver_log, stderr=subprocess.STDOUT)
            self.thumbprint = make_tls(self.directory)
            self.trust("trust")
            hosts = Path(os.environ["SystemRoot"]) / "System32/drivers/etc/hosts"
            with hosts_mapping(hosts, self.directory.name), update_server(self.directory, self.base, self.candidate, self.metadata) as (requests, _port):
                self.start()
                self.first_owner()
                self.normal_support()
                tenant = self.state / "tenants/native-acceptance"
                tenant.mkdir(parents=True, exist_ok=True)
                marker = tenant / "native-acceptance-state.txt"
                marker.write_text("before signed upgrade", encoding="utf-8")
                self.transition()
                marker.write_text("after signed upgrade", encoding="utf-8")
                self.transition(rollback=True)
                if marker.read_text(encoding="utf-8") != "before signed upgrade":
                    raise RuntimeError("The native rollback did not restore the captured workspace file.")
                self.record("native-rollback-restored-fixture-state", passed=True)
                if not {"/base.msi", "/next.msi"}.issubset(requests):
                    raise RuntimeError("Native installation did not download both signed packages from its compiled HTTPS channel.")
                self.report["https_routes"] = sorted(set(requests))
                self.startup_failure()
            self.report["passed"] = True
        except BaseException as error:
            primary = error
            self.report["failed_phase"] = self.report.get("phase")
            raise
        finally:
            cleanup = [("native-app", self.stop)]
            if self.thumbprint:
                cleanup.append(("fixture-tls-trust", lambda: self.trust("cleanup")))
            cleanup += [("candidate-uninstall", lambda: self.msi(self.candidate, "/x")),
                        ("base-uninstall", lambda: self.msi(self.base, "/x"))]
            for name, operation in cleanup:
                self.cleanup_step(name, operation)
            if self.driver:
                def stop_driver():
                    with contextlib.suppress(OSError):
                        self.driver.kill()
                    self.driver.wait(timeout=15)
                self.cleanup_step("webview-driver", stop_driver)
            if self.driver_log:
                self.cleanup_step("driver-log", self.driver_log.close)
            self.password = ""
            self.report["journey_passed"] = self.report["passed"]
            self.report["passed"] = self.report["passed"] and not self.report["cleanup_errors"]
            if not self.cleanup_step("acceptance-report", lambda: self.record("complete" if self.report["passed"] else "failed")):
                self.report["passed"] = False
                print("Native acceptance report could not be written; cleanup failure recorded in memory.", flush=True)
            if primary is None and self.report["cleanup_errors"]:
                raise RuntimeError("Native acceptance cleanup failed; exact resource names are in its report.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "runner", "base", "candidate"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    Journey(args.workspace.resolve(), args.runner.resolve(), args.base.resolve(), args.candidate.resolve()).run()


if __name__ == "__main__":
    main()
