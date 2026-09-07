"""Stage a complete frozen runtime and a strict customer Tauri configuration.

Run on the build host. The customer never executes Python, Git, or pip.
Publisher public keys and channel URLs are build inputs, never webview input.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
TARGET = "x86_64-pc-windows-msvc"
UPGRADE_CODE = "AB5BC260-7EC9-4B70-AF78-28F4067BC530"


def validate_channel(endpoint: str, public_key: str) -> None:
    url = urlsplit(endpoint)
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError("customer update endpoint must have a valid HTTPS port") from exc
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or len(endpoint) > 4096
            or port == 0 or "\\" in endpoint or any(ord(char) <= 32 for char in endpoint)):
        raise ValueError("customer update endpoint must be an HTTPS URL without credentials or fragment")
    try:
        # Tauri's public key is base64-wrapped minisign text. Validate both
        # layers so a missing/placeholder key cannot produce a customer build.
        decoded = base64.b64decode(public_key, validate=True).decode("ascii").splitlines()
        raw = base64.b64decode(decoded[1], validate=True)
        if len(decoded) != 2 or not decoded[0].startswith("untrusted comment:") or len(raw) != 42:
            raise ValueError
        if raw[:2] != b"Ed":
            raise ValueError
    except (ValueError, IndexError, UnicodeError) as exc:
        raise ValueError("customer update public key is not a valid Tauri minisign key") from exc


def verify_runtime(binary: Path, cohort: dict, *, run=subprocess.run) -> dict:
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("runtime must be an existing regular executable")
    with binary.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("customer runtime must be a Windows executable")
    # Synthetic empty home keeps build verification away from operator data.
    import os
    with tempfile.TemporaryDirectory(prefix="lightwork-runtime-check-") as home:
        environment = dict(os.environ, MAVERICK_HOME=home, NO_COLOR="1")
        result = run([str(binary.resolve()), "version"], check=True, capture_output=True,
                     text=True, timeout=120, env=environment)
        for package in cohort["packages"]:
            expected = rf"^\s*{re.escape(package['distribution'])}\s+{re.escape(cohort['version'])}\s*$"
            if re.search(expected, result.stdout, re.MULTILINE) is None:
                raise ValueError(f"frozen runtime omits the expected {package['distribution']} cohort version")
        for arguments in (["release-runtime-check"], ["dashboard", "--help"], ["mcp", "--help"]):
            run([str(binary.resolve()), *arguments], check=True, capture_output=True,
                text=True, timeout=120, env=environment)
    return {"sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "bytes": binary.stat().st_size}


def stage(runtime: Path, output: Path, endpoint: str, public_key: str,
          *, fixture_version: str | None = None, fault_fragment: Path | None = None) -> Path:
    validate_channel(endpoint, public_key)
    cohort = tomllib.loads((ROOT / "release-cohort.toml").read_text(encoding="utf-8"))
    metadata = verify_runtime(runtime, cohort)
    version = fixture_version or cohort["version"]
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("Windows customer package version must be three-part numeric")
    if fault_fragment is not None and fixture_version is None:
        raise ValueError("fault injection is allowed only in a labelled acceptance fixture")
    output.mkdir(parents=True, exist_ok=False)
    sidecar = output / f"maverick-{TARGET}.exe"
    shutil.copyfile(runtime, sidecar)
    if hashlib.sha256(sidecar.read_bytes()).hexdigest() != metadata["sha256"]:
        raise ValueError("staged runtime changed during copy")
    wix = {"upgradeCode": UPGRADE_CODE, "enableElevatedUpdateTask": False}
    if fault_fragment is not None:
        wix["fragmentPaths"] = [str(fault_fragment.resolve())]
        wix["featureRefs"] = ["LightworkAcceptanceHarness"]
    config = {
        "version": version,
        "bundle": {
            "targets": ["msi"], "category": "Productivity",
            "shortDescription": "Lightwork workspace and complete local runtime",
            "longDescription": "Lightwork with its complete runtime. No Python, Git, or source repository access is required on the customer computer.",
            "externalBin": [str(output.resolve() / "maverick")],
            "icon": [str(ROOT / "apps/installer-desktop/src-tauri/icons/icon.ico")],
            "createUpdaterArtifacts": False,
            "windows": {"webviewInstallMode": {"type": "offlineInstaller"},
                        "allowDowngrades": False, "wix": wix},
        },
        "plugins": {"updater": {"pubkey": public_key, "endpoints": [endpoint],
                                  "windows": {"installMode": "passive"}}},
    }
    target = output / "tauri.customer.json"
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (output / "runtime-inventory.json").write_text(json.dumps({
        "schema": "lightwork.windows-customer-build.v1", "version": version,
        "cohort_version": cohort["version"], "complete_release_cohort": True,
        "packages": cohort["packages"], "runtime": metadata,
        "acceptance_fixture": fixture_version is not None,
        "requires_customer_python": False, "requires_customer_git": False,
        "publisher_signature_verified": False,
    }, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--acceptance-fixture-version")
    parser.add_argument("--acceptance-fault-fragment", type=Path)
    args = parser.parse_args()
    print(stage(args.runtime, args.output, args.endpoint,
                args.public_key_file.read_text(encoding="utf-8").strip(),
                fixture_version=args.acceptance_fixture_version,
                fault_fragment=args.acceptance_fault_fragment))


if __name__ == "__main__":
    main()
