"""A governed pass-through LLM gateway for agents that never enroll.

The estate-coverage fork every guardian-agent buyer asks about: agents built
anywhere point their OpenAI-compatible base URL at Lightwork instead of the
provider, and every call crosses a governed checkpoint — its own bearer, a
model allowlist, bounded bodies, the platform's SSRF-pinned and
enterprise-egress-guarded upstream path — while a caller ledger turns
unknown traffic into shadow-AI discovery. Bodies are never stored: the
ledger keeps caller digests, models, counts, token usage, and content
digests only.

v1 scope, stated plainly: one fixed upstream, the ``/v1/chat/completions``
shape, no streaming (a ``stream`` request is refused with an explanation),
and no response rewriting. Interception is opt-in per estate: point the
agents at the gateway; nothing here can see traffic that never arrives.

Kernel rule 3 (budget caps) and this gateway, decided: proxied calls are
the CALLER's spend on the caller's own provider key, not a kernel agent
run, so they do not flow through :class:`maverick.budget.Budget` — there
is no goal, no role, and no platform dollar at stake. The governed limits
that do apply here are the gateway's own: bearer auth, the model
allowlist, bounded request/response bodies, and the caller ledger that
makes the spend visible. Routing a foreign agent's tokens through a
kernel Budget would cap someone else's bill with the platform's knobs.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from .governed_records import GovernedRecordStore, RecordConflict

log = logging.getLogger(__name__)

LEDGER_SCHEMA = "lightwork.llm-gateway-caller.v1"
_LEDGER = GovernedRecordStore("llm_gateway", "LGC", "llm_gateway_caller")
_MAX_CALLERS = 2_000
_MAX_LIST = 500
_ABS_MAX_BODY = 4 * 1024 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_UPSTREAM_TIMEOUT_SECONDS = 120.0


class LLMGatewayError(RuntimeError):
    """Base failure for the governed LLM gateway."""


class LLMGatewayConfigError(LLMGatewayError):
    """The gateway is not enabled or configured safely."""


def get_llm_gateway_config() -> dict[str, Any]:
    from .config import load_global_config

    try:
        config = load_global_config()
    except Exception as exc:
        raise LLMGatewayConfigError("llm_gateway configuration is unavailable") from exc
    section = (config or {}).get("llm_gateway") or {}
    if not isinstance(section, dict):
        raise LLMGatewayConfigError("llm_gateway must be a table")
    known = {
        "enable",
        "upstream_url",
        "upstream_token_env",
        "gateway_token_env",
        "allowed_models",
        "max_body_bytes",
        "screen_prompts",
    }
    unknown = set(section) - known
    if unknown:
        raise LLMGatewayConfigError(
            f"unknown llm_gateway keys: {', '.join(sorted(unknown))}"
        )
    flag = section.get("enable", False)
    if not isinstance(flag, bool):
        raise LLMGatewayConfigError("llm_gateway.enable must be a boolean")
    upstream = str(section.get("upstream_url") or "").strip().rstrip("/")
    if flag and not upstream.startswith("https://"):
        raise LLMGatewayConfigError("llm_gateway.upstream_url must be an https URL")
    upstream_token_env = str(
        section.get("upstream_token_env") or "LLM_GATEWAY_UPSTREAM_TOKEN"
    ).strip()
    gateway_token_env = str(
        section.get("gateway_token_env") or "MAVERICK_GATEWAY_TOKEN"
    ).strip()
    if not upstream_token_env or not gateway_token_env:
        raise LLMGatewayConfigError("llm_gateway token env names must be non-empty")
    models = section.get("allowed_models", [])
    if not isinstance(models, list) or not all(
        isinstance(model, str) and model.strip() for model in models
    ):
        raise LLMGatewayConfigError("llm_gateway.allowed_models must be a string list")
    max_body = section.get("max_body_bytes", 1024 * 1024)
    if (
        isinstance(max_body, bool)
        or not isinstance(max_body, int)
        or not 4096 <= max_body <= _ABS_MAX_BODY
    ):
        raise LLMGatewayConfigError(
            f"llm_gateway.max_body_bytes must be between 4096 and {_ABS_MAX_BODY}"
        )
    screen = section.get("screen_prompts", False)
    if not isinstance(screen, bool):
        raise LLMGatewayConfigError("llm_gateway.screen_prompts must be a boolean")
    return {
        "enable": flag,
        "upstream_url": upstream,
        "upstream_token_env": upstream_token_env,
        "gateway_token_env": gateway_token_env,
        "allowed_models": [model.strip() for model in models],
        "max_body_bytes": max_body,
        "screen_prompts": screen,
    }


def enabled() -> bool:
    try:
        return get_llm_gateway_config()["enable"] is True
    except Exception:
        return False


def _caller_digest(token: str) -> str:
    return hashlib.sha256(f"llm-gateway-caller:{token}".encode()).hexdigest()[:24]


def _authorized(caller_token: str, settings: Mapping[str, Any]) -> bool:
    expected = os.environ.get(settings["gateway_token_env"], "")
    return bool(
        expected
        and caller_token
        and hmac_mod.compare_digest(caller_token, expected)
    )


def verify_gateway_bearer(token: str) -> bool:
    """Whether ``token`` is the configured gateway bearer.

    The one public auth check for every gateway-credentialed surface, so
    the web layer never reaches into private helpers and a hardening
    change lands everywhere at once. False when the gateway is disabled
    or unconfigured.
    """

    try:
        settings = get_llm_gateway_config()
    except LLMGatewayConfigError:
        return False
    if settings["enable"] is not True:
        return False
    return _authorized(str(token or ""), settings)


def _default_upstream(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any],
) -> tuple[int, Any]:
    """POST through the SSRF-pinned, enterprise-egress-guarded client."""

    from .enterprise import enterprise_egress_denial
    from .tools._ssrf import BlockedHost, safe_client

    deny = enterprise_egress_denial(url, tool="llm_gateway")
    if deny:
        raise LLMGatewayError(deny)
    try:
        with safe_client(url, timeout=_UPSTREAM_TIMEOUT_SECONDS) as client:
            response = client.post(url, headers=dict(headers), json=dict(body))
    except BlockedHost as exc:
        raise LLMGatewayError("blocked host (SSRF guard)") from exc
    text = response.text or ""
    if len(text.encode("utf-8", "ignore")) > _MAX_RESPONSE_BYTES:
        raise LLMGatewayError("upstream response exceeds the gateway byte cap")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload


def _record_call(
    *,
    caller: str,
    model: str,
    status: str,
    prompt_sha256: str,
    usage: Mapping[str, Any] | None,
    now: float,
    requests: int = 1,
) -> None:
    """Aggregate call(s) into the caller ledger; never store bodies.

    ``requests`` lets a pre-aggregated batch (OTel span ingestion) land as
    one CAS write instead of one per span.
    """

    record_id = f"LGC-{hashlib.sha256(f'{caller}:{model}'.encode()).hexdigest()[:24]}"
    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    count = max(1, int(requests))

    def mutate(row: dict[str, Any]) -> None:
        row["request_count"] = int(row.get("request_count") or 0) + count
        row["last_seen"] = now
        row["last_status"] = status
        row["last_prompt_sha256"] = prompt_sha256
        row["prompt_tokens"] = int(row.get("prompt_tokens") or 0) + prompt_tokens
        row["completion_tokens"] = (
            int(row.get("completion_tokens") or 0) + completion_tokens
        )

    # Shadow accounting must NEVER fail the call it is accounting for: by
    # the time this runs the upstream completion may already have been paid
    # for, and a full or contended ledger is not the caller's problem. Every
    # store outcome short of success degrades to a logged, dropped row.
    for _ in range(3):
        current = _LEDGER.get(record_id)
        if current is None:
            record = {
                "id": record_id,
                "schema": LEDGER_SCHEMA,
                "caller_digest": caller,
                "model": model,
                "request_count": count,
                "first_seen": now,
                "last_seen": now,
                "last_status": status,
                "last_prompt_sha256": prompt_sha256,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
            try:
                _LEDGER.create(
                    record,
                    action="llm_gateway_call",
                    actor="gateway:llm",
                    max_records=_MAX_CALLERS,
                )
                return
            except RecordConflict:
                continue
            except Exception:
                # Capacity reached (each blocked_model refusal and each
                # OTel service/model pair mints a row, so an outsider can
                # force this) or a backend fault: drop the row, keep the
                # response.
                log.warning(
                    "llm_gateway: caller ledger write dropped (capacity or "
                    "backend fault); the call itself is unaffected"
                )
                return
        try:
            saved = _LEDGER.update(
                record_id,
                mutate,
                expected_revision=int(current.get("revision") or 0),
                action="llm_gateway_call",
                actor="gateway:llm",
            )
        except RecordConflict:
            # Concurrent same-caller call won the CAS: re-read and retry.
            continue
        except Exception:
            log.warning(
                "llm_gateway: caller ledger update dropped (backend fault); "
                "the call itself is unaffected"
            )
            return
        if saved is not None:
            return
    log.warning("llm_gateway: caller ledger update lost a CAS race repeatedly")


_SCREEN_MAX_CHARS = 200_000


def _screen_messages(body: Mapping[str, Any]):
    """Deny-wins content screen over the request's message texts.

    Uses the in-kernel deterministic detector ensemble (injection, exfil,
    PII) — never the optional agent-shield SDK, so the gateway works
    without it (kernel rule: fail open on absence, but a FIRING detector
    still blocks). Returns the verdict, or None when there is no text.
    """

    from .shield_ensemble import ShieldEnsemble

    parts: list[str] = []
    total = 0
    for message in body.get("messages") or []:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks = [content]
        elif isinstance(content, list):
            chunks = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            ]
        else:
            continue
        for chunk in chunks:
            if not chunk:
                continue
            take = chunk[: max(0, _SCREEN_MAX_CHARS - total)]
            parts.append(take)
            total += len(take)
            if total >= _SCREEN_MAX_CHARS:
                break
        if total >= _SCREEN_MAX_CHARS:
            break
    if not parts:
        return None
    try:
        return ShieldEnsemble().evaluate("\n".join(parts))
    except Exception:  # pragma: no cover -- screen must never break the path
        log.warning("llm_gateway: content screen errored; failing open")
        return None


def _otel_attr(attributes: list, key: str) -> str:
    for attribute in attributes or []:
        if not isinstance(attribute, Mapping):
            continue
        if str(attribute.get("key") or "") != key:
            continue
        value = attribute.get("value")
        if isinstance(value, Mapping):
            for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if kind in value:
                    return str(value[kind])
    return ""


def ingest_otel_spans(payload: object) -> dict[str, int]:
    """Ingest OTLP/JSON GenAI spans into the shadow-caller ledger.

    Reads the OpenTelemetry GenAI semantic conventions — ``gen_ai.system``,
    ``gen_ai.request.model``, ``gen_ai.usage.input_tokens`` /
    ``output_tokens`` — from ``resourceSpans[].scopeSpans[].spans[]`` and
    aggregates one ledger row per (service, model). Span bodies, prompts,
    and completions are never read or stored; only the model identity and
    token counts. Any agent framework exporting standard OTel traces
    becomes governed telemetry without an SDK.
    """

    settings = get_llm_gateway_config()
    if settings["enable"] is not True:
        raise LLMGatewayConfigError(
            "the LLM gateway requires [llm_gateway] enable = true"
        )
    if not isinstance(payload, Mapping):
        raise ValueError("the OTLP payload must be a JSON object")
    now = time.time()
    ingested = 0
    skipped = 0
    # Pre-aggregate per (caller, model): a collector batch commonly holds
    # hundreds of spans for one service+model, and one governed CAS write
    # per PAIR beats one per span. Any malformed span — wrong shapes,
    # non-numeric usage values — counts as skipped and never aborts the
    # batch (a mid-batch abort plus collector retry double-counts).
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for resource_spans in list(payload.get("resourceSpans") or [])[:100]:
        if not isinstance(resource_spans, Mapping):
            skipped += 1
            continue
        resource = resource_spans.get("resource") or {}
        service = _otel_attr(
            resource.get("attributes") if isinstance(resource, Mapping) else [],
            "service.name",
        ) or "unknown-service"
        caller = hashlib.sha256(
            f"otel-service:{service}".encode()
        ).hexdigest()[:24]
        for scope_spans in list(resource_spans.get("scopeSpans") or [])[:100]:
            if not isinstance(scope_spans, Mapping):
                continue
            for span in list(scope_spans.get("spans") or [])[:1000]:
                try:
                    if not isinstance(span, Mapping):
                        raise ValueError("span must be an object")
                    attributes = span.get("attributes") or []
                    system = _otel_attr(attributes, "gen_ai.system")
                    model = (
                        _otel_attr(attributes, "gen_ai.request.model")
                        or _otel_attr(attributes, "gen_ai.response.model")
                    )
                    if not system and not model:
                        raise ValueError("span carries no GenAI identity")
                    prompt_tokens = int(float(
                        _otel_attr(attributes, "gen_ai.usage.input_tokens")
                        or 0
                    ))
                    completion_tokens = int(float(
                        _otel_attr(attributes, "gen_ai.usage.output_tokens")
                        or 0
                    ))
                except Exception:
                    skipped += 1
                    continue
                bucket = totals.setdefault(
                    (caller, model or f"{system}:unknown"),
                    {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0},
                )
                bucket["requests"] += 1
                bucket["prompt_tokens"] += max(0, prompt_tokens)
                bucket["completion_tokens"] += max(0, completion_tokens)
                ingested += 1
    for (caller, model), bucket in totals.items():
        _record_call(
            caller=caller,
            model=model,
            status="otel",
            prompt_sha256="",
            usage={
                "prompt_tokens": bucket["prompt_tokens"],
                "completion_tokens": bucket["completion_tokens"],
            },
            now=now,
            requests=bucket["requests"],
        )
    return {"ingested": ingested, "skipped": skipped}


def handle_chat_completion(
    body: object,
    *,
    caller_token: str,
    upstream: Callable[[str, Mapping[str, str], Mapping[str, Any]], tuple[int, Any]]
    | None = None,
    now: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Screen, forward, and ledger one ``/v1/chat/completions`` call.

    Returns ``(http_status, response_body)``; error bodies use the OpenAI
    error envelope so unmodified SDKs surface them cleanly. Upstream error
    bodies are never echoed (they can reflect credentials).
    """

    settings = get_llm_gateway_config()
    if settings["enable"] is not True:
        raise LLMGatewayConfigError(
            "the LLM gateway requires [llm_gateway] enable = true"
        )
    if not _authorized(str(caller_token or ""), settings):
        return 401, {
            "error": {
                "message": "The gateway bearer is missing or wrong.",
                "type": "invalid_request_error",
            }
        }
    caller = _caller_digest(str(caller_token))
    if not isinstance(body, Mapping):
        return 400, {
            "error": {
                "message": "The request body must be a JSON object.",
                "type": "invalid_request_error",
            }
        }
    encoded = json.dumps(body, default=str).encode("utf-8", "ignore")
    if len(encoded) > settings["max_body_bytes"]:
        return 413, {
            "error": {
                "message": "The request exceeds the gateway's configured size cap.",
                "type": "invalid_request_error",
            }
        }
    model = str(body.get("model") or "").strip()
    if not model:
        return 400, {
            "error": {
                "message": "The request names no model.",
                "type": "invalid_request_error",
            }
        }
    if body.get("stream"):
        return 400, {
            "error": {
                "message": (
                    "Streaming is not supported through this gateway yet; "
                    "retry with stream disabled."
                ),
                "type": "invalid_request_error",
            }
        }
    current = time.time() if now is None else float(now)
    prompt_sha = hashlib.sha256(encoded).hexdigest()
    if settings["allowed_models"] and model not in settings["allowed_models"]:
        _record_call(
            caller=caller, model=model, status="blocked_model",
            prompt_sha256=prompt_sha, usage=None, now=current,
        )
        return 403, {
            "error": {
                "message": f"Model {model!r} is not on the gateway's allowlist.",
                "type": "invalid_request_error",
            }
        }
    if settings["screen_prompts"]:
        verdict = _screen_messages(body)
        if verdict is not None and not verdict.allowed:
            _record_call(
                caller=caller, model=model, status="screened_blocked",
                prompt_sha256=prompt_sha, usage=None, now=current,
            )
            return 403, {
                "error": {
                    "message": (
                        "The gateway's content screen blocked this request "
                        f"(severity {verdict.severity}; detectors: "
                        + ", ".join(
                            code["detector"] for code in verdict.reason_codes
                        )
                        + "). Contact your platform operator."
                    ),
                    "type": "invalid_request_error",
                }
            }
    upstream_token = os.environ.get(settings["upstream_token_env"], "")
    if not upstream_token:
        return 503, {
            "error": {
                "message": "The gateway's upstream credential is not configured.",
                "type": "server_error",
            }
        }
    url = f"{settings['upstream_url']}/v1/chat/completions"
    forward = upstream or _default_upstream
    try:
        status_code, payload = forward(
            url, {"Authorization": f"Bearer {upstream_token}"}, body,
        )
    except LLMGatewayError as exc:
        _record_call(
            caller=caller, model=model, status="egress_denied",
            prompt_sha256=prompt_sha, usage=None, now=current,
        )
        return 502, {
            "error": {"message": str(exc), "type": "server_error"}
        }
    except Exception:
        log.warning("llm_gateway: upstream request failed", exc_info=True)
        _record_call(
            caller=caller, model=model, status="upstream_error",
            prompt_sha256=prompt_sha, usage=None, now=current,
        )
        return 502, {
            "error": {
                "message": "The upstream request failed.",
                "type": "server_error",
            }
        }
    if status_code >= 400 or not isinstance(payload, Mapping):
        _record_call(
            caller=caller, model=model, status=f"upstream_{status_code}",
            prompt_sha256=prompt_sha, usage=None, now=current,
        )
        return 502, {
            "error": {
                "message": (
                    f"The upstream returned status {status_code}; its body is "
                    "not relayed through the gateway."
                ),
                "type": "server_error",
            }
        }
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
    _record_call(
        caller=caller, model=model, status="ok",
        prompt_sha256=prompt_sha, usage=usage, now=current,
    )
    return 200, dict(payload)


def gateway_status() -> dict[str, Any]:
    rows = _LEDGER.list(limit=_MAX_LIST)
    counts: dict[str, int] = {}
    callers: set[str] = set()
    requests = 0
    for row in rows:
        callers.add(str(row.get("caller_digest")))
        requests += int(row.get("request_count") or 0)
        status = str(row.get("last_status") or "")
        counts[status] = counts.get(status, 0) + 1
    return {
        "enabled": enabled(),
        "callers": len(callers),
        "caller_model_pairs": len(rows),
        "request_count": requests,
        "last_status_counts": counts,
    }


def list_callers(*, limit: int = 100) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIST:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST}")
    rows = _LEDGER.list(limit=limit)
    rows.sort(key=lambda row: -float(row.get("last_seen") or 0.0))
    return [
        {
            "caller_digest": row.get("caller_digest"),
            "model": row.get("model"),
            "request_count": row.get("request_count"),
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
            "last_status": row.get("last_status"),
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
        }
        for row in rows
    ]


__all__ = [
    "LLMGatewayConfigError",
    "LLMGatewayError",
    "LEDGER_SCHEMA",
    "enabled",
    "gateway_status",
    "get_llm_gateway_config",
    "handle_chat_completion",
    "ingest_otel_spans",
    "list_callers",
    "verify_gateway_bearer",
]
