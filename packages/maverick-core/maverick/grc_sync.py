"""Idempotent outbound sync of governance records into a customer GRC system.

Model-risk findings, accepted risks, and incidents become records in the
customer's system of record (ServiceNow IRM tables, Archer applications, or
any REST GRC) through the same SSRF-safe, enterprise-egress-guarded request
path every governed connector uses. The mapper is deliberately narrow:

* **Idempotent by construction.** Each (kind, source record, connector, path)
  pair owns exactly one outbound record with a stable ``GRC-`` id; a re-run
  with an unchanged source is a no-op, and a changed source becomes an update
  (PATCH) to the already-created remote record, never a duplicate create.
* **Round-trip linked.** The outbound payload carries the Lightwork record id
  as the remote ``correlation_id`` (plus an optional backlink URL), and the
  outbound ledger stores the remote id the target returned — navigable in
  both directions.
* **Fail-closed on ambiguity.** A write that succeeded but whose response
  could not be parsed for a remote id lands in the terminal
  ``delivered_unconfirmed`` state instead of being blindly retried into
  duplicates; a failed write records a bounded error and stays retryable.
* **Bounded content.** Only the source record's id, type, severity, summary,
  and content digest leave the platform. Evidence bodies, citations, and
  detail payloads never do.

The operator-invoked ``maverick grc-sync run`` (or the admin API caller) is
the human authorization boundary for a batch; every delivery is a governed
outbound record in the tenant-scoped ledger.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

OUTBOUND_SCHEMA = "lightwork.grc-outbound-record.v1"

_STORE = GovernedRecordStore("grc_sync_outbound", "GRC", "grc_outbound_record")
_KINDS = ("finding", "risk_acceptance", "incident")
_PATH_KEYS = {
    "finding": "findings_path",
    "risk_acceptance": "acceptances_path",
    "incident": "incidents_path",
}
_REMOTE_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{4,128}\Z")
_MAX_ERROR = 500
_MAX_BATCH = 200


class GRCSyncError(RuntimeError):
    """Base error for the outbound GRC sync."""


class GRCSyncConfigError(GRCSyncError):
    """The [grc_sync] configuration is missing, malformed, or disabled."""


class GRCSyncStateError(GRCSyncError):
    """A sync invariant does not hold."""


def _sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GRCSyncConfigError(f"{label} must be a non-empty string")
    out = value.strip()
    if len(out) > limit:
        raise GRCSyncConfigError(f"{label} exceeds {limit} characters")
    return out


def _bounded(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def get_grc_sync_config() -> dict[str, Any]:
    """Return the validated ``[grc_sync]`` policy, fail-closed."""

    try:
        from .config import config_source_errors, load_global_config

        errors = config_source_errors(include_tenant=False)
        root = load_global_config()
    except Exception as exc:
        raise GRCSyncConfigError("GRC sync configuration is unavailable") from exc
    if errors or not isinstance(root, Mapping):
        raise GRCSyncConfigError("GRC sync configuration is unreadable")
    raw = root.get("grc_sync") or {}
    if not isinstance(raw, Mapping):
        raise GRCSyncConfigError("grc_sync must be a table")
    known = {
        "enable",
        "connector",
        "findings_path",
        "acceptances_path",
        "incidents_path",
        "backlink_base",
        "response_field_params",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise GRCSyncConfigError(f"unknown grc_sync keys: {', '.join(unknown)}")
    flag = raw.get("enable", False)
    if not isinstance(flag, bool):
        raise GRCSyncConfigError("grc_sync.enable must be a boolean")
    connector = raw.get("connector", "servicenow")
    connector = _text(connector, "grc_sync.connector", 64).lower()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", connector):
        raise GRCSyncConfigError("grc_sync.connector must be a lowercase token")
    paths: dict[str, str] = {}
    for kind, key in _PATH_KEYS.items():
        value = raw.get(key, "")
        if value in ("", None):
            paths[kind] = ""
            continue
        path = _text(value, f"grc_sync.{key}", 512)
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise GRCSyncConfigError(
                f"grc_sync.{key} must be an absolute, query-free path"
            )
        paths[kind] = path
    backlink = raw.get("backlink_base", "")
    if backlink not in ("", None):
        backlink = _text(backlink, "grc_sync.backlink_base", 512)
        if not backlink.lower().startswith("https://"):
            raise GRCSyncConfigError("grc_sync.backlink_base must be HTTPS")
        backlink = backlink.rstrip("/")
    else:
        backlink = ""
    response_params = raw.get("response_field_params", {})
    if not isinstance(response_params, Mapping):
        raise GRCSyncConfigError("grc_sync.response_field_params must be a table")
    bounded_params: dict[str, str] = {}
    for key, value in response_params.items():
        bounded_params[_text(key, "response field param name", 64)] = _text(
            value, "response field param value", 128,
        )
    if len(bounded_params) > 8:
        raise GRCSyncConfigError("grc_sync.response_field_params is oversized")
    return {
        "enable": flag,
        "connector": connector,
        "paths": paths,
        "backlink_base": backlink,
        "response_field_params": bounded_params,
    }


def enabled() -> bool:
    try:
        config = get_grc_sync_config()
    except Exception:
        return False
    return bool(config["enable"]) and any(config["paths"].values())


def _require_config() -> dict[str, Any]:
    config = get_grc_sync_config()
    if not config["enable"]:
        raise GRCSyncConfigError("GRC sync requires [grc_sync] enable = true")
    if not any(config["paths"].values()):
        raise GRCSyncConfigError(
            "GRC sync requires at least one of findings_path, acceptances_path, "
            "or incidents_path"
        )
    return config


def _outbound_id(kind: str, source_id: str, connector: str, path: str) -> str:
    return "GRC-" + _sha256([kind, source_id, connector, path])[:24]


def _source_rows(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Bounded projections of every source record with an enabled path."""

    from . import model_risk_assurance as mra

    rows: list[dict[str, Any]] = []
    paths = config["paths"]
    if paths["finding"] or paths["risk_acceptance"]:
        for finding in mra.findings():
            accepted = mra.current_risk_acceptance(finding) is not None
            kind = "risk_acceptance" if accepted else "finding"
            if not paths[kind]:
                continue
            rows.append(
                {
                    "kind": kind,
                    "source_id": str(finding.get("id") or ""),
                    "asset_id": str(finding.get("asset_id") or ""),
                    "severity": str(finding.get("severity") or "unknown"),
                    "summary": _bounded(finding.get("summary"), 1000),
                    "content_sha256": str(
                        finding.get("finding_sha256") or _sha256(finding)
                    ),
                }
            )
    if paths["incident"]:
        for incident in mra.list_incidents():
            rows.append(
                {
                    "kind": "incident",
                    "source_id": str(incident.get("id") or ""),
                    "asset_id": str(incident.get("asset_id") or ""),
                    "severity": str(incident.get("severity") or "unknown"),
                    "summary": _bounded(incident.get("summary"), 1000),
                    "content_sha256": str(
                        incident.get("payload_sha256") or _sha256(incident)
                    ),
                }
            )
    rows = [row for row in rows if row["source_id"]]
    if len(rows) > _MAX_BATCH * 25:
        raise GRCSyncStateError("GRC sync source volume exceeds its bound")
    rows.sort(key=lambda row: (row["kind"], row["source_id"]))
    return rows


def outbound_payload(row: Mapping[str, Any], *, backlink_base: str) -> dict[str, Any]:
    """The exact bounded body sent to the GRC system. Nothing else leaves."""

    kind = str(row["kind"]).replace("_", " ")
    payload = {
        "short_description": _bounded(
            f"[Lightwork] {str(row['severity']).upper()} {kind}: {row['summary']}",
            160,
        ),
        "description": _bounded(
            (
                f"Lightwork governed {kind} {row['source_id']} for asset "
                f"{row['asset_id']}.\nSeverity: {row['severity']}\n"
                f"Summary: {row['summary']}\n"
                f"Content digest (sha256): {row['content_sha256']}\n"
                "Evidence and detail payloads remain in Lightwork; this record "
                "is a governed pointer, not the record of truth."
            ),
            4000,
        ),
        "correlation_id": row["source_id"],
        "correlation_display": "Lightwork",
        "severity": row["severity"],
    }
    if backlink_base:
        payload["u_lightwork_url"] = f"{backlink_base}/security/assurance"
    return payload


def _default_writer(config: Mapping[str, Any]) -> Callable[..., str]:
    """A writer over the SSRF-safe, egress-guarded governed request path."""

    from .governed_rest import GOVERNED_REST_FACTORIES, RestConnector

    factory = GOVERNED_REST_FACTORIES.get(config["connector"])
    if factory is not None:
        connector = factory()
    else:
        name = config["connector"]
        connector = RestConnector(
            name=name,
            base_url_env=f"{name.upper()}_INSTANCE_URL",
            token_env=f"{name.upper()}_TOKEN",
        )

    def write(op: str, path: str, body: Mapping[str, Any]) -> str:
        from .tools._rest_connector import _rest_execute

        args: dict[str, Any] = {"body": dict(body)}
        if config["response_field_params"]:
            args["params"] = dict(config["response_field_params"])
        return _rest_execute(
            op,
            path,
            args,
            name=connector.name,
            config=connector._config,  # noqa: SLF001 - same plumbing as .write
            headers=connector._headers,  # noqa: SLF001
            norm=connector._norm,  # noqa: SLF001
        )

    return write


def _remote_id_from(response: str) -> str | None:
    """Extract the created/updated remote record id, or None if unparseable."""

    if not isinstance(response, str) or response.startswith("ERROR:"):
        return None
    try:
        data = json.loads(response)
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    result = data.get("result")
    candidates = []
    if isinstance(result, Mapping):
        candidates.extend([result.get("sys_id"), result.get("id")])
    candidates.extend([data.get("sys_id"), data.get("id")])
    for candidate in candidates:
        if isinstance(candidate, str) and _REMOTE_ID_RE.fullmatch(candidate):
            return candidate
    return None


def _deliver_one(
    row: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    writer: Callable[..., str],
    actor: str,
) -> dict[str, Any]:
    path = config["paths"][row["kind"]]
    outbound_id = _outbound_id(
        row["kind"], row["source_id"], config["connector"], path,
    )
    payload = outbound_payload(row, backlink_base=config["backlink_base"])
    payload_sha = _sha256(payload)
    current = _STORE.get(outbound_id)
    if current is None:
        record = {
            "id": outbound_id,
            "schema": OUTBOUND_SCHEMA,
            "kind": row["kind"],
            "source_id": row["source_id"],
            "source_sha256": row["content_sha256"],
            "connector": config["connector"],
            "path": path,
            "payload_sha256": payload_sha,
            "status": "pending",
            "remote_id": None,
            "attempts": 0,
            "last_error": None,
        }
        try:
            current = _STORE.create(record, action="stage_grc_outbound", actor=actor)
        except RecordConflict:
            current = _STORE.get(outbound_id)
            if current is None:
                raise
    status = str(current.get("status"))
    unchanged = current.get("source_sha256") == row["content_sha256"]
    if status == "delivered_unconfirmed":
        return dict(current)
    if status == "delivered" and unchanged:
        return dict(current)

    if status == "delivered":
        op = "patch"
        remote = str(current.get("remote_id") or "")
        target_path = f"{path}/{remote}"
    else:
        op = "post"
        target_path = path

    try:
        response = writer(op, target_path, payload)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, bounded
        response = f"ERROR: writer failed ({type(exc).__name__})"
    succeeded = isinstance(response, str) and not response.startswith("ERROR:")
    remote_id = _remote_id_from(response) if succeeded else None

    def mutate(value: dict[str, Any]) -> None:
        value["attempts"] = int(value.get("attempts") or 0) + 1
        value["payload_sha256"] = payload_sha
        if not succeeded:
            value["status"] = "failed" if value.get("remote_id") is None else "delivered"
            value["last_error"] = _bounded(response, _MAX_ERROR)
            return
        value["source_sha256"] = row["content_sha256"]
        value["last_error"] = None
        if op == "patch":
            value["status"] = "delivered"
            return
        if remote_id is None:
            # The create write went through but the response gave no id:
            # terminal, never blindly re-created into a duplicate.
            value["status"] = "delivered_unconfirmed"
            return
        value["status"] = "delivered"
        value["remote_id"] = remote_id

    saved = _STORE.update(
        outbound_id,
        mutate,
        expected_revision=int(current.get("revision") or 0),
        action="deliver_grc_outbound",
        actor=actor,
    )
    if saved is None:
        raise GRCSyncStateError("GRC outbound record disappeared during delivery")
    return dict(saved)


def run_sync(
    *,
    actor: str,
    writer: Callable[..., str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Deliver up to ``limit`` pending/changed records; per-record isolation."""

    config = _require_config()
    operator = _text(actor, "actor", 256)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_BATCH:
        raise ValueError(f"limit must be between 1 and {_MAX_BATCH}")
    send = writer or _default_writer(config)
    delivered: list[dict[str, Any]] = []
    unchanged = 0
    failures: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    processed = 0
    for row in _source_rows(config):
        if processed >= limit:
            break
        before = _STORE.get(
            _outbound_id(
                row["kind"], row["source_id"], config["connector"],
                config["paths"][row["kind"]],
            )
        )
        status_before = "" if before is None else str(before.get("status"))
        same_source = (
            before is not None
            and before.get("source_sha256") == row["content_sha256"]
        )
        if status_before in {"delivered", "delivered_unconfirmed"} and same_source:
            unchanged += 1
            continue
        if status_before == "delivered_unconfirmed":
            # The create went through but the remote id is unknown; an update
            # is impossible and a re-create would duplicate. Surface it.
            blocked.append(
                {
                    "source_id": row["source_id"],
                    "reason": (
                        "source changed but the remote record id is "
                        "unconfirmed; resolve the outbound record manually"
                    ),
                }
            )
            continue
        processed += 1
        try:
            outcome = _deliver_one(row, config=config, writer=send, actor=operator)
        except (GRCSyncError, RecordConflict, ValueError) as exc:
            failures.append({"source_id": row["source_id"], "reason": _bounded(exc, 256)})
            continue
        if str(outcome.get("status")).startswith("delivered"):
            delivered.append(outcome)
        else:
            failures.append(
                {
                    "source_id": row["source_id"],
                    "reason": _bounded(outcome.get("last_error"), 256),
                }
            )
    return {
        "delivered": delivered,
        "delivered_count": len(delivered),
        "unchanged_count": unchanged,
        "blocked": blocked,
        "blocked_count": len(blocked),
        "failures": failures,
        "failure_count": len(failures),
        "limit": limit,
        "connector": config["connector"],
    }


def list_outbound(*, limit: int = 100) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    rows = _STORE.list(limit=limit)
    rows.sort(key=lambda row: (str(row.get("kind")), str(row.get("id"))))
    return [
        {
            "id": row.get("id"),
            "kind": row.get("kind"),
            "source_id": row.get("source_id"),
            "connector": row.get("connector"),
            "path": row.get("path"),
            "status": row.get("status"),
            "remote_id": row.get("remote_id"),
            "attempts": row.get("attempts"),
            "last_error": row.get("last_error"),
            "revision": row.get("revision"),
        }
        for row in rows
    ]


def sync_status() -> dict[str, Any]:
    counts = {"pending": 0, "delivered": 0, "delivered_unconfirmed": 0, "failed": 0}
    for row in _STORE.list(limit=500):
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    return {"outbound": counts, "enabled": enabled()}


__all__ = [
    "GRCSyncConfigError",
    "GRCSyncError",
    "GRCSyncStateError",
    "OUTBOUND_SCHEMA",
    "enabled",
    "get_grc_sync_config",
    "list_outbound",
    "outbound_payload",
    "run_sync",
    "sync_status",
]
