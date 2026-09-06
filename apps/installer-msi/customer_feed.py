"""Write the update feed only after independent signature verification.

This build-host utility does not sign, verify certificates, publish, or execute
an installer. The protected workflow verifies both signatures before calling it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def feed(inventory: dict, signature: str, url_template: str) -> dict:
    version = inventory.get("version", "")
    if (inventory.get("schema") != "lightwork.windows-customer-build.v1"
            or inventory.get("acceptance_fixture") is not False
            or inventory.get("complete_release_cohort") is not True
            or not re.fullmatch(r"\d+\.\d+\.\d+", version)):
        raise ValueError("Only a complete customer candidate may produce update metadata")
    if url_template.count("{version}") != 1:
        raise ValueError("Installer address must contain one immutable {version} placeholder")
    url = url_template.replace("{version}", version)
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Installer address must have a valid HTTPS port") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment or len(url) > 4096
            or any(ord(char) <= 32 for char in url) or "{" in url or "}" in url
            or port == 0 or "\\" in url):
        raise ValueError("Installer address must be HTTPS without credentials or fragments")
    try:
        decoded = base64.b64decode(signature, validate=True).decode("ascii").splitlines()
        if (len(decoded) != 4 or not decoded[0].startswith("untrusted comment:")
                or not decoded[2].startswith("trusted comment:")
                or len(base64.b64decode(decoded[1], validate=True)) != 74
                or len(base64.b64decode(decoded[3], validate=True)) != 64):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Expected the verified Tauri installer signature") from exc
    runtime_sha256 = (inventory.get("runtime") or {}).get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", runtime_sha256):
        raise ValueError("The customer release requires its exact runtime SHA-256 identity")
    return {"version": version, "runtime_sha256": runtime_sha256, "notes": "Lightwork customer release " + version,
            "pub_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "platforms": {"windows-x86_64": {"signature": signature, "url": url}}}


def write_feed(directory: Path, url_template: str) -> None:
    """Check immutable identity before changing the visible latest pointer."""
    inventory = json.loads((directory / "runtime-inventory.json").read_text(encoding="utf-8"))
    signature = (directory / "Lightwork.msi.sig").read_text(encoding="utf-8").strip()
    content = feed(inventory, signature, url_template)
    digest = hashlib.sha256((directory / "Lightwork.msi").read_bytes()).hexdigest()
    content["installer_sha256"] = digest
    versions = directory / "versions"
    versions.mkdir(exist_ok=True)
    # Publish this immutable address alongside latest.json. Repair and retained
    # pre-upgrade installers must remain available after the channel advances.
    versioned = versions / (content["version"] + ".json")
    if versioned.exists():
        existing = json.loads(versioned.read_text(encoding="utf-8"))
        if {key: value for key, value in existing.items() if key != "pub_date"} != {key: value for key, value in content.items() if key != "pub_date"}:
            raise ValueError("Refusing to replace an immutable release manifest")
        content["pub_date"] = existing["pub_date"]
    serialized = json.dumps(content, indent=2) + "\n"
    if not versioned.exists():
        # Exclusive creation protects immutable releases from concurrent writers.
        with versioned.open("x", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
    import tempfile
    for name, value in (("SHA256SUMS", f"{digest}  Lightwork.msi\n"), ("latest.json", serialized)):
        descriptor, temporary = tempfile.mkstemp(prefix=".release-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / name)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--installer-url-template", required=True)
    args = parser.parse_args()
    write_feed(args.directory, args.installer_url_template)


if __name__ == "__main__":
    main()
