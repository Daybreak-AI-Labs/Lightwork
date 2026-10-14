"""Bound and validate untrusted standalone Agent Trust Auditor input."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 512 * 1024
MAX_DEPTH = 8
MAX_OBJECT_KEYS = 64
MAX_ARRAY_ITEMS = 1024
MAX_STRING_BYTES = 4096
MAX_TOTAL_KEYS = 8192
MAX_DECLARED_ASSETS = 256
MAX_OBSERVATIONS = 1000
MAX_ITEM_BYTES = 16 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_FORBIDDEN_KEY = re.compile(
    r"(?:^|[_.-])(?:raw|telemetry|prompts?|messages?|contents?|body|bodies|"
    r"payloads?|completions?|inputs?|arguments?|args?|results?|outputs?|"
    r"tool[_.-]?calls?|credentials?|secrets?|passwords?|tokens?|keys?|"
    r"authorization|headers?|cookies?)(?:$|[_.-])",
    re.IGNORECASE,
)
_DECLARED_FIELDS = frozenset(
    {
        "source",
        "source_id",
        "entity_type",
        "display_name",
        "owner",
        "allowed_tools",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {
        "observation_id",
        "source",
        "source_id",
        "entity_type",
        "display_name",
        "observed_at",
        "observed_tools",
        "pin_required",
        "pinned",
        "allow_all_tools",
        "status",
        "metadata",
    }
)
_METADATA_FIELDS = frozenset(
    {
        "environment",
        "model",
        "operation",
        "service_name",
        "service_namespace",
        "tool_type",
        "transport",
    }
)


@dataclass
class PayloadError(ValueError):
    detail: str
    status_code: int = 422

    def __str__(self) -> str:
        return self.detail


def _declared_length(request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise PayloadError("invalid Content-Length header") from exc
    if length < 0:
        raise PayloadError("invalid Content-Length header")
    return length


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return 443 if scheme == "https" else 80 if scheme == "http" else None


def _validate_json_boundary(request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0]
    if media_type.strip().lower() != "application/json":
        raise PayloadError("Content-Type must be application/json", status_code=415)
    origin = request.headers.get("origin")
    if origin is None:
        return
    parsed = urlsplit(origin)
    try:
        origin_port = parsed.port
        request_port = request.url.port
    except ValueError as exc:
        raise PayloadError("browser Origin has an invalid port", status_code=403) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.scheme != request.url.scheme
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query or parsed.fragment)
        or (request.url.hostname or "").lower() not in _LOOPBACK_HOSTS
        or (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS
        or _effective_port(parsed.scheme, origin_port)
        != _effective_port(request.url.scheme, request_port)
    ):
        raise PayloadError(
            "browser requests must use the same loopback origin",
            status_code=403,
        )


def _validate_shape(
    value,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
) -> None:
    if depth > MAX_DEPTH:
        raise PayloadError(f"JSON nesting exceeds {MAX_DEPTH} levels")
    if counter is None:
        counter = [0]
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise PayloadError(
                f"JSON objects may contain at most {MAX_OBJECT_KEYS} keys"
            )
        counter[0] += len(value)
        if counter[0] > MAX_TOTAL_KEYS:
            raise PayloadError(f"JSON may contain at most {MAX_TOTAL_KEYS} keys")
        for key, item in value.items():
            if not isinstance(key, str) or len(key.encode("utf-8")) > 256:
                raise PayloadError("JSON object keys must be strings up to 256 bytes")
            _validate_shape(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise PayloadError(f"JSON arrays may contain at most {MAX_ARRAY_ITEMS} items")
        for item in value:
            _validate_shape(item, depth=depth + 1, counter=counter)
    elif isinstance(value, str) and len(value.encode("utf-8")) > MAX_STRING_BYTES:
        raise PayloadError(f"JSON strings may not exceed {MAX_STRING_BYTES} bytes")
    elif isinstance(value, float) and not math.isfinite(value):
        raise PayloadError("JSON numbers must be finite")


def reject_sensitive_fields(value) -> None:
    """Reject content-bearing or credential-shaped keys at every depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().replace(" ", "_")
            if _FORBIDDEN_KEY.search(normalized):
                raise PayloadError(f"field {key!r} is not accepted at this boundary")
            reject_sensitive_fields(item)
    elif isinstance(value, list):
        for item in value:
            reject_sensitive_fields(item)


def _objects(value, label: str, limit: int) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PayloadError(f"{label} must be a list of objects")
    if len(value) > limit:
        raise PayloadError(f"{label} may contain at most {limit} items")
    for item in value:
        size = len(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()
        )
        if size > MAX_ITEM_BYTES:
            raise PayloadError(f"each {label} item may contain at most {MAX_ITEM_BYTES} bytes")
    return value


def _check_fields(item: dict, allowed: frozenset[str], label: str) -> None:
    unexpected = set(item).difference(allowed)
    if unexpected:
        raise PayloadError(f"unsupported {label} fields: {sorted(unexpected)}")


def _validate_string_list(item: dict, key: str) -> None:
    if key not in item:
        return
    values = item[key]
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise PayloadError(f"{key} must be a list of strings")
    if len(values) > 256:
        raise PayloadError(f"{key} may contain at most 256 items")


def validate_analysis_payload(value: dict) -> tuple[list[dict], list[dict]]:
    reject_sensitive_fields(value)
    allowed = {
        "declared_assets",
        "observations",
        "expected_revision",
        "now",
        "stale_after_seconds",
    }
    unexpected = set(value).difference(allowed)
    if unexpected:
        raise PayloadError(f"unsupported request fields: {sorted(unexpected)}")
    declared = _objects(
        value.get("declared_assets"), "declared_assets", MAX_DECLARED_ASSETS
    )
    observations = _objects(
        value.get("observations"), "observations", MAX_OBSERVATIONS
    )
    for item in declared:
        _check_fields(item, _DECLARED_FIELDS, "declared asset")
        _validate_string_list(item, "allowed_tools")
    for item in observations:
        _check_fields(item, _OBSERVATION_FIELDS, "observation")
        _validate_string_list(item, "observed_tools")
        for key in ("pin_required", "pinned", "allow_all_tools"):
            if key in item and item[key] is not None and not isinstance(item[key], bool):
                raise PayloadError(f"{key} must be a boolean or null")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise PayloadError("observation metadata must be an object")
        unexpected_metadata = set(metadata).difference(_METADATA_FIELDS)
        if unexpected_metadata:
            raise PayloadError(
                "unsupported observation metadata fields: "
                f"{sorted(unexpected_metadata)}"
            )
        for metadata_value in metadata.values():
            if not isinstance(metadata_value, (str, int, float, bool)):
                raise PayloadError("observation metadata values must be scalar")
            if isinstance(metadata_value, float) and not math.isfinite(metadata_value):
                raise PayloadError("observation metadata numbers must be finite")
    return declared, observations


async def read_bounded_json(request):
    _validate_json_boundary(request)
    declared = _declared_length(request)
    if declared is not None and declared > MAX_REQUEST_BYTES:
        raise PayloadError(
            f"request body exceeds {MAX_REQUEST_BYTES} bytes",
            status_code=413,
        )
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise PayloadError(
                f"request body exceeds {MAX_REQUEST_BYTES} bytes",
                status_code=413,
            )
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError("request body must be valid JSON") from exc
    _validate_shape(value)
    if not isinstance(value, dict):
        raise PayloadError("request body must be a JSON object")
    return value


def require_expected_revision(value: dict) -> int:
    if "expected_revision" not in value:
        raise PayloadError("expected_revision is required")
    revision = value["expected_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise PayloadError("expected_revision must be a non-negative integer")
    return revision


__all__ = [
    "PayloadError",
    "read_bounded_json",
    "reject_sensitive_fields",
    "require_expected_revision",
    "validate_analysis_payload",
]
