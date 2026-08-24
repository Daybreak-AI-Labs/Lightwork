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
    return {
        "enable": flag,
        "upstream_url": upstream,
        "upstream_token_env": upstream_token_env,
        "gateway_token_env": gateway_token_env,
        "allowed_models": [model.strip() for model in models],
        "max_body_bytes": max_body,
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
) -> None:
    """Aggregate one call into the caller ledger; never store bodies."""

    record_id = f"LGC-{hashlib.sha256(f'{caller}:{model}'.encode()).hexdigest()[:24]}"
    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)

    def mutate(row: dict[str, Any]) -> None:
        row["request_count"] = int(row.get("request_count") or 0) + 1
        row["last_seen"] = now
        row["last_status"] = status
        row["last_prompt_sha256"] = prompt_sha256
        row["prompt_tokens"] = int(row.get("prompt_tokens") or 0) + prompt_tokens
        row["completion_tokens"] = (
            int(row.get("completion_tokens") or 0) + completion_tokens
        )

    for _ in range(3):
        current = _LEDGER.get(record_id)
        if current is None:
            record = {
                "id": record_id,
                "schema": LEDGER_SCHEMA,
                "caller_digest": caller,
                "model": model,
                "request_count": 1,
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
        saved = _LEDGER.update(
            record_id,
            mutate,
            expected_revision=int(current.get("revision") or 0),
            action="llm_gateway_call",
            actor="gateway:llm",
        )
        if saved is not None:
            return
    log.warning("llm_gateway: caller ledger update lost a CAS race repeatedly")


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
    "list_callers",
]
