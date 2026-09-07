"""Renewable named connections with sealed app registrations and consent state.

The existing provider-wide OAuth vault remains a legacy automation contract.
These records belong to an exact named connection, owner, tenant and origin.
Refreshing access bytes preserves the reviewed connection generation; changing
credentials, app registration, sharing, provider or destination does not.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from . import config, connections, oauth_providers
from .file_lock import (
    atomic_read_bytes,
    atomic_write_bytes,
    ensure_private_directory,
    ensure_private_file,
)
from .paths import current_tenant_id_strict, data_dir
from .tenant.kms import seal_text_for_tenant, unseal_text_for_tenant

PROFILES = {
    "google-drive": {"label": "Google Drive evidence", "provider": "google", "connector": "gdrive", "origin": "https://www.googleapis.com", "scopes": ["https://www.googleapis.com/auth/drive.readonly"]},
    "google-cloud-storage": {"label": "Google Cloud Storage discovery", "provider": "google", "connector": "google_cloud_storage", "origin": "https://storage.googleapis.com", "scopes": ["https://www.googleapis.com/auth/devstorage.read_only"]},
    "jira-cloud": {"label": "Jira Cloud reviewed delivery", "provider": "atlassian", "connector": "jira", "origin": "https://api.atlassian.com", "scopes": ["read:jira-work", "write:jira-work", "offline_access"]},
}
_MAX_BYTES = 4 * 1024 * 1024


def enabled():
    override = config.env_flag("MAVERICK_OAUTH_VAULT")
    if override is None:
        override = config.env_flag("LIGHTWORK_OAUTH_VAULT")
    loaded = config.load_global_config()
    return connections.enabled() and not config.config_source_errors(include_tenant=True) and (override if override is not None else (loaded.get("oauth") or {}).get("vault") is True)


def _authority(actor):
    tenant = current_tenant_id_strict()
    if not tenant or not isinstance(actor, str) or not actor.startswith("user:") or not enabled():
        raise PermissionError("Named account authorization requires an enabled workspace connection vault.")
    return tenant


def _path():
    return data_dir("connections", "oauth-setup.sealed")


def _load():
    try:
        path = _path()
        ensure_private_file(path)
        if path.stat().st_size > _MAX_BYTES:
            raise ValueError("setup exceeds its bound")
        value = json.loads(unseal_text_for_tenant(current_tenant_id_strict(), atomic_read_bytes(path)))
        if not isinstance(value, dict) or set(value) != {"applications", "consents"} or any(not isinstance(value[k], dict) for k in value):
            raise ValueError("invalid setup")
        return value
    except FileNotFoundError:
        return {"applications": {}, "consents": {}}
    except Exception as exc:
        raise connections.ConnectionStoreUnavailable("Account authorization setup is unavailable.") from exc


def _save(value):
    raw = json.dumps(value, sort_keys=True)
    if len(raw.encode()) > _MAX_BYTES - 4096:
        raise ValueError("Account authorization setup capacity reached.")
    ensure_private_directory(_path().parent)
    atomic_write_bytes(_path(), seal_text_for_tenant(current_tenant_id_strict(), raw), mode=0o600)


def _app_key(actor, provider):
    return hashlib.sha256(json.dumps([actor, provider]).encode()).hexdigest()


def applications(actor):
    _authority(actor)
    value = _load()["applications"]
    return [{"provider": row["provider"], "client_id": row["client_id"], "revision": row["revision"], "has_secret": bool(row["client_secret"])} for row in value.values() if row.get("owner") == actor]


def save_application(actor, *, provider, client_id, client_secret, expected_revision):
    _authority(actor)
    if provider not in {"google", "atlassian"}:
        raise ValueError("Select a supported account provider.")
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 1024 or any(ord(c) < 33 for c in client_id):
        raise ValueError("Enter the provider's application client ID.")
    if client_secret is not None and (not isinstance(client_secret, str) or not 1 <= len(client_secret) <= 4096 or any(ord(c) < 32 for c in client_secret)):
        raise ValueError("Enter the application's client secret, or leave it unchanged.")
    with connections._mutation_lock():
        store = _load()
        key = _app_key(actor, provider)
        previous = store["applications"].get(key)
        if (previous or {}).get("revision", "") != expected_revision:
            raise connections.ConnectionVersionConflict("Application registration changed. Reload before saving.")
        secret = client_secret if client_secret is not None else (previous or {}).get("client_secret", "")
        if not secret:
            raise ValueError("This authorization flow requires the application's client secret.")
        if not previous and len(store["applications"]) >= 1000:
            raise ValueError("Application registration capacity reached.")
        store["applications"][key] = {"provider": provider, "owner": actor, "client_id": client_id,
            "client_secret": secret, "revision": uuid.uuid4().hex, "saved_at": time.time()}
        _save(store)
    return applications(actor)


def _network_allowed(url):
    from .enterprise import deployment_enterprise_enabled, enterprise_egress_denial
    loaded = config.load_global_config()
    if config.config_source_errors(include_tenant=True):
        raise PermissionError("Network policy is unavailable.")
    hosts = (loaded.get("enterprise") or {}).get("allowed_hosts") or []
    if deployment_enterprise_enabled() and urlsplit(url).hostname not in hosts:
        raise PermissionError("Deployment network policy blocks this authorization provider.")
    if enterprise_egress_denial(url, tool="connection_oauth"):
        raise PermissionError("Workspace network policy blocks this authorization provider.")


def _post_token(provider, payload):
    from .tools.oauth_helper import _post_form
    url = oauth_providers.get_provider(provider).token_url
    _network_allowed(url)
    # HTTPS, public-address pinning, no redirects, bounded response; the
    # surrounding application egress guard remains active as well.
    if provider != "atlassian":
        return _post_form(url, payload)
    from .tools._ssrf import safe_client
    chunks, size = [], 0
    with safe_client(url, timeout=30.0) as client:
        with client.stream("POST", url, json=payload, headers={"Accept": "application/json"}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 100000:
                    raise ValueError("Provider authorization response exceeds its supported bound.")
                chunks.append(chunk)
    return json.loads(b"".join(chunks))


def _jira_sites(token):
    from .tools._ssrf import safe_client
    url = "https://api.atlassian.com/oauth/token/accessible-resources"
    _network_allowed(url)
    chunks, size = [], 0
    with safe_client(url, timeout=30.0) as client:
        with client.stream("GET", url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 100000:
                    raise ValueError("Authorized site list exceeds its supported bound.")
                chunks.append(chunk)
    return json.loads(b"".join(chunks))


def _redirect_uri(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/connections"):
        raise ValueError("Authorization must return to this app's Connections page.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote account authorization requires HTTPS.")
    return value


def begin(actor, *, profile, name, redirect_uri, context, expected_revision="", site_url=""):
    _authority(actor)
    if profile not in PROFILES or not connections._NAME_RE.fullmatch(name):
        raise ValueError("Choose a supported account and connection name.")
    selected = PROFILES[profile]
    if profile == "jira-cloud":
        site = urlsplit(site_url)
        if (site.scheme != "https" or not site.hostname or not site.hostname.endswith(".atlassian.net")
                or site.username or site.password or site.port not in (None, 443)
                or site.path not in ("", "/") or site.query or site.fragment):
            raise ValueError("Enter the exact HTTPS address of the Jira Cloud site to authorize.")
        site_url = "https://" + site.hostname
    elif site_url:
        raise ValueError("A site address is needed only for Jira Cloud.")
    redirect_uri = _redirect_uri(redirect_uri)
    _network_allowed(oauth_providers.get_provider(selected["provider"]).token_url)
    with connections._mutation_lock():
        store = _load()
        application = store["applications"].get(_app_key(actor, selected["provider"]))
        if not application:
            raise ValueError("Save the provider's app registration below before authorizing.")
        current = connections.get_connection(name)
        if current and current.get("owner") != actor:
            raise PermissionError("This connection name is already owned by another account.")
        if (current or {}).get("revision", "") != expected_revision:
            raise connections.ConnectionVersionConflict("Connection changed. Reload before authorizing.")
        state = secrets.token_urlsafe(32)
        key = hashlib.sha256(state.encode()).hexdigest()
        built, verifier = oauth_providers.build_authorize_url(selected["provider"], client_id=application["client_id"], redirect_uri=redirect_uri, state=state, scopes=selected["scopes"])
        # Retain only bounded, current pending consent. Consumed requests cannot
        # be retried after an uncertain token exchange.
        now = time.time()
        store["consents"] = {k: v for k, v in store["consents"].items() if v["expires_at"] > now}
        if len(store["consents"]) >= 32:
            raise ValueError("Too many account authorizations are pending. Wait ten minutes.")
        store["consents"][key] = {"owner": actor, "profile": profile, "name": name, "context": context,
            "application_revision": application["revision"], "expected_revision": expected_revision,
            "redirect_uri": redirect_uri, "site_url": site_url, "verifier": verifier, "expires_at": now + 600}
        _save(store)
    return {"authorize_url": built, "state": state, "expires_in": 600}


def _tokens(response, scopes, *, prior_refresh=""):
    if not isinstance(response, dict):
        raise ValueError("Provider authorization response was incomplete.")
    if "token_type" in response and (not isinstance(response["token_type"], str) or response["token_type"].lower() != "bearer"):
        raise ValueError("Provider did not return a supported Bearer authorization.")
    access, refresh = response.get("access_token"), response.get("refresh_token") or prior_refresh
    if any(not isinstance(v, str) or not 1 <= len(v) <= 32768 or any(ord(c) < 32 for c in v) for v in (access, refresh)):
        raise ValueError("Provider did not supply renewable authorization. Reconnect with offline access.")
    seconds = response.get("expires_in")
    if isinstance(seconds, bool):
        raise ValueError("Provider authorization expiry was invalid.")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        raise ValueError("Provider authorization expiry was absent.") from None
    if not math.isfinite(seconds) or not 60 < seconds <= 366 * 86400:
        raise ValueError("Provider authorization expiry was outside supported limits.")
    returned = response.get("scope")
    if returned is not None and (not isinstance(returned, str) or not set(scopes) <= set(returned.split())):
        raise ValueError("Provider did not grant the requested access. Review consent and reconnect.")
    return access, refresh, time.time() + seconds


def finish(actor, *, state, code, context, redirect_uri):
    _authority(actor)
    if (not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", state)
            or not isinstance(code, str) or not 1 <= len(code) <= 8192 or any(ord(c) < 32 for c in code)):
        raise ValueError("Authorization response was invalid.")
    key = hashlib.sha256(state.encode()).hexdigest()
    with connections._mutation_lock():
        store = _load()
        consent = store["consents"].get(key)
        if (not consent or consent["owner"] != actor or consent["context"] != context
                or consent["redirect_uri"] != redirect_uri or consent["expires_at"] <= time.time()):
            raise PermissionError("Authorization expired or belongs to a different account or workspace. Start again.")
        selected = PROFILES[consent["profile"]]
        application = store["applications"].get(_app_key(actor, selected["provider"]))
        records = connections._load()
        previous = records.get(consent["name"])
        if (not application or application["revision"] != consent["application_revision"]
                or (previous or {}).get("revision", "") != consent["expected_revision"]
                or previous and previous.get("owner") != actor):
            raise connections.ConnectionVersionConflict("Connection or application changed during consent. Start again.")
        del store["consents"][key]
        _save(store)
        token_response = _post_token(selected["provider"], {"grant_type": "authorization_code", "client_id": application["client_id"], "client_secret": application["client_secret"], "code": code, "redirect_uri": redirect_uri, "code_verifier": consent["verifier"]})
        access, refresh, expires = _tokens(token_response, selected["scopes"])
        prefix = ""
        if consent["profile"] == "jira-cloud":
            sites = _jira_sites(access)
            if not isinstance(sites, list):
                raise ValueError("Authorized Jira sites were unavailable.")
            matches = [row for row in sites if isinstance(row, dict) and str(row.get("url", "")).rstrip("/") == consent["site_url"]]
            if len(matches) != 1 or not re.fullmatch(r"[0-9a-fA-F-]{36}", str(matches[0].get("id", ""))):
                raise ValueError("Consent did not authorize the selected Jira site. No connection was saved.")
            resource_scopes = matches[0].get("scopes")
            if (not isinstance(resource_scopes, list) or any(not isinstance(value, str) for value in resource_scopes)
                    or not (set(selected["scopes"]) - {"offline_access"}) <= set(resource_scopes)):
                raise ValueError("The selected Jira site did not grant the required read and write access.")
            prefix = "/ex/jira/" + str(uuid.UUID(matches[0]["id"]))
        records[consent["name"]] = {"name": consent["name"], "connector": selected["connector"], "base_url": selected["origin"], "token": access,
            "created": (previous or {}).get("created", time.time()), "owner": actor,
            "access": (previous or {}).get("access", "owner"), "allowed_principals": (previous or {}).get("allowed_principals", []),
            "revision": uuid.uuid4().hex,
            "oauth": {"profile": consent["profile"], "application_revision": application["revision"], "refresh_token": refresh,
                "expires_at": expires, "refresh_state": "ready", "api_prefix": prefix, "site_url": consent["site_url"]}}
        connections._save(records)
        return connections._public(records[consent["name"]])


def renew(record, *, actor):
    """Resolve refreshed bytes only after callers validated origin and grants."""
    if not record.get("oauth"):
        return record
    _authority(actor)
    with connections._mutation_lock():
        records = connections._load()
        current = records.get(record["name"])
        if (not current or current.get("revision") != record.get("revision")
                or not connections._may_use(current, actor)):
            raise connections.ConnectionVersionConflict("Connection authority changed. Reload before continuing.")
        oauth = current.get("oauth") or {}
        selected = PROFILES.get(oauth.get("profile"))
        if (not selected or current.get("connector") != selected["connector"] or current.get("base_url") != selected["origin"]):
            raise PermissionError("Renewable connection destination does not match its authorization.")
        application = _load()["applications"].get(_app_key(current["owner"], selected["provider"]))
        if not application or application["revision"] != oauth.get("application_revision"):
            raise connections.ConnectionVersionConflict("App registration changed. Reconnect this account in Connections.")
        if oauth.get("refresh_state") != "ready":
            raise PermissionError("Account renewal was interrupted. Reconnect in Connections before continuing.")
        if float(oauth.get("expires_at", 0)) > time.time() + 60:
            return dict(current)
        _network_allowed(oauth_providers.get_provider(selected["provider"]).token_url)
        # A crash/lost reply during rotating-token exchange is ambiguous. Mark
        # before sending and refuse automatic replay of the old refresh token.
        oauth["refresh_state"] = "pending"
        connections._save(records)
        try:
            result = _post_token(selected["provider"], {"grant_type": "refresh_token", "client_id": application["client_id"], "client_secret": application["client_secret"], "refresh_token": oauth["refresh_token"]})
            access, refresh, expires = _tokens(result, selected["scopes"], prior_refresh=oauth["refresh_token"])
            current["token"] = access
            oauth.update(refresh_token=refresh, expires_at=expires, refresh_state="ready")
            connections._save(records)
        except Exception as exc:
            raise PermissionError("Account renewal could not be confirmed. Reconnect in Connections before continuing.") from exc
        return dict(current)


def public_status(record):
    value = record.get("oauth")
    if not isinstance(value, dict):
        return None
    return {"profile": value.get("profile"), "expires_at": value.get("expires_at"),
        "renewal_state": value.get("refresh_state"), "site_url": value.get("site_url", "")}
