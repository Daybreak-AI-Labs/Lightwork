"""Executable acceptance pilot for the authenticated external runtime gateway.

The pilot drives the existing ``/api/v1/external/runtime/evaluate`` and
governed ``/execute`` APIs.  It is intentionally an operator tool, not a new
policy authority: the target deployment must already have an enrolled external
agent, a current Model Risk asset binding, runtime policy enabled, and a
governed connector selected.

Evidence is closed to request/response digests, bounded decision metadata and
opaque references.  Bearers, prompt/output text, connector bodies, connector
responses, hostnames and approval/execution identifiers are never emitted.
The client retries only transient, side-effect-free calls and write *parking*
requests carrying an exact idempotency key.  A commit transport failure is
recovered by polling its durable execution ledger; an executing,
indeterminate, or failed effect is never automatically re-fired.

Run with ``python -m maverick.external_gateway_runtime_pilot --help``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

EVIDENCE_SCHEMA = "lightwork.external-runtime-policy-pilot.v1"
READ_RESULT_DIGEST_CONTRACT = "lightwork.typed-canonical-json-result.v1"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_EXECUTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ASSET_RE = re.compile(r"MAA-[0-9a-f]{32}\Z")
_DIGEST_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z", re.IGNORECASE)


class RuntimePolicyPilotError(RuntimeError):
    """Base error whose messages exclude request and response content."""


class PilotConfigError(RuntimePolicyPilotError, ValueError):
    """The target, scenario or credential reference is malformed."""


class PilotProtocolError(RuntimePolicyPilotError):
    """The gateway returned a definitive malformed or rejected response."""


class PilotUnavailable(RuntimePolicyPilotError):
    """A side-effect-free/idempotent call exhausted its safe retries."""


class PilotAmbiguousEffect(RuntimePolicyPilotError):
    """A write commit may have reached the gateway; do not blindly retry."""


class InjectedPilotOutage(RuntimePolicyPilotError):
    """Deterministic transport interruption used by the acceptance harness."""


@dataclass(frozen=True)
class PilotHTTPResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class PilotHTTPTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> PilotHTTPResponse: ...


def _canonical(value: object, *, maximum: int = MAX_REQUEST_BYTES) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PilotConfigError("pilot payload must be finite JSON data") from exc
    if len(encoded) > maximum:
        raise PilotConfigError("pilot payload exceeds its byte bound")
    return encoded


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object) -> str:
    return _sha256_bytes(_canonical(value, maximum=MAX_RESPONSE_BYTES))


def _json_result_kind(value: object) -> str:
    """Validate one exact JSON value and return its top-level JSON kind."""

    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        if not math.isfinite(value):
            raise PilotProtocolError("gateway read result contains a non-finite number")
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        for item in value:
            _json_result_kind(item)
        return "array"
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise PilotProtocolError(
                    "gateway read result contains a non-string object key"
                )
            _json_result_kind(item)
        return "object"
    raise PilotProtocolError("gateway read result is not strict JSON data")


def read_result_digest(value: object) -> str:
    """Hash a strict JSON read result with an explicit top-level type tag."""

    try:
        kind = _json_result_kind(value)
        return _sha256({
            "contract": READ_RESULT_DIGEST_CONTRACT,
            "type": kind,
            "value": value,
        })
    except (PilotConfigError, RecursionError) as exc:
        raise PilotProtocolError(
            "gateway read result violates the canonical JSON contract"
        ) from exc


def _gateway_read_result_digest(response: Mapping[str, Any]) -> str:
    if "result" not in response:
        raise PilotProtocolError("gateway read response omits its result")
    return read_result_digest(response["result"])


def _request_fingerprint(method: str, path: str, body: bytes | None) -> str:
    return _sha256({
        "method": method.upper(),
        "path": path,
        "body_sha256": _sha256_bytes(body or b""),
    })


def _strict_json(value: bytes) -> dict[str, Any]:
    if len(value) > MAX_RESPONSE_BYTES:
        raise PilotProtocolError("gateway response exceeds its byte bound")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, item in pairs:
            if key in out:
                raise PilotProtocolError("gateway response contains duplicate keys")
            out[key] = item
        return out

    def reject_constant(_value: str) -> None:
        raise PilotProtocolError("gateway response contains a non-finite number")

    try:
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
    except PilotProtocolError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise PilotProtocolError("gateway response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise PilotProtocolError("gateway response must be a JSON object")
    return decoded


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _target_origin(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise PilotConfigError("gateway origin is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise PilotConfigError("gateway origin is invalid") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise PilotConfigError("gateway origin must be an exact HTTP(S) origin")
    if parsed.scheme.lower() == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise PilotConfigError("plaintext pilot transport is restricted to loopback")
    return value.rstrip("/")


class PinnedGatewayHTTPTransport:
    """One bounded, redirect-free HTTP request to an exact configured origin."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> PilotHTTPResponse:
        import httpx

        request_headers = {key: value for key, value in headers.items()
                           if key.lower() != "accept-encoding"}
        request_headers["Accept-Encoding"] = "identity"
        try:
            with httpx.Client(
                follow_redirects=False,
                timeout=timeout,
                trust_env=False,
            ) as client:
                from .governed_http import admit_client_request
                admit_client_request(client, url)
                with client.stream(
                    method,
                    url,
                    headers=request_headers,
                    content=body,
                ) as response:
                    # The protocol accepts identity bytes only. Reject a
                    # nonconforming server before HTTPX can decompress chunks.
                    encoding = response.headers.get("content-encoding", "")
                    if encoding.strip().lower() not in {"", "identity"}:
                        raise PilotProtocolError("gateway response encoding is unsupported")
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            raise PilotProtocolError(
                                "gateway Content-Length is invalid"
                            ) from None
                        if declared_size < 0 or declared_size > max_response_bytes:
                            raise PilotProtocolError(
                                "gateway response exceeds its byte bound"
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    for chunk in response.iter_bytes():
                        observed += len(chunk)
                        if observed > max_response_bytes:
                            raise PilotProtocolError(
                                "gateway response exceeds its byte bound"
                            )
                        chunks.append(chunk)
                    if declared is not None and int(declared) != observed:
                        raise PilotProtocolError(
                            "gateway Content-Length does not match its bounded body"
                        )
                    selected = {
                        key: val
                        for key, val in response.headers.items()
                        if key.lower()
                        in {
                            "content-length",
                            "content-type",
                            "content-encoding",
                            "location",
                            "retry-after",
                        }
                    }
                    return PilotHTTPResponse(
                        status_code=int(response.status_code),
                        headers=selected,
                        body=b"".join(chunks),
                    )
        except PilotProtocolError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise RuntimePolicyPilotError("gateway transport failed") from None


@dataclass(frozen=True)
class PilotFaultRule:
    """One deterministic outage, before or after a matching request dispatch."""

    name: str
    method: str
    path_match: str
    match_mode: str = "exact"
    occurrence: int = 1
    timing: str = "before"

    def __post_init__(self) -> None:
        if not _TOKEN_RE.fullmatch(self.name):
            raise PilotConfigError("fault name must be a bounded identifier")
        if self.method.upper() not in {"GET", "POST"}:
            raise PilotConfigError("fault method is unsupported")
        if self.match_mode not in {"exact", "prefix", "suffix"}:
            raise PilotConfigError("fault match mode is unsupported")
        if self.timing not in {"before", "after"}:
            raise PilotConfigError("fault timing is unsupported")
        if not isinstance(self.occurrence, int) or self.occurrence < 1:
            raise PilotConfigError("fault occurrence must be positive")
        if not self.path_match.startswith("/"):
            raise PilotConfigError("fault path must be absolute")


class FaultInjectingPilotTransport:
    """Transport wrapper for reproducible safe-retry and ambiguity drills."""

    def __init__(
        self,
        inner: PilotHTTPTransport,
        rules: tuple[PilotFaultRule, ...] | list[PilotFaultRule],
    ) -> None:
        self._inner = inner
        self._rules = tuple(rules)
        self._matches = [0 for _ in self._rules]
        self.triggered: list[str] = []

    @staticmethod
    def _matches_path(rule: PilotFaultRule, path: str) -> bool:
        if rule.match_mode == "prefix":
            return path.startswith(rule.path_match)
        if rule.match_mode == "suffix":
            return path.endswith(rule.path_match)
        return path == rule.path_match

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_response_bytes: int,
    ) -> PilotHTTPResponse:
        path = urlsplit(url).path
        selected: PilotFaultRule | None = None
        for index, rule in enumerate(self._rules):
            if method.upper() != rule.method.upper() or not self._matches_path(rule, path):
                continue
            self._matches[index] += 1
            if self._matches[index] == rule.occurrence:
                selected = rule
                break
        if selected is not None and selected.timing == "before":
            self.triggered.append(selected.name)
            raise InjectedPilotOutage("injected outage before dispatch")
        response = self._inner.request(
            method,
            url,
            headers=headers,
            body=body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        if selected is not None:
            self.triggered.append(selected.name)
            raise InjectedPilotOutage("injected outage after dispatch")
        return response


@dataclass(frozen=True)
class PilotCall:
    operation: str
    value: dict[str, Any] = field(repr=False)
    request_sha256: str
    response_sha256: str
    attempts: int
    http_status: int
    ambiguity_recovered: bool = False
    recovery_status: str = ""


class ExternalGatewayPilotClient:
    """Bounded authenticated client with operation-aware retry semantics."""

    def __init__(
        self,
        *,
        origin: str,
        bearer_token: str,
        transport: PilotHTTPTransport | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._origin = _target_origin(origin)
        if (
            not isinstance(bearer_token, str)
            or not bearer_token
            or len(bearer_token) > 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in bearer_token)
        ):
            raise PilotConfigError("gateway bearer is invalid")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(
            float(timeout_seconds)
        ) or not 0.1 <= float(timeout_seconds) <= 120:
            raise PilotConfigError("timeout_seconds is outside its bound")
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise PilotConfigError("max_attempts is outside its bound")
        self._bearer = bearer_token
        self._transport = transport or PinnedGatewayHTTPTransport()
        self._timeout = float(timeout_seconds)
        self._max_attempts = max_attempts
        self._sleep = sleeper

    @property
    def target_ref(self) -> str:
        return "GW-" + hashlib.sha256(self._origin.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _response_json(
        response: PilotHTTPResponse,
        *,
        operation: str,
        commit: bool,
    ) -> dict[str, Any]:
        try:
            if 300 <= response.status_code < 400:
                raise PilotProtocolError("gateway redirect was rejected")
            if not 200 <= response.status_code < 300:
                raise PilotProtocolError(
                    f"gateway definitively rejected {operation} with HTTP "
                    f"{response.status_code}"
                )
            content_type = _header(response.headers, "content-type")
            if (
                not isinstance(content_type, str)
                or content_type.split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise PilotProtocolError("gateway response must be application/json")
            encoding = _header(response.headers, "content-encoding")
            if encoding is not None and encoding.strip().lower() not in {
                "",
                "identity",
            }:
                raise PilotProtocolError("gateway response encoding is unsupported")
            return _strict_json(response.body)
        except PilotProtocolError:
            if commit and 200 <= response.status_code < 400:
                raise PilotAmbiguousEffect(
                    "commit response is malformed; inspect execution status"
                ) from None
            raise

    def _call(
        self,
        operation: str,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        retry_mode: str,
    ) -> PilotCall:
        if retry_mode not in {"safe", "idempotent", "commit"}:
            raise PilotConfigError("pilot retry mode is invalid")
        if not path.startswith("/api/v1/external/"):
            raise PilotConfigError("pilot path is outside the external gateway")
        body = None if payload is None else _canonical(dict(payload))
        request_digest = _request_fingerprint(method, path, body)
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {self._bearer}",
        }
        if body is not None:
            headers.update({
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            })
        attempts = 0
        while True:
            attempts += 1
            try:
                response = self._transport.request(
                    method,
                    self._origin + path,
                    headers=headers,
                    body=body,
                    timeout=self._timeout,
                    max_response_bytes=MAX_RESPONSE_BYTES,
                )
            except PilotProtocolError:
                if retry_mode == "commit":
                    raise PilotAmbiguousEffect(
                        "commit response is malformed; inspect execution status"
                    ) from None
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                if retry_mode in {"safe", "idempotent"} and attempts < self._max_attempts:
                    self._sleep(min(0.25 * (2 ** (attempts - 1)), 2.0))
                    continue
                if retry_mode == "commit":
                    raise PilotAmbiguousEffect(
                        "commit response is ambiguous; inspect execution status"
                    ) from None
                raise PilotUnavailable("gateway call exhausted safe retries") from None
            if response.status_code in _RETRYABLE_STATUS:
                if retry_mode in {"safe", "idempotent"} and attempts < self._max_attempts:
                    self._sleep(min(0.25 * (2 ** (attempts - 1)), 2.0))
                    continue
                if retry_mode == "commit":
                    raise PilotAmbiguousEffect(
                        "commit response is ambiguous; inspect execution status"
                    )
                raise PilotUnavailable("gateway call exhausted safe retries")
            value = self._response_json(
                response,
                operation=operation,
                commit=retry_mode == "commit",
            )
            return PilotCall(
                operation=operation,
                value=value,
                request_sha256=request_digest,
                response_sha256=_sha256_bytes(response.body),
                attempts=attempts,
                http_status=response.status_code,
            )

    def evaluate(
        self,
        *,
        stage: str,
        content: str,
        asset_id: str,
        asset_revision: int,
        asset_version_digest: str,
        action_name: str | None = None,
        action_arguments: Mapping[str, Any] | None = None,
    ) -> PilotCall:
        payload: dict[str, Any] = {
            "stage": stage,
            "content": content,
            "asset_id": asset_id,
            "asset_revision": asset_revision,
            "asset_version_digest": asset_version_digest,
        }
        if action_name is not None:
            payload["action_name"] = action_name
        if action_arguments is not None:
            payload["action_arguments"] = dict(action_arguments)
        return self._call(
            "runtime_evaluate",
            "POST",
            "/api/v1/external/runtime/evaluate",
            payload,
            retry_mode="safe",
        )

    def execute(self, request: Mapping[str, Any]) -> PilotCall:
        payload = dict(request)
        op = str(payload.get("op") or "").lower()
        safe = op == "get" or payload.get("preview") is True
        retry_mode = "safe" if safe else "idempotent"
        if not safe and not _TOKEN_RE.fullmatch(str(payload.get("idempotency_key") or "")):
            raise PilotConfigError(
                "write parking requires a bounded idempotency_key"
            )
        return self._call(
            "execute_safe" if safe else "execute_park",
            "POST",
            "/api/v1/external/execute",
            payload,
            retry_mode=retry_mode,
        )

    def execution_status(self, execution_id: str) -> PilotCall:
        if not _EXECUTION_ID_RE.fullmatch(str(execution_id)):
            raise PilotConfigError("execution id is invalid")
        return self._call(
            "execution_status",
            "GET",
            f"/api/v1/external/executions/{execution_id}",
            None,
            retry_mode="safe",
        )

    def commit(
        self,
        execution_id: str,
        request: Mapping[str, Any],
    ) -> PilotCall:
        if not _EXECUTION_ID_RE.fullmatch(str(execution_id)):
            raise PilotConfigError("execution id is invalid")
        payload = {
            key: request.get(key)
            for key in (
                "connector",
                "op",
                "path",
                "body",
                "detail",
                "goal_id",
                "runtime_asset_id",
                "runtime_asset_revision",
                "runtime_asset_version_digest",
            )
            if key in request
        }
        path = f"/api/v1/external/executions/{execution_id}/commit"
        commit_body = _canonical(payload)
        commit_request_digest = _request_fingerprint("POST", path, commit_body)
        try:
            return self._call(
                "execute_commit",
                "POST",
                path,
                payload,
                retry_mode="commit",
            )
        except PilotAmbiguousEffect:
            try:
                status_call = self.execution_status(execution_id)
            except RuntimePolicyPilotError:
                raise PilotAmbiguousEffect(
                    "commit and execution status are unavailable; verify in the system of record"
                ) from None
            status = str(status_call.value.get("status") or "unknown")
            approval = str(status_call.value.get("approval_status") or "")
            if status == "pending" and approval == "approved":
                try:
                    retry = self._call(
                        "execute_commit_recovery",
                        "POST",
                        path,
                        payload,
                        retry_mode="commit",
                    )
                except PilotAmbiguousEffect:
                    try:
                        final = self.execution_status(execution_id)
                    except RuntimePolicyPilotError:
                        raise PilotAmbiguousEffect(
                            "commit recovery is ambiguous; verify in the system of record"
                        ) from None
                    final_status = str(final.value.get("status") or "unknown")
                    return PilotCall(
                        operation="execute_commit_recovery",
                        value=final.value,
                        request_sha256=commit_request_digest,
                        response_sha256=final.response_sha256,
                        attempts=2 + status_call.attempts + final.attempts,
                        http_status=final.http_status,
                        ambiguity_recovered=True,
                        recovery_status=final_status,
                    )
                return PilotCall(
                    operation=retry.operation,
                    value=retry.value,
                    request_sha256=retry.request_sha256,
                    response_sha256=retry.response_sha256,
                    attempts=1 + status_call.attempts + retry.attempts,
                    http_status=retry.http_status,
                    ambiguity_recovered=True,
                    recovery_status=str(retry.value.get("status") or "unknown"),
                )
            return PilotCall(
                operation="execute_commit_recovery",
                value=status_call.value,
                request_sha256=commit_request_digest,
                response_sha256=status_call.response_sha256,
                attempts=1 + status_call.attempts,
                http_status=status_call.http_status,
                ambiguity_recovered=True,
                recovery_status=status,
            )


@dataclass(frozen=True)
class RuntimePolicyPilotScenario:
    asset_id: str
    asset_revision: int
    asset_version_digest: str
    allow_probe: str = field(repr=False)
    deny_probe: str = field(repr=False)
    read_request: Mapping[str, Any] = field(repr=False)
    change_request: Mapping[str, Any] = field(repr=False)
    compensation_request: Mapping[str, Any] = field(repr=False)
    operation_prefix: str = "runtime-pilot"

    def __post_init__(self) -> None:
        if not _ASSET_RE.fullmatch(self.asset_id):
            raise PilotConfigError("scenario asset_id is invalid")
        if (
            isinstance(self.asset_revision, bool)
            or not isinstance(self.asset_revision, int)
            or self.asset_revision < 1
        ):
            raise PilotConfigError("scenario asset_revision is invalid")
        if not _DIGEST_RE.fullmatch(self.asset_version_digest):
            raise PilotConfigError("scenario asset digest is invalid")
        if not isinstance(self.allow_probe, str) or not isinstance(self.deny_probe, str):
            raise PilotConfigError("scenario probes must be text")
        if len(self.allow_probe.encode("utf-8")) > 64 * 1024 or len(
            self.deny_probe.encode("utf-8")
        ) > 64 * 1024:
            raise PilotConfigError("scenario probe exceeds its byte bound")
        if (
            not _TOKEN_RE.fullmatch(self.operation_prefix)
            or len(self.operation_prefix) > 110
        ):
            raise PilotConfigError("operation_prefix is invalid")
        for label, request in (
            ("read_request", self.read_request),
            ("change_request", self.change_request),
            ("compensation_request", self.compensation_request),
        ):
            if not isinstance(request, Mapping):
                raise PilotConfigError(f"{label} must be an object")
            unknown = set(request) - {"connector", "op", "path", "body"}
            if unknown:
                raise PilotConfigError(f"{label} contains unsupported fields")
            _canonical(dict(request))
        if str(self.read_request.get("op") or "").lower() != "get":
            raise PilotConfigError("read_request must use get")
        for label, request in (
            ("change_request", self.change_request),
            ("compensation_request", self.compensation_request),
        ):
            if str(request.get("op") or "").lower() not in {
                "post",
                "put",
                "patch",
                "delete",
            }:
                raise PilotConfigError(f"{label} must be a write")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RuntimePolicyPilotScenario:
        if not isinstance(value, Mapping):
            raise PilotConfigError("scenario document must be an object")
        expected = {
            "asset_id",
            "asset_revision",
            "asset_version_digest",
            "allow_probe",
            "deny_probe",
            "read_request",
            "change_request",
            "compensation_request",
            "operation_prefix",
        }
        if set(value) - expected:
            raise PilotConfigError("scenario document contains unsupported fields")
        try:
            return cls(
                asset_id=value["asset_id"],
                asset_revision=value["asset_revision"],
                asset_version_digest=value["asset_version_digest"],
                allow_probe=value["allow_probe"],
                deny_probe=value["deny_probe"],
                read_request=value["read_request"],
                change_request=value["change_request"],
                compensation_request=value["compensation_request"],
                operation_prefix=value.get("operation_prefix", "runtime-pilot"),
            )
        except KeyError as exc:
            raise PilotConfigError("scenario document is incomplete") from exc


ApprovalCallback = Callable[[int, str], bool]


class RuntimePolicyAcceptancePilot:
    """Run a change/compensation acceptance cycle and return safe evidence."""

    def __init__(
        self,
        client: ExternalGatewayPilotClient,
        *,
        approval_callback: ApprovalCallback | None = None,
        approval_wait_seconds: float = 0.0,
        poll_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 <= float(approval_wait_seconds) <= 3600:
            raise PilotConfigError("approval_wait_seconds is outside its bound")
        if not 0.05 <= float(poll_seconds) <= 30:
            raise PilotConfigError("poll_seconds is outside its bound")
        self._client = client
        self._approve = approval_callback
        self._approval_wait = float(approval_wait_seconds)
        self._poll = float(poll_seconds)
        self._sleep = sleeper

    @staticmethod
    def _ref(value: object, prefix: str) -> str:
        return prefix + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _call_case(
        name: str,
        call: PilotCall,
        verdict: str,
        *,
        effect: str,
        observed: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_observed = dict(observed or {})
        return {
            "name": name,
            "verdict": verdict,
            "effect": effect,
            "attempts": call.attempts,
            "http_status": call.http_status,
            "request_sha256": call.request_sha256,
            "response_sha256": call.response_sha256,
            "ambiguity_recovered": call.ambiguity_recovered,
            "recovery_status": call.recovery_status or None,
            "observed": safe_observed,
        }

    @staticmethod
    def _error_case(name: str, error: Exception, *, effect: str) -> dict[str, Any]:
        category = type(error).__name__
        verdict = "indeterminate" if isinstance(error, PilotAmbiguousEffect) else "failed"
        return {
            "name": name,
            "verdict": verdict,
            "effect": effect,
            "error_category": category,
        }

    def _runtime_fields(self, scenario: RuntimePolicyPilotScenario) -> dict[str, Any]:
        return {
            "runtime_asset_id": scenario.asset_id,
            "runtime_asset_revision": scenario.asset_revision,
            "runtime_asset_version_digest": scenario.asset_version_digest.lower(),
        }

    def _read(self, scenario: RuntimePolicyPilotScenario) -> PilotCall:
        return self._client.execute({
            **dict(scenario.read_request),
            **self._runtime_fields(scenario),
            "detail": scenario.allow_probe,
        })

    def _wait_for_approval(
        self,
        execution_id: str,
        approval_id: int,
        label: str,
    ) -> tuple[str, PilotCall]:
        if self._approve is not None:
            try:
                self._approve(approval_id, label)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                raise PilotUnavailable("approval callback failed") from None
        deadline = time.monotonic() + self._approval_wait
        while True:
            status = self._client.execution_status(execution_id)
            decision = str(status.value.get("approval_status") or "")
            if decision in {"approved", "denied"}:
                return decision, status
            if time.monotonic() >= deadline:
                return decision or "pending", status
            self._sleep(self._poll)

    def _finish_report(
        self,
        scenario: RuntimePolicyPilotScenario,
        *,
        started: float,
        pilot_material: Mapping[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        counts = {
            name: sum(1 for item in events if item.get("verdict") == name)
            for name in ("passed", "failed", "pending", "indeterminate")
        }
        change_executed = any(
            item.get("name") == "change_commit" and item.get("verdict") == "passed"
            for item in events
        )
        baseline_restored = any(
            item.get("name") == "compensation_restores_baseline"
            and item.get("verdict") == "passed"
            for item in events
        )
        return {
            "schema": EVIDENCE_SCHEMA,
            "pilot_id": "RPP-" + _sha256(pilot_material)[:24],
            "target_ref": self._client.target_ref,
            "asset_ref": self._ref(scenario.asset_id, "ASSET-"),
            "asset_revision": scenario.asset_revision,
            "asset_version_sha256": scenario.asset_version_digest.lower().removeprefix(
                "sha256:"
            ),
            "started_at": started,
            "completed_at": time.time(),
            "read_result_digest_contract": READ_RESULT_DIGEST_CONTRACT,
            "events": events,
            "summary": counts,
            "residual_effect_possible": change_executed and not baseline_restored,
            "coverage": {
                "safe_retry_exercised": any(
                    item.get("attempts", 0) > 1 and item.get("effect") == "none"
                    for item in events
                ),
                "ambiguous_commit_recovered": any(
                    item.get("ambiguity_recovered") is True for item in events
                ),
                "exact_park_idempotency_exercised": any(
                    item.get("name") == "write_park_exact_replay"
                    and item.get("verdict") == "passed"
                    for item in events
                ),
                "compensation_exercised": any(
                    item.get("name") == "compensation_restores_baseline"
                    and item.get("verdict") == "passed"
                    for item in events
                ),
            },
            "limits": [
                "Evidence contains digests and bounded verdict metadata, not raw probes, bodies, responses, credentials, hostnames or opaque execution identifiers.",
                "State equality uses a type-preserving canonical JSON digest of the configured read probe; unsupported, non-JSON and non-finite results are rejected, and equality does not prove estate-wide semantic completeness.",
                    "A connector-reported failed or indeterminate commit may have produced an effect and is never automatically retried.",
                    "Write-park idempotency is retained for the governed execution ledger TTL, not forever.",
                    "The JSON pilot report is machine-readable but is not itself a signed certification artifact; correlate it with the governed audit ledger.",
                    "Human approval and compensation acceptance in this pilot do not establish production, customer or regulator acceptance.",
            ],
        }

    def _run_no_change_checks(
        self,
        scenario: RuntimePolicyPilotScenario,
        events: list[dict[str, Any]],
    ) -> str | None:
        """Exercise allow, deny, preview and enforced-denial with no effects."""

        try:
            allow = self._client.evaluate(
                stage="prompt",
                content=scenario.allow_probe,
                asset_id=scenario.asset_id,
                asset_revision=scenario.asset_revision,
                asset_version_digest=scenario.asset_version_digest.lower(),
            )
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("runtime_allow", exc, effect="none"))
            return None
        allow_decision = str(allow.value.get("decision") or "")
        events.append(self._call_case(
            "runtime_allow",
            allow,
            "passed" if allow_decision == "allow" else "failed",
            effect="none",
            observed={"decision": allow_decision},
        ))
        if allow_decision != "allow":
            return None

        try:
            deny = self._client.evaluate(
                stage="prompt",
                content=scenario.deny_probe,
                asset_id=scenario.asset_id,
                asset_revision=scenario.asset_revision,
                asset_version_digest=scenario.asset_version_digest.lower(),
            )
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("runtime_deny", exc, effect="none"))
            return None
        deny_decision = str(deny.value.get("decision") or "")
        events.append(self._call_case(
            "runtime_deny",
            deny,
            "passed" if deny_decision != "allow" else "failed",
            effect="none",
            observed={"decision": deny_decision},
        ))
        if deny_decision == "allow":
            return None

        try:
            baseline = self._read(scenario)
            baseline_digest = _gateway_read_result_digest(baseline.value)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("baseline_read", exc, effect="none"))
            return None
        if baseline.value.get("allowed") is not True or baseline.value.get("status") != "executed":
            events.append(self._call_case(
                "baseline_read", baseline, "failed", effect="none",
                observed={
                    "allowed": baseline.value.get("allowed") is True,
                    "rule": str(baseline.value.get("rule") or ""),
                    "status": str(baseline.value.get("status") or ""),
                },
            ))
            return None
        events.append(self._call_case(
            "baseline_read", baseline, "passed", effect="none",
            observed={"result_sha256": baseline_digest},
        ))

        preview_request = {
            **dict(scenario.change_request),
            **self._runtime_fields(scenario),
            "detail": scenario.allow_probe,
            "preview": True,
        }
        try:
            preview = self._client.execute(preview_request)
            after_preview = self._read(scenario)
            preview_digest = _gateway_read_result_digest(after_preview.value)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("preview_no_change", exc, effect="none"))
            return None
        preview_ok = (
            preview.value.get("rule") == "preview"
            and preview.value.get("allowed") is True
            and preview_digest == baseline_digest
        )
        events.append(self._call_case(
            "preview_no_change",
            preview,
            "passed" if preview_ok else "failed",
            effect="none",
            observed={
                "rule": str(preview.value.get("rule") or ""),
                "baseline_sha256": baseline_digest,
                "after_sha256": preview_digest,
            },
        ))
        if not preview_ok:
            return None

        blocked_request = {
            **dict(scenario.change_request),
            **self._runtime_fields(scenario),
            "detail": scenario.deny_probe,
            "idempotency_key": f"{scenario.operation_prefix}-blocked",
        }
        try:
            blocked = self._client.execute(blocked_request)
            after_blocked = self._read(scenario)
            blocked_digest = _gateway_read_result_digest(after_blocked.value)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("runtime_block_no_change", exc, effect="none"))
            return None
        blocked_ok = (
            blocked.value.get("allowed") is False
            and blocked.value.get("rule") == "runtime_guardrail"
            and blocked_digest == baseline_digest
        )
        events.append(self._call_case(
            "runtime_block_no_change",
            blocked,
            "passed" if blocked_ok else "failed",
            effect="none",
            observed={
                "rule": str(blocked.value.get("rule") or ""),
                "decision": str(
                    (blocked.value.get("runtime_guardrail") or {}).get("decision")
                    or ""
                ),
                "baseline_sha256": baseline_digest,
                "after_sha256": blocked_digest,
            },
        ))
        return baseline_digest if blocked_ok else None

    def run(self, scenario: RuntimePolicyPilotScenario) -> dict[str, Any]:
        started = time.time()
        pilot_material = {
            "target": self._client.target_ref,
            "asset": scenario.asset_id,
            "revision": scenario.asset_revision,
            "operation_prefix": scenario.operation_prefix,
            "started_at": started,
        }
        events: list[dict[str, Any]] = []
        finish = lambda: self._finish_report(  # noqa: E731 - local early-return helper
            scenario,
            started=started,
            pilot_material=pilot_material,
            events=events,
        )

        baseline_digest = self._run_no_change_checks(scenario, events)
        if baseline_digest is None:
            return finish()

        change_request = {
            **dict(scenario.change_request),
            **self._runtime_fields(scenario),
            "detail": scenario.allow_probe,
            "idempotency_key": f"{scenario.operation_prefix}-change",
        }
        try:
            parked = self._client.execute(change_request)
            replay = self._client.execute(change_request)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("write_park_exact_replay", exc, effect="none"))
            return finish()
        execution_id = str(parked.value.get("execution_id") or "")
        replay_ok = (
            parked.value.get("rule") == "approval_required"
            and execution_id
            and replay.value.get("execution_id") == execution_id
            and replay.value.get("duplicate") is True
        )
        events.append(self._call_case(
            "write_park_exact_replay",
            replay,
            "passed" if replay_ok else "failed",
            effect="none",
            observed={
                "rule": str(replay.value.get("rule") or ""),
                "duplicate": replay.value.get("duplicate") is True,
                "same_execution": replay.value.get("execution_id") == execution_id,
                "execution_ref": self._ref(execution_id, "EXE-") if execution_id else None,
            },
        ))
        if not replay_ok:
            return finish()
        approval_id = parked.value.get("approval_id") or replay.value.get("approval_id")
        if not isinstance(approval_id, int):
            events.append({
                "name": "change_approval",
                "verdict": "pending",
                "effect": "none",
                "observed": {"approval_created": False},
            })
            return finish()
        try:
            decision, approval_status = self._wait_for_approval(
                execution_id, approval_id, "change"
            )
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("change_approval", exc, effect="none"))
            return finish()
        events.append(self._call_case(
            "change_approval",
            approval_status,
            "passed" if decision == "approved" else "pending" if decision == "pending" else "failed",
            effect="none",
            observed={
                "decision": decision,
                "approval_ref": self._ref(approval_id, "APR-"),
            },
        ))
        if decision != "approved":
            return finish()

        try:
            committed = self._client.commit(execution_id, change_request)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("change_commit", exc, effect="indeterminate"))
            return finish()
        committed_status = str(
            committed.value.get("status") or committed.recovery_status or ""
        )
        commit_verdict = (
            "passed" if committed_status == "executed"
            else "indeterminate" if committed_status in {"failed", "executing", "indeterminate"}
            else "failed"
        )
        events.append(self._call_case(
            "change_commit",
            committed,
            commit_verdict,
            effect="one" if committed_status == "executed" else "indeterminate",
            observed={"status": committed_status},
        ))
        if commit_verdict != "passed":
            return finish()
        replay_commit = self._client.commit(execution_id, change_request)
        commit_replay_ok = (
            replay_commit.value.get("rule") == "already_finished"
            and replay_commit.value.get("status") == "executed"
        )
        events.append(self._call_case(
            "commit_exact_replay_no_refire",
            replay_commit,
            "passed" if commit_replay_ok else "failed",
            effect="none",
            observed={
                "rule": str(replay_commit.value.get("rule") or ""),
                "status": str(replay_commit.value.get("status") or ""),
            },
        ))
        if not commit_replay_ok:
            return finish()
        try:
            changed = self._read(scenario)
            changed_digest = _gateway_read_result_digest(changed.value)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("change_observed", exc, effect="none"))
            return finish()
        changed_ok = changed_digest != baseline_digest
        events.append(self._call_case(
            "change_observed",
            changed,
            "passed" if changed_ok else "failed",
            effect="none",
            observed={
                "baseline_sha256": baseline_digest,
                "changed_sha256": changed_digest,
            },
        ))
        if not changed_ok:
            return finish()

        compensation_request = {
            **dict(scenario.compensation_request),
            **self._runtime_fields(scenario),
            "detail": scenario.allow_probe,
            "idempotency_key": f"{scenario.operation_prefix}-compensate",
        }
        try:
            compensation = self._client.execute(compensation_request)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case("compensation_park", exc, effect="none"))
            return finish()
        compensation_execution = str(compensation.value.get("execution_id") or "")
        compensation_approval = compensation.value.get("approval_id")
        park_ok = (
            compensation.value.get("rule") == "approval_required"
            and compensation_execution
            and isinstance(compensation_approval, int)
        )
        events.append(self._call_case(
            "compensation_park",
            compensation,
            "passed" if park_ok else "failed",
            effect="none",
            observed={
                "rule": str(compensation.value.get("rule") or ""),
                "execution_ref": self._ref(compensation_execution, "EXE-")
                if compensation_execution else None,
            },
        ))
        if not park_ok:
            return finish()
        try:
            comp_decision, comp_status = self._wait_for_approval(
                compensation_execution,
                int(compensation_approval),
                "compensation",
            )
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case(
                "compensation_approval", exc, effect="none"
            ))
            return finish()
        events.append(self._call_case(
            "compensation_approval",
            comp_status,
            "passed" if comp_decision == "approved" else "pending" if comp_decision == "pending" else "failed",
            effect="none",
            observed={
                "decision": comp_decision,
                "approval_ref": self._ref(compensation_approval, "APR-"),
            },
        ))
        if comp_decision != "approved":
            return finish()
        try:
            compensated = self._client.commit(
                compensation_execution,
                compensation_request,
            )
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case(
                "compensation_commit", exc, effect="indeterminate"
            ))
            return finish()
        compensation_status = str(
            compensated.value.get("status") or compensated.recovery_status or ""
        )
        events.append(self._call_case(
            "compensation_commit",
            compensated,
            "passed" if compensation_status == "executed" else "indeterminate",
            effect="compensation" if compensation_status == "executed" else "indeterminate",
            observed={"status": compensation_status},
        ))
        if compensation_status != "executed":
            return finish()
        try:
            final_read = self._read(scenario)
            final_digest = _gateway_read_result_digest(final_read.value)
        except RuntimePolicyPilotError as exc:
            events.append(self._error_case(
                "compensation_restores_baseline", exc, effect="indeterminate"
            ))
            return finish()
        events.append(self._call_case(
            "compensation_restores_baseline",
            final_read,
            "passed" if final_digest == baseline_digest else "failed",
            effect="none",
            observed={
                "baseline_sha256": baseline_digest,
                "final_sha256": final_digest,
            },
        ))
        return finish()


def _default_faults() -> tuple[PilotFaultRule, ...]:
    return (
        PilotFaultRule(
            name="safe-evaluate-outage",
            method="POST",
            path_match="/api/v1/external/runtime/evaluate",
            occurrence=1,
            timing="before",
        ),
        PilotFaultRule(
            name="commit-response-loss",
            method="POST",
            path_match="/commit",
            match_mode="suffix",
            occurrence=1,
            timing="after",
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the authenticated external-gateway runtime policy acceptance pilot. "
            "The bearer is read only from an environment variable."
        )
    )
    parser.add_argument("--origin", required=True, help="Exact Lightwork gateway origin")
    parser.add_argument("--token-env", required=True, help="Environment variable holding the external-agent bearer")
    parser.add_argument("--scenario", required=True, type=Path, help="Pilot scenario JSON")
    parser.add_argument("--evidence", type=Path, help="Write safe JSON evidence to this path")
    parser.add_argument("--approval-wait-seconds", type=float, default=0.0)
    parser.add_argument(
        "--inject-outages",
        action="store_true",
        help="Inject one safe pre-dispatch outage and one post-commit response loss",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        token = os.environ.get(args.token_env)
        if not token:
            raise PilotConfigError("the configured bearer environment variable is empty")
        try:
            scenario_raw = json.loads(args.scenario.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PilotConfigError("scenario file is unavailable or invalid") from exc
        scenario = RuntimePolicyPilotScenario.from_mapping(scenario_raw)
        transport: PilotHTTPTransport = PinnedGatewayHTTPTransport()
        fault_transport: FaultInjectingPilotTransport | None = None
        if args.inject_outages:
            fault_transport = FaultInjectingPilotTransport(transport, _default_faults())
            transport = fault_transport
        client = ExternalGatewayPilotClient(
            origin=args.origin,
            bearer_token=token,
            transport=transport,
        )
        report = RuntimePolicyAcceptancePilot(
            client,
            approval_wait_seconds=args.approval_wait_seconds,
        ).run(scenario)
        if fault_transport is not None:
            report["faults_triggered"] = sorted(fault_transport.triggered)
        rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if args.evidence:
            from .file_lock import atomic_write_text

            atomic_write_text(args.evidence, rendered)
        else:
            sys.stdout.write(rendered)
        summary = report["summary"]
        if summary["failed"] or summary["indeterminate"]:
            return 1
        if summary["pending"]:
            return 2
        return 0
    except RuntimePolicyPilotError as exc:
        sys.stderr.write(f"runtime policy pilot: {exc}\n")
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())


__all__ = [
    "EVIDENCE_SCHEMA",
    "READ_RESULT_DIGEST_CONTRACT",
    "ExternalGatewayPilotClient",
    "FaultInjectingPilotTransport",
    "InjectedPilotOutage",
    "PilotAmbiguousEffect",
    "PilotConfigError",
    "PilotFaultRule",
    "PilotHTTPResponse",
    "PilotHTTPTransport",
    "PilotProtocolError",
    "PilotUnavailable",
    "RuntimePolicyAcceptancePilot",
    "RuntimePolicyPilotError",
    "RuntimePolicyPilotScenario",
    "main",
    "read_result_digest",
]
