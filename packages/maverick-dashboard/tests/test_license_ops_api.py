"""Settings > License: the in-app form of ``maverick license show / set / issue /
keygen``. Global admin only; the signing key is read only from the server's
secret store and never shown, recorded or returned; a new key pair is shown
once; uploaded files are checked against the trusted license keys; replacing an
installed license needs confirmation; each change writes one activity row."""
from __future__ import annotations

import html as _html
import json
import re

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import static_url

ORIGIN = {"Origin": "http://127.0.0.1"}
API = "/api/v1/admin/license"
PAGE = "/settings/license"

BANNED = (
    "MAVERICK_", "config.toml", "[license]", "maverick ", "tenant", "principal", "payload",
    "endpoint", "schema", "UUID", "SHA-256", "digest", "idempotency", "revision", "lease",
    "HTTP ", "license.json", "publisher_pubkeys", "key_id", "_features", "external_agents",
)


def visible_text(page: str) -> str:
    main = page.split('<main id="content"', 1)[-1].split("</main>", 1)[0]
    main = re.sub(r"<(script|style)\b.*?</\1>", " ", main, flags=re.DOTALL)
    main = re.sub(r"<[^>]+>", " ", main)
    return _html.unescape(re.sub(r"\s+", " ", main))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mvhome"))
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_LICENSE_PRIVATE_KEY", "MAVERICK_LICENSE_PUBKEYS",
                 "MAVERICK_LICENSE_FILE", "MAVERICK_LICENSE_ENFORCE", "MAVERICK_LICENSE_API",
                 "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    from maverick import config, entitlements
    config.reset_config_cache()
    entitlements.reset_cache()
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, base_url="http://127.0.0.1", headers=ORIGIN)
    config.reset_config_cache()
    entitlements.reset_cache()


@pytest.fixture
def audit_rows(monkeypatch):
    import maverick.audit as audit

    rows: list[dict] = []

    def capture(kind, **payload):
        rows.append({"kind": kind, **payload})
        return True

    monkeypatch.setattr(audit, "audit_event", capture)
    return rows


def _keypair():
    from maverick.entitlements import new_keypair
    return new_keypair()


@pytest.fixture
def signing(monkeypatch):
    """A publisher signing key on this server, trusted by it."""
    priv, pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    monkeypatch.setenv("MAVERICK_LICENSE_PUBKEYS", pub)
    return priv, pub


def _as(monkeypatch, role: str, who: str) -> None:
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def _license_path():
    from maverick.entitlements import _default_license_path
    return _default_license_path()


def _terms(**extra):
    return {"customer": "Acme Bank", "tier": "gold", "suites": ["fleet"],
            "features": ["advanced_evolve"], "expires": "2031-01-31", "grace_days": 10,
            "seats": 25, **extra}


def _signed(priv: str, **changes) -> dict:
    from maverick.entitlements import build_license_payload, sign_license
    return sign_license(build_license_payload(**{"customer": "Acme Bank", "tier": "gold", **changes}), priv)


def _upload(client, doc_or_bytes, **form):
    raw = doc_or_bytes if isinstance(doc_or_bytes, bytes) else json.dumps(doc_or_bytes).encode()
    data = {k: "true" if v else "false" for k, v in form.items()}
    return client.post(API + "/install-file", files={"file": ("license.json", raw, "application/json")},
                       data=data)


def _all_files_text(root) -> str:
    chunks = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                chunks.append(path.read_text("utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(chunks)


# ---- page -----------------------------------------------------------------

def test_page_offers_every_control_in_plain_language(client, signing):
    page = client.get(PAGE)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    text = visible_text(page.text)
    for label in ("Your license", "Install a license file", "Check the file",
                  "Change what this installation is licensed for", "Sign and install",
                  "Issue a license for a customer", "Sign the license", "Make a signing key pair",
                  "Back to Settings", "About this page's safeguards"):
        assert label in text, label
    # Choices come from the kernel's real registries.
    assert 'id="license-issue-form"' in page.text and 'id="license-set-form"' in page.text
    for label in ("Agent fleet", "Finance", "Outside agents", "Advanced self-improvement", "Platinum"):
        assert label in text, label
    assert 'type="date"' in page.text and 'name="seats"' in page.text
    for word in BANNED:
        assert word not in text, word
    assert signing[0] not in page.text
    assert f'<script src="{static_url("lightwork-license.js")}" defer></script>' in page.text
    script = client.get("/static/lightwork-license.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")


def test_no_signing_key_on_this_server_renders_without_the_forms(client):
    page = client.get(PAGE).text
    assert 'id="license-issue-form"' not in page
    assert 'id="license-set-form"' not in page
    text = visible_text(page)
    assert "This server cannot sign licenses because no signing key is set up on it." in text
    # Installing a file you were sent and making a key pair stay available.
    assert 'id="license-file-form"' in page and 'id="license-keygen"' in page
    for word in BANNED:
        assert word not in text, word
    r = client.post(API + "/sign", json=_terms())
    assert r.status_code == 503
    assert r.json()["detail"].startswith("This server cannot sign licenses")


def test_unusable_signing_key_is_explained_and_never_echoed(client, monkeypatch, audit_rows):
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", "not-a-real-key-zz")  # pragma: allowlist secret
    page = client.get(PAGE).text
    assert 'id="license-issue-form"' not in page
    assert "cannot be used" in visible_text(page)
    r = client.post(API + "/sign", json=_terms())
    assert r.status_code == 503
    assert "not-a-real-key" not in r.text
    assert audit_rows == []


def test_settings_links_to_the_license_page(client):
    assert 'href="/settings/license"' in client.get("/settings").text


# ---- permissions ----------------------------------------------------------

@pytest.mark.parametrize("role", ["viewer", "operator", "auditor"])
def test_non_admins_are_refused(client, monkeypatch, signing, audit_rows, role):
    _as(monkeypatch, role, f"user:{role}")
    assert client.get(PAGE).status_code == 403
    assert client.get(API).status_code == 403
    assert client.post(API + "/keygen").status_code == 403
    assert client.post(API + "/sign", json=_terms()).status_code == 403
    assert client.post(API + "/install-issued", json={"license": "{}"}).status_code == 403
    assert _upload(client, _signed(signing[0])).status_code == 403
    assert not _license_path().exists()
    assert not [row for row in audit_rows if str(row.get("verb", "")).startswith("license")]


def test_cross_site_changes_are_refused(client, signing, audit_rows):
    evil = {"Origin": "https://evil.example"}
    assert client.post(API + "/keygen", headers=evil).status_code == 403
    assert client.post(API + "/sign", json=_terms(install=True), headers=evil).status_code == 403
    assert not _license_path().exists()
    assert audit_rows == []


# ---- keygen ---------------------------------------------------------------

def test_key_pair_is_shown_once_and_never_returned_or_kept(client, tmp_path, audit_rows):
    r = client.post(API + "/keygen")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    pair = r.json()
    priv, pub = pair["private_key"], pair["public_key"]
    from maverick.entitlements import public_key_for
    assert public_key_for(priv) == pub
    assert pair["fingerprint"] == pub[:16]
    assert pair["store_as"] == "MAVERICK_LICENSE_PRIVATE_KEY"
    # Nothing on the server ever gives it back...
    for path in (PAGE, API, "/settings", "/api/v1/admin/system/license"):
        assert priv not in client.get(path).text, path
    # ...or kept it anywhere on disk (settings, overlays, logs, license file).
    assert priv not in _all_files_text(tmp_path)
    # A second press makes a different pair; the first is gone for good.
    assert client.post(API + "/keygen").json()["private_key"] != priv
    rows = [row for row in audit_rows if row.get("verb") == "license_signing_key_generated"]
    assert len(rows) == 2
    assert rows[0]["public_key_fingerprint"] == pub[:16]
    assert priv not in repr(audit_rows) and pub not in repr(audit_rows)


def test_refused_activity_record_releases_no_key(client, monkeypatch):
    import maverick.audit as audit
    from maverick.audit import AuditRefused

    def refuse(kind, **payload):
        raise AuditRefused("sealed")

    monkeypatch.setattr(audit, "audit_event", refuse)
    r = client.post(API + "/keygen")
    assert r.status_code == 503
    assert "private_key" not in r.text


# ---- issue ----------------------------------------------------------------

def test_issue_signs_a_verifiable_license_without_installing_it(client, signing, audit_rows):
    priv, pub = signing
    r = client.post(API + "/sign", json=_terms())
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["installed"] is False and body["trusted"] is True and body["warning"] == ""
    assert not _license_path().exists()
    doc = json.loads(body["file"]["text"])
    from maverick.entitlements import verify_license
    assert verify_license(doc, [pub]) == (True, "ok")
    assert doc["customer"] == "Acme Bank" and doc["tier"] == "gold" and doc["suites"] == ["fleet"]
    assert doc["features"] == ["advanced_evolve"] and doc["seats"] == 25 and doc["grace_days"] == 10
    assert doc["expires_at"].startswith("2031-01-31")
    assert body["file"]["name"] == "lightwork-license-acme-bank.json"
    summary = body["license"]
    assert summary == {"customer": "Acme Bank", "plan": "Gold", "add_ons": ["Agent fleet"],
                       "features": ["Advanced self-improvement"], "expires": "2031-01-31", "seats": 25}
    assert priv not in r.text
    rows = [row for row in audit_rows if row.get("verb") == "license_issued"]
    assert len(rows) == 1 and len(audit_rows) == 1
    assert rows[0]["installed"] is False and rows[0]["customer"] == "Acme Bank"
    assert rows[0]["signing_key_fingerprint"] == pub[:16]
    assert priv not in repr(audit_rows) and pub not in repr(audit_rows)


def test_issue_with_an_untrusted_signing_key_warns_like_the_command(client, monkeypatch):
    priv, _pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    page = client.get(PAGE).text
    assert 'id="license-set-untrusted"' in page
    body = client.post(API + "/sign", json=_terms()).json()
    assert body["trusted"] is False
    assert "does not trust the key that signed this license" in body["warning"]


def test_install_issued_writes_the_deployment_license_like_the_command(client, signing, audit_rows):
    _priv, pub = signing
    signed = client.post(API + "/sign", json=_terms()).json()
    r = client.post(API + "/install-issued", json={"license": signed["file"]["text"]})
    assert r.status_code == 200, r.text
    assert r.json()["installed"] is True
    from maverick.entitlements import current, license_text, load_entitlements
    doc = json.loads(signed["file"]["text"])
    assert _license_path().read_text("utf-8") == license_text(doc)
    ent = load_entitlements(trusted_pubkeys=[pub])
    assert ent.status == "licensed" and ent.suite_enabled("fleet")
    assert current().customer == "Acme Bank"  # the running server picks it up
    rows = [row for row in audit_rows if row.get("verb") == "license_installed"]
    assert len(rows) == 1 and rows[0]["source"] == "signed on this server"
    # The page now shows it, in plain words.
    summary = client.get(API).json()["summary"]
    assert summary["status_label"] == "Active" and summary["customer"] == "Acme Bank"


def test_install_issued_refuses_a_license_signed_elsewhere(client, signing, audit_rows):
    other_priv, _ = _keypair()
    doc = _signed(other_priv)
    r = client.post(API + "/install-issued", json={"license": json.dumps(doc)})
    assert r.status_code == 422
    assert "not signed on this server" in r.json()["detail"]
    assert not _license_path().exists()
    assert audit_rows == []


# ---- set ------------------------------------------------------------------

def test_set_signs_and_installs_and_asks_before_replacing(client, signing, audit_rows):
    _priv, pub = signing
    r = client.post(API + "/sign", json=_terms(install=True))
    assert r.status_code == 200, r.text
    assert r.json()["installed"] is True
    first = _license_path().read_text("utf-8")
    # The change form now starts from the installed license.
    current = client.get(API).json()["current"]
    assert current["customer"] == "Acme Bank" and current["suites"] == ["fleet"]
    assert current["expires"] == "2031-01-31" and current["seats"] == 25
    # Replacing it needs an explicit confirmation; nothing changes without it.
    r = client.post(API + "/sign", json=_terms(tier="platinum", install=True))
    assert r.status_code == 409
    assert r.json()["detail"] == "A license is already installed. Confirm that you want to replace it."
    assert _license_path().read_text("utf-8") == first
    r = client.post(API + "/sign", json=_terms(tier="platinum", install=True, confirm=True))
    assert r.status_code == 200
    from maverick.entitlements import load_entitlements
    assert load_entitlements(trusted_pubkeys=[pub]).tier == "platinum"
    rows = [row for row in audit_rows if row.get("verb") == "license_set"]
    assert [row["replaced"] for row in rows] == [False, True]
    assert len(audit_rows) == 2


@pytest.mark.parametrize("change", [
    {"customer": "x" * 201},
    {"customer": "   "},
    {"suites": ["fleet"] * 101},
    {"suites": ["../etc"]},
    {"features": ["Not A Feature"]},
    {"tier": "diamond"},
    {"grace_days": -1},
    {"seats": 0},
    {"expires": "31/01/2031"},
    {"edition": "<script>"},
    {"unexpected": True},
])
def test_bounded_license_details_are_refused(client, signing, audit_rows, change):
    r = client.post(API + "/sign", json=_terms(install=True, **change))
    assert r.status_code == 422, change
    assert not _license_path().exists()
    assert audit_rows == []


# ---- install a license file -------------------------------------------------

def test_uploaded_file_is_checked_first_then_installed(client, signing, audit_rows):
    priv, pub = signing
    doc = _signed(priv, suites=["fleet"], expires="2031-01-31")
    r = _upload(client, doc, check_only=True)
    assert r.status_code == 200, r.text
    check = r.json()
    assert check["installed"] is False and check["replaces"] is False
    assert check["status_sentence"] == "This license checks out and is in date."
    assert check["license"]["add_ons"] == ["Agent fleet"]
    assert not _license_path().exists() and audit_rows == []
    r = _upload(client, doc)
    assert r.status_code == 200 and r.json()["installed"] is True
    from maverick.entitlements import load_entitlements
    assert load_entitlements(trusted_pubkeys=[pub]).suite_enabled("fleet")
    # Replacing needs confirmation, and the check says what would change.
    newer = _signed(priv)
    check = _upload(client, _signed(priv, customer="Other Co"), check_only=True).json()
    assert check["replaces"] is True
    assert check["note"] == "This license is for a different customer than the one installed now."
    assert _upload(client, newer).status_code == 409
    assert _upload(client, newer, confirm=True).status_code == 200
    rows = [row for row in audit_rows if row.get("verb") == "license_installed"]
    assert [row["source"] for row in rows] == ["uploaded file", "uploaded file"]
    assert [row["replaced"] for row in rows] == [False, True]
    assert priv not in repr(audit_rows) and pub not in repr(audit_rows)


def test_unsigned_or_tampered_license_uploads_are_refused(client, signing, audit_rows):
    priv, _pub = signing
    good = _signed(priv)
    unsigned = {k: v for k, v in good.items() if k not in ("sig", "key_id")}
    tampered = {**good, "tier": "platinum"}
    stranger_priv, _ = _keypair()
    untrusted = _signed(stranger_priv)
    cases = {
        "unsigned": (unsigned, "That license is not signed, so nothing was installed."),
        "tampered": (tampered, "That license was changed after it was signed, so nothing was installed."),
        "untrusted": (untrusted, "That license was signed with a key this server does not trust, so nothing was installed."),
        "not json": (b"not a license", "That file is not a Lightwork license, so nothing was installed."),
        "not an object": (b"[1, 2]", "That file is not a Lightwork license, so nothing was installed."),
    }
    for name, (payload, sentence) in cases.items():
        for confirm in (False, True):
            r = _upload(client, payload, confirm=confirm)
            assert r.status_code == 422, name
            assert r.json()["detail"] == sentence, name
    r = _upload(client, b" " * (64 * 1024 + 1))
    assert r.status_code == 413
    assert not _license_path().exists()
    assert audit_rows == []


def test_upload_is_refused_when_the_server_trusts_no_key(client, monkeypatch, audit_rows):
    priv, _pub = _keypair()
    r = _upload(client, _signed(priv))
    assert r.status_code == 422
    assert "no trusted license keys" in r.json()["detail"]
    assert "cannot confirm that a license is genuine" in visible_text(client.get(PAGE).text)
    assert not _license_path().exists() and audit_rows == []


def test_refused_activity_record_installs_nothing(client, signing, monkeypatch):
    import maverick.audit as audit
    from maverick.audit import AuditRefused

    def refuse(kind, **payload):
        raise AuditRefused("sealed")

    monkeypatch.setattr(audit, "audit_event", refuse)
    r = client.post(API + "/sign", json=_terms(install=True))
    assert r.status_code == 503
    assert r.json()["detail"] == "This could not be added to the activity record, so nothing was signed."
    assert _upload(client, _signed(signing[0])).status_code == 503
    assert not _license_path().exists()


def test_page_state_never_carries_the_signing_key(client, signing):
    priv, pub = signing
    state = client.get(API)
    assert state.status_code == 200
    body = state.json()
    assert body["signing"] == {"state": "ready", "fingerprint": pub[:16], "trusted": True}
    assert priv not in state.text
    from maverick.entitlements import DEFAULT_GRACE_DAYS, GATED_FEATURES
    assert body["default_grace_days"] == DEFAULT_GRACE_DAYS
    assert {f["value"] for f in body["features"]} == set(GATED_FEATURES)
    assert body["suites"][0] == {"value": "fleet", "label": "Agent fleet", "paid": True}
