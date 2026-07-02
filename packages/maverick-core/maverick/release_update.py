"""Governed release updates — verify + plan upgrades, offline-capable.

A **release manifest** is an Ed25519-signed JSON document (same trust anchor as
licenses, see :mod:`maverick.entitlements`) describing one release: its version,
its artifacts (name + sha256 + size), the **minimum version it can upgrade
from**, and the ordered list of **governed migrations** it introduces. Because
it verifies **offline**, an air-gapped bank can receive an update bundle
out-of-band (physical media / one-way transfer), verify the signature **and**
every artifact hash, and apply it under change control — no network, no
"trust the vendor," and every step reproducible.

This module owns the *decision* layer — verify a manifest, verify a bundle's
contents against it, and compute a safe upgrade **plan**. Swapping the running
binary/container is deployment-specific (systemd, container tag, Tauri
auto-update) and is intentionally out of scope here; those callers ask this
module "is this update authentic, intact, and safe to apply from where I am?".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .entitlements import _trusted_pubkeys, sign_license, verify_license


def _ver(s: str | None) -> tuple[int, int, int]:
    """Parse ``v0.1.6`` / ``0.1.6`` / ``0.1.6-rc1`` → ``(0, 1, 6)``."""
    core = str(s or "0").lstrip("vV").split("-")[0].split("+")[0]
    parts = [int(x) for x in core.split(".") if x.isdigit()]
    parts += [0, 0, 0]
    return tuple(parts[:3])  # type: ignore[return-value]


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(version: str, artifacts: list[dict], *, min_from: str,
                   migrations: list[str] | None, private_key_hex: str,
                   notes: str = "") -> dict:
    """Sign a release manifest. ``artifacts`` are ``{name, sha256, size}`` dicts."""
    payload = {
        "kind": "lightwork.release",
        "version": str(version),
        "min_from": str(min_from),
        "artifacts": artifacts,
        "migrations": list(migrations or []),
        "notes": notes,
    }
    return sign_license(payload, private_key_hex)  # generic signed-doc envelope


def verify_manifest(doc: dict, trusted_pubkeys: list[str] | None = None) -> tuple[bool, str]:
    """Authenticate a manifest against the shipped publisher key set."""
    return verify_license(doc, _trusted_pubkeys(trusted_pubkeys))


@dataclass
class BundleCheck:
    ok: bool
    reason: str
    version: str = ""
    issues: list[str] = field(default_factory=list)


def verify_bundle(bundle_dir: str | Path, *,
                  trusted_pubkeys: list[str] | None = None) -> BundleCheck:
    """Verify an offline update bundle: the manifest signature **and** every
    artifact's sha256. Both must hold — a tampered binary is caught even if the
    manifest is authentic (the hash won't match) and vice-versa."""
    d = Path(bundle_dir)
    mpath = d / "manifest.json"
    if not mpath.exists():
        return BundleCheck(False, "no manifest.json in bundle")
    try:
        doc = json.loads(mpath.read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        return BundleCheck(False, f"unreadable manifest: {e}")
    ok, why = verify_manifest(doc, trusted_pubkeys)
    if not ok:
        return BundleCheck(False, f"manifest signature: {why}", doc.get("version", ""))
    issues: list[str] = []
    for art in doc.get("artifacts", []):
        name = art.get("name", "?")
        fp = d / name
        if not fp.exists():
            issues.append(f"missing artifact: {name}")
            continue
        actual = sha256_file(fp)
        if actual != art.get("sha256"):
            issues.append(f"hash mismatch: {name} ({actual[:12]}… != "
                          f"{str(art.get('sha256'))[:12]}…)")
    if issues:
        return BundleCheck(False, "artifact verification failed",
                           doc.get("version", ""), issues)
    return BundleCheck(True, "ok", doc.get("version", ""))


@dataclass
class UpgradePlan:
    ok: bool
    reason: str
    from_version: str
    to_version: str
    migrations: list[str] = field(default_factory=list)
    downgrade: bool = False


def plan_upgrade(current_version: str, manifest: dict) -> UpgradePlan:
    """Decide whether it's safe to apply ``manifest`` from ``current_version``.

    Blocks a **downgrade**, a **no-op** (same version), and a **version gap**
    (current below the manifest's ``min_from`` — the site must step through an
    intermediate release so migrations apply in order). On success, returns the
    ordered governed migrations the caller must run."""
    cur, to = _ver(current_version), _ver(manifest.get("version"))
    mn = _ver(manifest.get("min_from"))
    migs = list(manifest.get("migrations", []))
    if to == cur:
        return UpgradePlan(False, "already on this version", current_version,
                           manifest.get("version", ""), [])
    if to < cur:
        return UpgradePlan(False, "manifest is older than the running version",
                           current_version, manifest.get("version", ""),
                           downgrade=True)
    if cur < mn:
        return UpgradePlan(
            False,
            f"version gap: running {current_version} is below min_from "
            f"{manifest.get('min_from')} — upgrade to an intermediate release first",
            current_version, manifest.get("version", ""), migs)
    return UpgradePlan(True, "ok", current_version, manifest.get("version", ""), migs)


# ---- CLI: python -m maverick.release_update {verify-bundle,plan} ------------

def main(argv: list[str] | None = None) -> int:  # pragma: no cover -- CLI shell
    import argparse

    p = argparse.ArgumentParser(prog="maverick.release_update",
                                description="Verify + plan Lightwork updates offline.")
    sub = p.add_subparsers(dest="cmd", required=True)

    vb = sub.add_parser("verify-bundle", help="verify an offline update bundle dir")
    vb.add_argument("dir")
    vb.add_argument("--pubkey", default=None, help="comma-separated trusted pubkey hex")

    pl = sub.add_parser("plan", help="plan an upgrade from the running version")
    pl.add_argument("--current", required=True, help="running version, e.g. v0.1.6")
    pl.add_argument("--manifest", required=True, help="path to a manifest.json")

    args = p.parse_args(argv)

    if args.cmd == "verify-bundle":
        trust = [k.strip() for k in args.pubkey.split(",")] if args.pubkey else None
        res = verify_bundle(args.dir, trusted_pubkeys=trust)
        print(f"{'OK' if res.ok else 'FAIL'}: {res.reason}  (version {res.version or '?'})")
        for issue in res.issues:
            print("  -", issue)
        return 0 if res.ok else 1

    manifest = json.loads(Path(args.manifest).read_text("utf-8"))
    plan = plan_upgrade(args.current, manifest)
    print(f"{'OK' if plan.ok else 'BLOCKED'}: {plan.reason}")
    if plan.ok:
        print(f"  {plan.from_version} → {plan.to_version}")
        print(f"  migrations: {', '.join(plan.migrations) or 'none'}")
    return 0 if plan.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
