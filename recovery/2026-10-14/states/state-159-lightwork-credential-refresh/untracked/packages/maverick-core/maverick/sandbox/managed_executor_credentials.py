"""Opt-in AWS executor credential refresh, without restarting the application.

Unverified JWT claims constrain lifetime and pin identity, not authentication.
The pinned Kubernetes TLS server still verifies the token signature and binding.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass

from ..file_lock import (
    atomic_read_text,
    atomic_write_text,
    cross_process_lock,
    ensure_private_directory,
)
from ..paths import maverick_home

_ARN_ENV = "MAVERICK_EXECUTOR_KUBECONFIG_SECRET_ARN"
_INITIAL_ENV = "MAVERICK_EXECUTOR_KUBECONFIG_SECRET"
_ERROR = "Managed executor credential unavailable; execution is paused"
_CACHE_SECONDS = 30
_SA = "lightwork-controller"
_SLOTS = {f"lightwork-prod-controller-binding-{slot}" for slot in "abc"}


class CredentialUnavailable(RuntimeError):
    """A redacted, fail-closed credential refresh error."""


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise ValueError("invalid document")
    return json.loads(raw, object_pairs_hook=_object_pairs)


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid shape")
    return value


def _single(value, field):
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("invalid singleton")
    entry = _exact(value[0], {"name", field})
    if not isinstance(entry["name"], str) or not entry["name"]:
        raise ValueError("invalid name")
    return entry


def _claims(token):
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError("invalid token")
    parts = token.split(".")
    if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
        raise ValueError("invalid token")
    header, claims = [_json(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)).decode()) for p in parts[:2]]
    if not isinstance(header, dict) or header.get("alg") not in {"RS256", "ES256"}:
        raise ValueError("invalid token algorithm")
    if not isinstance(claims, dict):
        raise ValueError("invalid claims")
    for key in ("iat", "exp", "nbf"):
        if type(claims.get(key)) is not int:
            raise ValueError("invalid token time")
    if not 0 < claims["exp"] - claims["iat"] <= 86460 or claims["nbf"] > claims["iat"]:
        raise ValueError("invalid lifetime")
    audience = claims.get("aud")
    if not isinstance(audience, list) or not audience or any(not isinstance(a, str) or not a for a in audience):
        raise ValueError("invalid audience")
    if len(set(audience)) != len(audience) or not isinstance(claims.get("iss"), str) or not claims["iss"].startswith("https://"):
        raise ValueError("invalid issuer/audience")
    return claims


def _service_identity(claims, namespace):
    expected = f"system:serviceaccount:{namespace}:{_SA}"
    if claims.get("sub") != expected:
        raise ValueError("wrong service account")
    identity = _exact(claims.get("kubernetes.io"), {"namespace", "serviceaccount", "secret"})
    if identity.get("namespace") != namespace:
        raise ValueError("invalid Kubernetes identity")
    account = _exact(identity.get("serviceaccount"), {"name", "uid"})
    secret = _exact(identity.get("secret"), {"name", "uid"})
    if account["name"] != _SA or secret["name"] not in _SLOTS:
        raise ValueError("wrong token binding")
    for uid in (account["uid"], secret["uid"]):
        if not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", uid):
            raise ValueError("invalid UID")
    return (expected, account["uid"], claims["iss"], tuple(sorted(claims["aud"])))


def _document(raw, namespace):
    config = _exact(_json(raw), {"apiVersion", "kind", "current-context", "contexts", "clusters", "users"})
    if config["kind"] != "Config" or config["apiVersion"] != "v1":
        raise ValueError("invalid kubeconfig")
    context_entry, cluster_entry, user_entry = [_single(config[key], field) for key, field in (
        ("contexts", "context"), ("clusters", "cluster"), ("users", "user"),
    )]
    context = _exact(context_entry["context"], {"cluster", "user", "namespace"})
    if context_entry["name"] != config["current-context"] or context["namespace"] != namespace:
        raise ValueError("wrong context")
    if context["cluster"] != cluster_entry["name"] or context["user"] != user_entry["name"]:
        raise ValueError("wrong identity references")
    user = _exact(user_entry["user"], {"token"})
    from .kubernetes_authority import _validate_identity
    _validate_identity(user, cluster_entry["cluster"])
    claims = _claims(user["token"])
    identity = _service_identity(claims, namespace)
    pin = json.loads(json.dumps(config))
    pin["users"][0]["user"] = {"identity": identity}
    return config, claims, json.dumps(pin, sort_keys=True), hashlib.sha256(user["token"].encode()).hexdigest()


def _client(arn):
    import boto3
    from botocore.config import Config

    region = arn.split(":")[3]
    # Explicit endpoint and CA verification defeat AWS_ENDPOINT_URL/CA overrides.
    return boto3.client(
        "secretsmanager", region_name=region,
        endpoint_url=f"https://secretsmanager.{region}.amazonaws.com", verify=True,
        config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1},
                      proxies={}, ignore_configured_endpoint_urls=True),
    )


@dataclass
class _Cached:
    checked: float
    version: str
    iat: int
    exp: int
    digest: str
    encoded: str


_CACHE: dict[str, _Cached] = {}
_LOCK = threading.Lock()


def enabled():
    return bool(os.environ.get(_ARN_ENV))


def _settings(namespace):
    arn = os.environ.get(_ARN_ENV, "")
    if not re.fullmatch(r"arn:aws:secretsmanager:us-east-2:589997396906:secret:lightwork-prod/executor-kubeconfig-[A-Za-z0-9]{6}", arn):
        raise ValueError("invalid secret ARN")
    if os.environ.get("MAVERICK_DEPLOYMENT_ISOLATION") != "kubernetes" or namespace != "lightwork-execution":
        raise ValueError("managed mode requires production pin")
    if os.environ.get("MAVERICK_EXECUTOR_NAMESPACE", "lightwork-execution") != namespace:
        raise ValueError("namespace mismatch")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", os.environ.get("MAVERICK_EXECUTOR_IMAGE", "")):
        raise ValueError("unpinned image")
    initial = _document(os.environ.get(_INITIAL_ENV, ""), namespace)
    return arn, initial


def _fetch(arn, initial, namespace, remaining, state):
    response = _client(arn).get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")
    if response.get("ARN") != arn or "AWSCURRENT" not in response.get("VersionStages", []):
        raise ValueError("wrong returned secret")
    version = response.get("VersionId", "")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", version):
        raise ValueError("invalid version")
    config, claims, pin, digest = _document(response.get("SecretString"), namespace)
    if pin != initial[2] or claims["iat"] < initial[1]["iat"]:
        raise ValueError("identity changed or initial rollback")
    now = time.time()
    if claims["iat"] > now + 60 or claims["nbf"] > now + 60 or claims["exp"] < now + remaining + 60:
        raise ValueError("insufficient credential lifetime")
    if state:
        if claims["iat"] < state["iat"] or claims["exp"] < state["exp"]:
            raise ValueError("version rollback")
        if claims["iat"] == state["iat"] and (version != state["version"] or digest != state["digest"]):
            raise ValueError("ambiguous version")
        if version == state["version"] and digest != state["digest"]:
            raise ValueError("version contents changed")
    return _Cached(time.monotonic(), version, claims["iat"], claims["exp"], digest,
                   json.dumps(config, sort_keys=True))


def _read_state(path):
    if not path.exists():
        return None
    state = _exact(_json(atomic_read_text(path)), {"iat", "exp", "version", "digest"})
    if type(state["iat"]) is not int or type(state["exp"]) is not int:
        raise ValueError("corrupt high-water state")
    if (not isinstance(state["version"], str) or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", state["version"])
            or not isinstance(state["digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", state["digest"])
            or not 0 < state["exp"] - state["iat"] <= 86460):
        raise ValueError("corrupt high-water state")
    return state


def managed_path(namespace, remaining_seconds=0):
    """Return a validated private path, or fail without any stale-token fallback."""
    try:
        if not isinstance(remaining_seconds, (int, float)) or not math.isfinite(remaining_seconds) or remaining_seconds < 0:
            raise ValueError("invalid operation budget")
        arn, initial = _settings(namespace)
        key = hashlib.sha256((arn + initial[2]).encode()).hexdigest()
        directory = ensure_private_directory(maverick_home() / "sandbox-authority" / "managed")
        path, state_path = directory / (key + ".json"), directory / (key + ".state")
        with _LOCK, cross_process_lock(directory / (key + ".lock"), strict=True):
            state = _read_state(state_path)
            cached = _CACHE.get(key)
            now = time.time()
            if not (cached and state and cached.version == state["version"]
                    and 0 <= time.monotonic() - cached.checked < _CACHE_SECONDS
                    and cached.iat <= now + 60
                    and cached.exp >= now + remaining_seconds + 60):
                _CACHE.pop(key, None)
                cached = _fetch(arn, initial, namespace, remaining_seconds, state)
                # Persist high-water before credential publication; crashes fail closed.
                atomic_write_text(state_path, json.dumps({
                    "iat": cached.iat, "exp": cached.exp,
                    "version": cached.version, "digest": cached.digest,
                }))
                _CACHE[key] = cached
            # Re-publish verified bytes rather than trust a mutable credential file.
            atomic_write_text(path, cached.encoded)
            if cached.exp < time.time() + remaining_seconds + 60:
                raise ValueError("credential expired during publication")
            return str(path)
    except Exception:
        raise CredentialUnavailable(_ERROR) from None


def backend_path(backend, remaining_seconds):
    """Refuse caller overrides that could redirect an otherwise pinned credential."""
    if (backend.context is not None or backend.extra_kubectl_args
            or backend.allow_network or backend.run_as_user != 1000
            or backend.image != os.environ.get("MAVERICK_EXECUTOR_IMAGE")):
        raise CredentialUnavailable(_ERROR)
    return managed_path(backend.namespace, remaining_seconds)
