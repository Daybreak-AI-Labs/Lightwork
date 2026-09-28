"""Settings > License: see, install, change and issue signed licenses in the app.

The page does what ``maverick license show / set / issue / keygen`` do in a
terminal, through the same kernel functions in :mod:`maverick.entitlements`:

* the installed license in plain words (``license show``);
* "Install a license file": the uploaded file is checked against this
  server's trusted license keys before it is written, and a file that does
  not check out is refused;
* "Change what this installation is licensed for" (``license set``) and
  "Issue a license" (``license issue``), signed with the publisher key the
  kernel reads from the server's secret store only
  (:func:`maverick.entitlements.publisher_private_key`). The key is never read
  from settings, never shown, never logged and never recorded;
* "Make a signing key pair" (``license keygen``): made on the server, shown
  once in the response and never stored or returned again.

Every route needs the global ``admin`` permission. Every change is
same-origin checked, refused without ``confirm`` when it would replace an
installed license, and recorded in one activity-record row before it happens.
Rows name signing keys only by the short fingerprint of their public key.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from maverick.entitlements import DEFAULT_GRACE_DAYS
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import auth

router = APIRouter()

PAGE_PATH = "/settings/license"
API_PATH = "/api/v1/admin/license"
MAX_LICENSE_BYTES = 64 * 1024

_STATIC = Path(__file__).parent / "static"
_NO_STORE = {"Cache-Control": "no-store"}
_NAME = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_EDITION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$")
_TIERS = (("basic", "Basic"), ("gold", "Gold"), ("platinum", "Platinum"))
_TIER_LABELS = dict(_TIERS)
_PAID_SUITE_LABELS = {"fleet": "Agent fleet"}

_NO_SIGNING_KEY = ("This server cannot sign licenses because no signing key is set up on it. "
                   "Nothing was signed.")
_UNUSABLE_KEY = ("The signing key set up on this server cannot be used. Ask whoever runs your "
                 "server to check it. Nothing was signed.")
_REPLACE_REFUSAL = "A license is already installed. Confirm that you want to replace it."
_NOT_A_LICENSE = "That file is not a Lightwork license, so nothing was installed."
_UNTRUSTED_WARNING = (
    "This server does not trust the key that signed this license yet, so its paid features stay "
    "off here until whoever runs your server adds the key's public key to the trusted license keys.")
_VERIFY_REFUSALS = {
    "malformed": _NOT_A_LICENSE,
    "unsigned": "That license is not signed, so nothing was installed.",
    "no_trust_anchor": ("This server has no trusted license keys yet, so it cannot check a license. "
                        "Nothing was installed."),
    "untrusted_key": "That license was signed with a key this server does not trust, so nothing was installed.",
    "bad_signature": "That license was changed after it was signed, so nothing was installed.",
}
_CHECK_SENTENCES = {
    "licensed": "This license checks out and is in date.",
    "grace": "This license checks out but has ended. It keeps working for a short time.",
    "expired": "This license checks out but has ended, so its paid features would stay off.",
}


def _app():
    from . import app as app_module

    return app_module


def _admin(request: Request) -> None:
    auth.require_global_permission(request, "admin")


def _mutation(request: Request) -> None:
    _app()._require_same_origin(request)
    _admin(request)


def _record(request: Request, verb: str, refusal: str, **detail: Any) -> None:
    """One activity-record row per license change, written BEFORE the change so
    a refused row stops it. Never pass key material here: signing keys are
    named only by the fingerprint of their public key."""
    from maverick.audit import AuditRefused, EventKind, audit_event
    try:
        audit_event(EventKind.CONTROL_PLANE_ACTION, agent="dashboard", _global=True,
                    verb=verb, principal="*",
                    actor=auth.caller_principal(request) or "local administrator",
                    ok=True, **detail)
    except AuditRefused as exc:
        raise HTTPException(503, refusal) from exc


def _plain(value: str) -> str:
    return str(value).replace("_", " ").replace(":", " ").strip().capitalize()


def _fingerprint(public_key: str) -> str:
    return str(public_key or "")[:16]


# ---------------------------------------------------------------- state
def _license_path() -> Path:
    from maverick.entitlements import _default_license_path

    return _default_license_path()


def _installed_doc() -> dict | None:
    """The raw installed license document (prefills the change form)."""
    path = _license_path()
    try:
        if not path.is_file() or path.stat().st_size > MAX_LICENSE_BYTES:
            return None
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, str) and _NAME.match(v)]


def _date_of(value: Any) -> str:
    from maverick.entitlements import _parse_dt

    try:
        parsed = _parse_dt(value) if value else None
    except (TypeError, ValueError):
        return ""
    return parsed.date().isoformat() if parsed else ""


def _current(doc: dict | None) -> dict[str, Any]:
    doc = doc or {}
    tier = str(doc.get("tier") or "")
    edition = doc.get("edition")
    grace = doc.get("grace_days")
    seats = doc.get("seats")
    return {
        "customer": str(doc.get("customer") or "")[:200],
        "tier": tier if tier in _TIER_LABELS else "gold",
        "edition": edition if isinstance(edition, str) and _EDITION.match(edition) else "enterprise",
        "suites": _names(doc.get("suites")),
        "features": _names(doc.get("features")),
        "expires": _date_of(doc.get("expires_at")),
        "grace_days": grace if isinstance(grace, int) and 0 <= grace <= 3650 else DEFAULT_GRACE_DAYS,
        "seats": seats if isinstance(seats, int) and seats > 0 else None,
    }


def _suite_choices(extra: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Paid add-on suites first, then every business suite, from the kernel's
    own registries (so the list is always the real one)."""
    from maverick.departments import department_title
    from maverick.domain import SUITE_PREFIXES
    from maverick.entitlements import GATED_SUITES

    paid = [{"value": s, "label": _PAID_SUITE_LABELS.get(s, department_title(s)), "paid": True}
            for s in sorted(GATED_SUITES)]
    rest = sorted(
        ({"value": s, "label": department_title(s), "paid": False}
         for s in set(SUITE_PREFIXES.values()) | set(extra) if s not in GATED_SUITES),
        key=lambda row: row["label"].lower())
    return paid + rest


def _feature_label(name: str) -> str:
    from .system_checks_api import _FEATURE_NAMES

    return _FEATURE_NAMES.get(name, _plain(name))


def _feature_choices(extra: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    from maverick.entitlements import GATED_FEATURES

    rows = [{"value": f, "label": _feature_label(f), "tier": _TIER_LABELS.get(GATED_FEATURES[f], "")}
            for f in sorted(GATED_FEATURES)]
    rows += [{"value": f, "label": _feature_label(f), "tier": ""}
             for f in sorted(set(extra) - set(GATED_FEATURES))]
    return rows


def _signing() -> dict[str, Any]:
    """Whether this server can sign, without ever exposing the key itself."""
    from maverick import entitlements

    key = entitlements.publisher_private_key()
    if not key:
        return {"state": "missing", "fingerprint": "", "trusted": False}
    try:
        public = entitlements.public_key_for(key)
    except Exception:  # noqa: BLE001 -- a bad key is reported, never echoed
        return {"state": "unusable", "fingerprint": "", "trusted": False}
    return {"state": "ready", "fingerprint": _fingerprint(public),
            "trusted": entitlements.key_trusted(public)}


def license_state() -> dict[str, Any]:
    from maverick.entitlements import _trusted_pubkeys

    from .system_checks_api import _license

    current = _current(_installed_doc())
    return {
        "summary": _license(),
        "installed": _license_path().exists(),
        "can_check": bool(_trusted_pubkeys()),
        "signing": _signing(),
        "current": current,
        "tiers": [{"value": v, "label": label} for v, label in _TIERS],
        "suites": _suite_choices(current["suites"]),
        "features": _feature_choices(current["features"]),
        "default_grace_days": DEFAULT_GRACE_DAYS,
    }


def _doc_summary(doc: dict) -> dict[str, Any]:
    suites = {row["value"]: row["label"] for row in _suite_choices()}
    seats = doc.get("seats")
    return {
        "customer": str(doc.get("customer") or "")[:200],
        "plan": _TIER_LABELS.get(str(doc.get("tier")), "Basic"),
        "add_ons": [suites.get(s, _plain(s)) for s in _names(doc.get("suites"))],
        "features": [_feature_label(f) for f in _names(doc.get("features"))],
        "expires": _date_of(doc.get("expires_at")),
        "seats": seats if isinstance(seats, int) and seats > 0 else None,
    }


def _filename(doc: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(doc.get("customer") or "").lower()).strip("-")[:40]
    return f"lightwork-license-{slug or 'customer'}.json"


def _result(doc: dict, *, trusted: bool, installed: bool) -> dict[str, Any]:
    from maverick.entitlements import license_text

    return {
        "installed": installed,
        "trusted": trusted,
        "warning": "" if trusted else _UNTRUSTED_WARNING,
        "license": _doc_summary(doc),
        "file": {"name": _filename(doc), "text": license_text(doc)},
        # The signing key's PUBLIC half: what an operator adds to the trusted keys.
        "signed_with": {"fingerprint": _fingerprint(doc["key_id"]), "public_key": doc["key_id"]},
    }


def _refuse_unconfirmed_replace(confirm: bool) -> bool:
    replacing = _license_path().exists()
    if replacing and not confirm:
        raise HTTPException(409, _REPLACE_REFUSAL)
    return replacing


def _install(doc: dict) -> None:
    from maverick.entitlements import install_license, reset_cache

    install_license(doc)
    reset_cache()


def _audit_terms(doc: dict) -> dict[str, Any]:
    return {"customer": str(doc.get("customer") or "")[:200], "tier": doc.get("tier"),
            "suites": _names(doc.get("suites")), "features": _names(doc.get("features")),
            "expires_at": doc.get("expires_at"), "seats": doc.get("seats"),
            "license_id": str(doc.get("license_id") or "")[:40],
            "signing_key_fingerprint": _fingerprint(doc.get("key_id"))}


# ---------------------------------------------------------------- sign (issue / set)
class LicenseTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: str = Field(min_length=1, max_length=200)
    tier: Literal["basic", "gold", "platinum"] = "gold"
    edition: str = Field(default="enterprise", min_length=1, max_length=60)
    suites: list[str] = Field(default_factory=list, max_length=100)
    features: list[str] = Field(default_factory=list, max_length=100)
    expires: date | None = None
    grace_days: int = Field(default=DEFAULT_GRACE_DAYS, ge=0, le=3650)
    seats: int | None = Field(default=None, ge=1, le=10_000_000)
    install: bool = False
    confirm: bool = False


def _terms(body: LicenseTerms) -> dict[str, Any]:
    customer = body.customer.strip()
    if not customer:
        raise HTTPException(422, "Enter who the license is for.")
    if not _EDITION.match(body.edition):
        raise HTTPException(422, "Choose an edition from the list.")
    for name in (*body.suites, *body.features):
        if not _NAME.match(name):
            raise HTTPException(422, "Choose suites and paid features from the lists.")
    return {
        "customer": customer, "tier": body.tier, "edition": body.edition,
        "suites": list(dict.fromkeys(body.suites)), "features": list(dict.fromkeys(body.features)),
        "seats": body.seats, "expires": body.expires.isoformat() if body.expires else None,
        "grace_days": body.grace_days,
    }


def _sign(request: Request, terms: dict[str, Any], install: bool, confirm: bool) -> dict[str, Any]:
    from maverick import entitlements

    key = entitlements.publisher_private_key()
    if not key:
        raise HTTPException(503, _NO_SIGNING_KEY)
    replacing = _refuse_unconfirmed_replace(confirm) if install else False
    try:
        payload = entitlements.build_license_payload(**terms)
    except ValueError:
        raise HTTPException(422, "Some of the license details need fixing. Check the plan and the end date.") from None
    try:
        doc = entitlements.sign_license(payload, key)
    except Exception:  # noqa: BLE001 -- never surface the key or its error
        raise HTTPException(503, _UNUSABLE_KEY) from None
    trusted = entitlements.key_trusted(doc["key_id"])
    _record(request, "license_set" if install else "license_issued",
            "This could not be added to the activity record, so nothing was signed.",
            installed=install, replaced=replacing, trusted=trusted, **_audit_terms(doc))
    if install:
        _install(doc)
    return _result(doc, trusted=trusted, installed=install)


@router.post(API_PATH + "/sign")
async def sign_license(body: LicenseTerms, request: Request) -> JSONResponse:
    """``maverick license issue`` (download the result or install it) and
    ``maverick license set`` (``install: true``: sign and install in one step)."""
    _mutation(request)
    terms = _terms(body)
    result = await run_in_threadpool(_sign, request, terms, body.install, body.confirm)
    return JSONResponse(result, headers=_NO_STORE)


# ---------------------------------------------------------------- install
def _parse(raw: bytes | str) -> dict:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        doc = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(422, _NOT_A_LICENSE) from None
    if not isinstance(doc, dict):
        raise HTTPException(422, _NOT_A_LICENSE)
    return doc


class IssuedLicense(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license: str = Field(min_length=2, max_length=MAX_LICENSE_BYTES)
    confirm: bool = False


def _install_issued(request: Request, text: str, confirm: bool) -> dict[str, Any]:
    """Install a license this server just signed, exactly as ``license issue``
    writes it. Only a document whose signature checks out against THIS
    server's signing key is accepted, so nothing else can reach the file."""
    from maverick import entitlements

    key = entitlements.publisher_private_key()
    if not key:
        raise HTTPException(503, _NO_SIGNING_KEY)
    try:
        own = entitlements.public_key_for(key)
    except Exception:  # noqa: BLE001 -- never surface the key or its error
        raise HTTPException(503, _UNUSABLE_KEY) from None
    doc = _parse(text)
    ok, _why = entitlements.verify_license(doc, [own])
    if not ok:
        raise HTTPException(422, "That license was not signed on this server, so it was not installed. "
                                 "Use Install a license file instead.")
    replacing = _refuse_unconfirmed_replace(confirm)
    trusted = entitlements.key_trusted(own)
    _record(request, "license_installed",
            "This could not be added to the activity record, so nothing was installed.",
            source="signed on this server", replaced=replacing, trusted=trusted, **_audit_terms(doc))
    _install(doc)
    return _result(doc, trusted=trusted, installed=True)


@router.post(API_PATH + "/install-issued")
async def install_issued(body: IssuedLicense, request: Request) -> JSONResponse:
    _mutation(request)
    result = await run_in_threadpool(_install_issued, request, body.license, body.confirm)
    return JSONResponse(result, headers=_NO_STORE)


def _rollback_note(doc: dict) -> str:
    from maverick.entitlements import _rollback_reason

    reason = _rollback_reason(_license_path(), doc) or ""
    if "different customer" in reason:
        return "This license is for a different customer than the one installed now."
    if "older" in reason:
        return "This license is older than the one installed now."
    return ""


def _install_file(request: Request, raw: bytes, confirm: bool, check_only: bool) -> dict[str, Any]:
    """Check an uploaded license against this server's trusted license keys and
    install it. Anything that does not check out is refused."""
    from maverick import entitlements

    doc = _parse(raw)
    ok, why = entitlements.verify_license(doc, entitlements._trusted_pubkeys())
    if not ok:
        raise HTTPException(422, _VERIFY_REFUSALS.get(why, _NOT_A_LICENSE))
    resolved = entitlements.resolve(doc)
    if resolved.status not in _CHECK_SENTENCES:
        raise HTTPException(422, "The end date in that license could not be read, so nothing was installed.")
    preview = {
        "license": _doc_summary(doc),
        "status": resolved.status,
        "status_sentence": _CHECK_SENTENCES[resolved.status],
        "replaces": _license_path().exists(),
        "note": _rollback_note(doc),
    }
    if check_only:
        return {"installed": False, **preview}
    replacing = _refuse_unconfirmed_replace(confirm)
    _record(request, "license_installed",
            "This could not be added to the activity record, so nothing was installed.",
            source="uploaded file", replaced=replacing, trusted=True, **_audit_terms(doc))
    _install(doc)
    return {"installed": True, **preview}


@router.post(API_PATH + "/install-file")
async def install_file(
    request: Request,
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    check_only: bool = Form(False),
) -> JSONResponse:
    """``check_only`` checks the file and says what it grants without writing."""
    _mutation(request)
    try:
        raw = await file.read(MAX_LICENSE_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > MAX_LICENSE_BYTES:
        raise HTTPException(413, "That file is too large to be a license file, so nothing was installed.")
    result = await run_in_threadpool(_install_file, request, raw, confirm, check_only)
    return JSONResponse(result, headers=_NO_STORE)


# ---------------------------------------------------------------- key pair
def _keygen(request: Request) -> dict[str, Any]:
    from maverick.entitlements import new_keypair

    try:
        private_key, public_key = new_keypair()
    except Exception:  # noqa: BLE001 -- e.g. the signing library is missing
        raise HTTPException(503, "This server cannot make a key pair right now. Nothing was made.") from None
    _record(request, "license_signing_key_generated",
            "This could not be added to the activity record, so no key pair was made.",
            public_key_fingerprint=_fingerprint(public_key))
    # Shown once: this response is the only place the private key ever exists
    # outside the kernel call. Nothing here stores, logs or records it.
    return {
        "private_key": private_key,
        "public_key": public_key,
        "fingerprint": _fingerprint(public_key),
        "store_as": "MAVERICK_LICENSE_PRIVATE_KEY",
        "trusted_keys_name": "MAVERICK_LICENSE_PUBKEYS",
    }


@router.post(API_PATH + "/keygen")
async def make_key_pair(request: Request) -> JSONResponse:
    _mutation(request)
    result = await run_in_threadpool(_keygen, request)
    return JSONResponse(result, headers=_NO_STORE)


# ---------------------------------------------------------------- read + page
@router.get(API_PATH)
async def license_status(request: Request) -> JSONResponse:
    _admin(request)
    return JSONResponse(await run_in_threadpool(license_state), headers=_NO_STORE)


@router.get("/static/lightwork-license.js", include_in_schema=False)
async def license_script() -> FileResponse:
    return FileResponse(_STATIC / "lightwork-license.js",
                        media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})


@router.get(PAGE_PATH, response_class=HTMLResponse)
async def license_page(request: Request) -> HTMLResponse:
    _admin(request)
    state = await run_in_threadpool(license_state)
    return _app().templates.TemplateResponse(request, "settings_license.html", {"state": state},
                                             headers=_NO_STORE)


__all__ = ["router", "license_state", "PAGE_PATH", "API_PATH", "MAX_LICENSE_BYTES"]
