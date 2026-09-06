from __future__ import annotations

import ast
import base64
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

MODULE = Path(__file__).with_name("stage_customer.py")
spec = importlib.util.spec_from_file_location("stage_customer", MODULE)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def public_key():
    raw = base64.b64encode(b"Ed" + b"\x00" * 40).decode()
    return base64.b64encode(f"untrusted comment: test-only key\n{raw}\n".encode()).decode()


def test_direct_desktop_native_build_has_a_valid_windows_icon():
    manifest_dir = stage.ROOT / "apps/desktop/src-tauri"
    config = json.loads((manifest_dir / "tauri.conf.json").read_text(encoding="utf-8"))
    icons = [manifest_dir / name for name in config["bundle"]["icon"] if name.lower().endswith(".ico")]
    assert icons, "Direct cargo tests need a Windows icon before customer staging runs"
    for icon in icons:
        assert icon.is_file()
        assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00", "Expected an ICO resource"


def test_synthetic_workflow_signing_explicitly_supplies_the_empty_key_password():
    workflow = yaml.safe_load((stage.ROOT / ".github/workflows/windows-customer.yml").read_text(encoding="utf-8"))
    commands = []
    for step in workflow["jobs"]["build-and-accept"]["steps"]:
        for line in step.get("run", "").splitlines():
            if re.match(r"\s*node\s+\S+\s+signer\s+sign\s", line):
                commands.append(shlex.split(line))
    assert len(commands) == 2, "Both synthetic MSI signing paths must be covered"
    for arguments in commands:
        # Tauri generates --ci keys with an empty password, but signer sign
        # still prompts when the password argument is absent, even in CI.
        assert "--password" in arguments, "Headless signing must never prompt for the fixture key password"
        index = arguments.index("--password")
        assert arguments[index + 1] == "", "Use the generated test key's exact empty password"
        assert index + 2 < len(arguments), "The signature must still target its MSI argument"


@pytest.mark.parametrize("url", [
    "http://updates.example.test/latest.json", "file:///tmp/update", "https://user@example.test/u",
    "https://example.test/u#fragment", "https:///missing", "javascript:alert(1)",
    "https://example.test:bad/u", "https://example.test:0/u", "https://example.test/ u",
])
def test_channel_refuses_unsafe_or_credential_bearing_address(url):
    with pytest.raises(ValueError, match="HTTPS"):
        stage.validate_channel(url, public_key())


@pytest.mark.parametrize("key", ["", "placeholder", "!!!!", base64.b64encode(b"not a public key").decode()])
def test_customer_build_requires_real_key_shape(key):
    with pytest.raises(ValueError, match="minisign"):
        stage.validate_channel("https://updates.example.test/latest.json", key)


def test_every_cohort_member_must_be_in_the_actual_frozen_runtime(tmp_path):
    runtime = tmp_path / "maverick.exe"
    runtime.write_bytes(b"MZfixture")
    cohort = {"version": "1.2.3", "packages": [
        {"distribution": "maverick-agent"}, {"distribution": "maverick-dashboard"},
    ]}
    def incomplete(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "  maverick-agent 1.2.3\n", "")
    with pytest.raises(ValueError, match="maverick-dashboard"):
        stage.verify_runtime(runtime, cohort, run=incomplete)
    calls = []
    def complete(command, **kwargs):
        calls.append(command[1:])
        assert kwargs["env"]["MAVERICK_HOME"] != str(stage.ROOT)
        return subprocess.CompletedProcess(command, 0, "  maverick-agent 1.2.3\n  maverick-dashboard 1.2.3\n", "")
    assert stage.verify_runtime(runtime, cohort, run=complete)["bytes"] == 9
    assert calls == [["version"], ["release-runtime-check"], ["dashboard", "--help"], ["mcp", "--help"]]


def test_customer_config_bundles_offline_runtime_and_never_signs_with_build_secrets(tmp_path, monkeypatch):
    runtime = tmp_path / "maverick.exe"
    runtime.write_bytes(b"MZfixture")
    monkeypatch.setattr(stage, "verify_runtime", lambda *_: {
        "sha256": stage.hashlib.sha256(runtime.read_bytes()).hexdigest(), "bytes": runtime.stat().st_size,
    })
    path = stage.stage(runtime, tmp_path / "staged", "https://updates.example.test/latest.json", public_key())
    config = json.loads(path.read_text(encoding="utf-8"))
    bundle = config["bundle"]
    assert bundle["targets"] == ["msi"]
    assert bundle["windows"]["webviewInstallMode"]["type"] == "offlineInstaller"
    assert bundle["windows"]["allowDowngrades"] is False
    assert bundle["windows"]["wix"]["upgradeCode"] == stage.UPGRADE_CODE
    assert "fragmentPaths" not in bundle["windows"]["wix"]
    assert bundle["createUpdaterArtifacts"] is False
    assert Path(bundle["externalBin"][0] + "-" + stage.TARGET + ".exe").read_bytes() == runtime.read_bytes()
    assert config["plugins"]["updater"]["pubkey"] == public_key()
    inventory = json.loads(path.with_name("runtime-inventory.json").read_text(encoding="utf-8"))
    assert len(inventory["packages"]) == 8
    assert inventory["acceptance_fixture"] is False
    assert inventory["publisher_signature_verified"] is False


def test_fault_injection_cannot_enter_a_customer_release(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "verify_runtime", lambda *_: {})
    with pytest.raises(ValueError, match="acceptance fixture"):
        stage.stage(tmp_path / "runtime", tmp_path / "staged", "https://updates.example.test/latest.json",
                    public_key(), fault_fragment=tmp_path / "fault.wxs")
    assert not (tmp_path / "staged").exists()


def test_same_stage_directory_cannot_reuse_stale_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "verify_runtime", lambda *_: {})
    with pytest.raises(FileExistsError):
        stage.stage(tmp_path / "runtime", tmp_path, "https://updates.example.test/latest.json", public_key())


def test_acceptance_failure_fragment_is_referenced_so_linker_cannot_drop_it(tmp_path, monkeypatch):
    runtime = tmp_path / "maverick.exe"
    runtime.write_bytes(b"MZfixture")
    monkeypatch.setattr(stage, "verify_runtime", lambda *_: {
        "sha256": stage.hashlib.sha256(runtime.read_bytes()).hexdigest(), "bytes": runtime.stat().st_size,
    })
    path = stage.stage(runtime, tmp_path / "staged", "https://updates.example.test/latest.json", public_key(),
                       fixture_version="0.1.8", fault_fragment=MODULE.with_name("acceptance-failure.wxs"))
    config = json.loads(path.read_text(encoding="utf-8"))
    import xml.etree.ElementTree as ET
    fragment = ET.parse(config["bundle"]["windows"]["wix"]["fragmentPaths"][0]).getroot()
    ns = {"w": "http://schemas.microsoft.com/wix/2006/wi"}
    feature = fragment.find("w:Fragment/w:Feature", ns)
    assert feature.attrib["Id"] in config["bundle"]["windows"]["wix"]["featureRefs"]
    action = fragment.find("w:Fragment/w:InstallExecuteSequence/w:Custom", ns)
    assert action.attrib["After"] == "InstallFiles"
    assert action.text == "LIGHTWORK_ACCEPTANCE_FAIL=1"


def test_customer_smoke_checks_registered_setup_route_in_both_modes():
    # Compare smoke addresses with actual decorated GET handlers, so an invalid
    # setup URL cannot pass just because normal and recovery smoke copies agree.
    script = MODULE.with_name("test-customer.ps1").read_text(encoding="utf-8")
    app = ast.parse((stage.ROOT / "packages/maverick-dashboard/maverick_dashboard/app.py").read_text(encoding="utf-8"))
    handlers = {}
    for node in app.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == "get" and decorator.args
                        and isinstance(decorator.args[0], ast.Constant)):
                    handlers[decorator.args[0].value] = node.name
    paths = re.findall(r"Invoke-WebRequest 'http://127\.0\.0\.1:8765([^']+)'", script)
    assert paths
    assert all(path in handlers for path in paths)
    setup_paths = [path for path in paths if handlers[path] == "get_started_page"]
    assert len(setup_paths) == 2, "Normal render and isolated recovery must exercise the real setup handler."


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="PowerShell runtime unavailable")
def test_actual_powershell_acceptance_guards_reject_unproven_install_results():
    completed = subprocess.run([
        shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-NonInteractive",
        "-File", str(MODULE.with_name("test-acceptance-guards.ps1")),
        "-ScriptDirectory", str(MODULE.parent),
    ], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "8 MSI failure cases and 5 native channel cases passed" in completed.stdout
