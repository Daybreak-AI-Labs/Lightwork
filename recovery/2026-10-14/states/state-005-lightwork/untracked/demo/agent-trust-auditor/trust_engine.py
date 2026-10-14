"""Vendored, read-only Agent Trust Auditor analysis.

This module imports nothing from Maverick. Declarations and observations are
processed in memory and discarded; only minimized findings can enter the
unsigned local derived-record store.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from request_limits import reject_sensitive_fields

_SOURCE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ENTITY_TYPES = frozenset({"agent", "mcp_server", "provider", "tool"})
_OWNABLE_TYPES = frozenset({"agent", "mcp_server", "provider"})
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
_TITLES = {
    "unowned": "Observed asset has no accountable owner",
    "unpinned": "External asset identity is not pinned",
    "overprivileged": "Agent has an allow-all tool ceiling",
    "stale": "Observed asset is outside the freshness window",
    "undeclared-tool": "Observed tool is absent from the declaration",
}
_SEVERITIES = {
    "unowned": "medium",
    "unpinned": "high",
    "overprivileged": "high",
    "stale": "medium",
    "undeclared-tool": "high",
}
DEFAULT_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60
MAX_DERIVED_FINDINGS = 5_000
MAX_DERIVED_STORE_BYTES = 8 * 1024 * 1024
MAX_FINDING_OBSERVATIONS = 1_000


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _required_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{label} contains control characters")
    return text


def _optional_text(value: object, label: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{label} contains control characters")
    return text


def _source(value: object) -> str:
    source = _required_text(value, "source", 64)
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("source must be a stable namespace token")
    return source


def _source_id(value: object) -> str:
    return _required_text(value, "source_id", 256)


def _entity_type(value: object) -> str:
    kind = _required_text(value, "entity_type", 32).lower()
    if kind not in _ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(_ENTITY_TYPES)}")
    return kind


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite positive number")
    try:
        stamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(stamp) or stamp <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return stamp


def _names(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if len(value) > 256:
        raise ValueError(f"{label} exceeds 256 items")
    return sorted({_required_text(item, label, 256) for item in value})


def stable_entity_id(source: str, source_id: str) -> str:
    namespace = _source(source)
    identity = _source_id(source_id)
    return f"ATE-{_digest({'source': namespace, 'source_id': identity})[:32]}"


def stable_observation_id(source: str, source_id: str, observation_id: str) -> str:
    namespace = _source(source)
    identity = _source_id(source_id)
    observed = _required_text(observation_id, "observation_id", 256)
    return f"ATO-{_digest({'source': namespace, 'source_id': identity, 'observation_id': observed})[:32]}"


def _declaration(item: dict) -> dict:
    source = _source(item.get("source"))
    source_id = _source_id(item.get("source_id"))
    entity_id = stable_entity_id(source, source_id)
    kind = _entity_type(item.get("entity_type"))
    display_name = _optional_text(item.get("display_name"), "display_name", 256)
    return {
        "id": f"ATD-{_digest(entity_id)[:24]}",
        "entity_id": entity_id,
        "source": source,
        "source_id": source_id,
        "entity_type": kind,
        "display_name": display_name or source_id,
        "owner": _optional_text(item.get("owner"), "owner", 256),
        "allowed_tools": _names(item.get("allowed_tools"), "allowed_tools"),
        # The standalone's declaration input is explicitly selected for this
        # one unsigned analysis request.  Mark that ephemeral projection active
        # so the optional core pure-rule adapter preserves standalone semantics;
        # it is never persisted into Lightwork's governed declaration store.
        "lifecycle": "approved",
        "status": "approved",
    }


def _optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean or null")
    return value


def _observation(item: dict) -> dict:
    source = _source(item.get("source"))
    source_id = _source_id(item.get("source_id"))
    entity_id = stable_entity_id(source, source_id)
    kind = _entity_type(item.get("entity_type"))
    metadata = item.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    unexpected_metadata = set(metadata).difference(_METADATA_FIELDS)
    if unexpected_metadata:
        raise ValueError(
            f"unsupported observation metadata fields: {sorted(unexpected_metadata)}"
        )
    safe_metadata: dict[str, object] = {}
    for key, value in sorted(metadata.items()):
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("observation metadata values must be scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("observation metadata numbers must be finite")
        safe_metadata[key] = (
            _optional_text(value, f"metadata.{key}", 256)
            if isinstance(value, str)
            else value
        )
    return {
        "observation_id": stable_observation_id(
            source,
            source_id,
            item.get("observation_id"),
        ),
        "entity_id": entity_id,
        "source": source,
        "source_id": source_id,
        "entity_type": kind,
        "display_name": _optional_text(
            item.get("display_name"), "display_name", 256
        )
        or source_id,
        "observed_at": _timestamp(item.get("observed_at"), "observed_at"),
        "observed_tools": _names(item.get("observed_tools"), "observed_tools"),
        "pin_required": _optional_bool(item.get("pin_required"), "pin_required"),
        "pinned": _optional_bool(item.get("pinned"), "pinned"),
        "allow_all_tools": _optional_bool(
            item.get("allow_all_tools"), "allow_all_tools"
        ),
        "status": _optional_text(item.get("status"), "status", 64) or "observed",
        "metadata": safe_metadata,
    }


def _deduplicated(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    unique: dict[str, dict] = {}
    for row in rows:
        existing = unique.get(row[key])
        if existing is not None and existing != row:
            raise ValueError(f"conflicting duplicate {label}: {row[key]}")
        unique[row[key]] = row
    return unique


def prepare_analysis(
    declared_assets: list[dict], observations: list[dict]
) -> tuple[dict[str, dict], list[dict]]:
    reject_sensitive_fields(
        {"declared_assets": declared_assets, "observations": observations}
    )
    declarations = _deduplicated(
        [_declaration(item) for item in declared_assets],
        "entity_id",
        "declared asset",
    )
    observed = _deduplicated(
        [_observation(item) for item in observations],
        "observation_id",
        "observation",
    )
    by_entity: dict[str, list[dict]] = {}
    for observation in observed.values():
        declaration = declarations.get(observation["entity_id"])
        if declaration and declaration["entity_type"] != observation["entity_type"]:
            raise ValueError("declared and observed entity_type values do not match")
        by_entity.setdefault(observation["entity_id"], []).append(observation)
    entities: list[dict] = []
    for entity_id, rows in sorted(by_entity.items()):
        rows.sort(key=lambda row: (row["observed_at"], row["observation_id"]))
        latest = rows[-1]
        declaration = declarations.get(entity_id)
        tools = sorted({tool for row in rows for tool in row["observed_tools"]})
        entities.append(
            {
                "entity_id": entity_id,
                "entity_type": latest["entity_type"],
                "display_name": (declaration or latest)["display_name"],
                "last_seen": latest["observed_at"],
                "observed_tools": tools,
                "observation_ids": sorted(row["observation_id"] for row in rows),
                "tool_observations": {
                    tool: sorted(
                        row["observation_id"]
                        for row in rows
                        if tool in row["observed_tools"]
                    )
                    for tool in tools
                },
                "latest_observation_id": latest["observation_id"],
                "posture": {
                    "pin_required": latest["pin_required"],
                    "pinned": latest["pinned"],
                    "allow_all_tools": latest["allow_all_tools"],
                },
            }
        )
    return declarations, entities


def _finding(
    kind: str,
    entity: dict,
    *,
    summary: str,
    details: dict,
    citations: list[str] | None = None,
) -> dict:
    observation_ids = sorted(set(citations or entity["observation_ids"]))
    if not observation_ids:
        raise ValueError("findings require at least one observation citation")
    return {
        "id": f"ATF-{_digest({'finding_type': kind, 'entity_id': entity['entity_id']})[:24]}",
        "finding_type": kind,
        "severity": _SEVERITIES[kind],
        "entity_id": entity["entity_id"],
        "entity_type": entity["entity_type"],
        "display_name": entity["display_name"],
        "summary": summary,
        "details": details,
        "observation_ids": observation_ids,
        "citations": [
            {"observation_id": observation_id, "relation": "supports"}
            for observation_id in observation_ids
        ],
    }


def _entity_findings(
    entity: dict,
    declaration: dict | None,
    *,
    now: float,
    stale_after_seconds: float,
) -> list[dict]:
    findings: list[dict] = []
    if entity["entity_type"] in _OWNABLE_TYPES and not str(
        (declaration or {}).get("owner") or ""
    ).strip():
        findings.append(
            _finding(
                "unowned",
                entity,
                summary="The observed asset has no declared accountable owner.",
                details={"declaration_present": declaration is not None},
            )
        )
    posture = entity["posture"]
    latest_citation = [entity["latest_observation_id"]]
    if posture["pin_required"] is True and posture["pinned"] is not True:
        findings.append(
            _finding(
                "unpinned",
                entity,
                summary="The external asset has no source identity pin.",
                details={"pin_required": True, "pinned": posture["pinned"]},
                citations=latest_citation,
            )
        )
    if entity["entity_type"] == "agent" and posture["allow_all_tools"] is True:
        findings.append(
            _finding(
                "overprivileged",
                entity,
                summary="The observed agent has an allow-all tool ceiling.",
                details={"reason": "allow_all_tools"},
                citations=latest_citation,
            )
        )
    if entity["entity_type"] in _OWNABLE_TYPES:
        age = max(0.0, now - entity["last_seen"])
        if age > stale_after_seconds:
            findings.append(
                _finding(
                    "stale",
                    entity,
                    summary=(
                        "The asset has not been observed inside the freshness window."
                    ),
                    details={
                        "age_seconds": int(age),
                        "last_seen": entity["last_seen"],
                        "stale_after_seconds": stale_after_seconds,
                    },
                    citations=latest_citation,
                )
            )
    declared_tools = set((declaration or {}).get("allowed_tools") or [])
    undeclared_tools = sorted(set(entity["observed_tools"]) - declared_tools)
    if undeclared_tools:
        citations = sorted(
            {
                observation_id
                for tool in undeclared_tools
                for observation_id in entity["tool_observations"][tool]
            }
        )
        findings.append(
            _finding(
                "undeclared-tool",
                entity,
                summary="The asset used or exposes tools absent from its declaration.",
                details={
                    "declared_tools": sorted(declared_tools),
                    "undeclared_tools": undeclared_tools,
                },
                citations=citations,
            )
        )
    return findings


def _record_shape(row: dict) -> dict:
    kind = str(row.get("finding_type") or "")
    if kind not in _TITLES:
        raise ValueError(f"unsupported trust finding type: {kind!r}")
    raw_observation_ids = row.get("observation_ids", [])
    if not isinstance(raw_observation_ids, list) or len(raw_observation_ids) > MAX_FINDING_OBSERVATIONS:
        raise ValueError(
            f"finding observation_ids must contain at most {MAX_FINDING_OBSERVATIONS} items"
        )
    observation_ids = sorted(
        {
            _required_text(value, "observation_id", 36)
            for value in raw_observation_ids
        }
    )
    citations = row.get("citations") or [
        {"observation_id": value, "relation": "supports"}
        for value in observation_ids
    ]
    if not isinstance(citations, list) or len(citations) > MAX_FINDING_OBSERVATIONS:
        raise ValueError(
            f"finding citations must contain at most {MAX_FINDING_OBSERVATIONS} items"
        )
    cited_ids = sorted(
        {
            _required_text(item.get("observation_id"), "observation_id", 36)
            for item in citations
            if isinstance(item, dict) and item.get("relation") == "supports"
        }
    )
    if not observation_ids or cited_ids != observation_ids:
        raise ValueError("finding citations must bind every observation_id")
    return {
        "id": _required_text(row.get("id"), "finding id", 160),
        "type": "finding",
        "rule_id": f"agent-trust-{kind}",
        "finding_type": kind,
        "title": _TITLES[kind],
        "summary": _required_text(row.get("summary"), "summary", 500),
        "severity": _SEVERITIES[kind],
        "status": "open",
        "entity_id": _required_text(row.get("entity_id"), "entity_id", 36),
        "entity_type": _entity_type(row.get("entity_type")),
        "display_name": _required_text(row.get("display_name"), "display_name", 256),
        "details": dict(row.get("details") or {}),
        "observation_ids": observation_ids,
        "citations": [
            {"observation_id": value, "relation": "supports"}
            for value in observation_ids
        ],
    }


def analyze_snapshot(
    declared_assets: list[dict],
    observations: list[dict],
    *,
    now: float | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    entity_finder=None,
) -> list[dict]:
    """Analyze one bounded snapshot without retaining either input collection."""
    current = time.time() if now is None else _timestamp(now, "now")
    if isinstance(stale_after_seconds, bool):
        raise ValueError("stale_after_seconds must be a finite positive number")
    try:
        freshness = float(stale_after_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "stale_after_seconds must be a finite positive number"
        ) from exc
    if not math.isfinite(freshness) or not 60 <= freshness <= 366 * 24 * 60 * 60:
        raise ValueError("stale_after_seconds must be between 60 seconds and 366 days")
    declarations, entities = prepare_analysis(declared_assets, observations)
    if any(entity["last_seen"] > current + 300 for entity in entities):
        raise ValueError("observed_at exceeds the 300-second clock-skew allowance")
    finder = entity_finder or _entity_findings
    findings: list[dict] = []
    for entity in entities:
        rows = finder(
            entity,
            declarations.get(entity["entity_id"]),
            now=current,
            stale_after_seconds=freshness,
        )
        findings.extend(_record_shape(row) for row in rows)
    return sorted(
        {row["id"]: row for row in findings}.values(),
        key=lambda row: (row["entity_id"], row["finding_type"], row["id"]),
    )


def _minimized_details(kind: str, value: object) -> dict:
    details = value if isinstance(value, dict) else {}
    if kind == "unowned":
        return {"declaration_present": bool(details.get("declaration_present"))}
    if kind == "unpinned":
        return {
            "pin_required": details.get("pin_required") is True,
            "pinned": details.get("pinned") if isinstance(details.get("pinned"), bool) else None,
        }
    if kind == "overprivileged":
        return {"reason": "allow_all_tools"}
    if kind == "stale":
        return {
            "age_seconds": max(0, int(details.get("age_seconds", 0) or 0)),
            "last_seen": float(details.get("last_seen", 0) or 0),
            "stale_after_seconds": float(details.get("stale_after_seconds", 0) or 0),
        }
    if kind == "undeclared-tool":
        return {
            "declared_tools": _names(details.get("declared_tools"), "declared_tools"),
            "undeclared_tools": _names(
                details.get("undeclared_tools"), "undeclared_tools"
            ),
        }
    raise ValueError(f"unsupported trust finding type: {kind!r}")


def _minimized_record(record: dict) -> dict:
    reject_sensitive_fields(record)
    if record.get("type") != "finding":
        raise ValueError("only derived Agent Trust Auditor findings may be persisted")
    shaped = _record_shape(record)
    shaped["details"] = _minimized_details(
        shaped["finding_type"], shaped.get("details")
    )
    return shaped


def _checked_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    return value


@contextmanager
def _cross_process_lock(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            while True:
                try:
                    if lock_path.stat().st_size == 0:
                        handle.seek(0)
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised on POSIX CI
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LocalDerivedStore:
    """Bounded unsigned local CAS containing current derived findings only."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get(
                "AGENT_TRUST_AUDITOR_STORE",
                ".agent-trust-auditor/findings.json",
            )
        )
        self._lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"revision": 0, "records": []}
        if self.path.is_symlink():
            raise ValueError("local Agent Trust Auditor store must not be a symlink")
        if self.path.stat().st_size > MAX_DERIVED_STORE_BYTES:
            raise ValueError("local Agent Trust Auditor store exceeds 8 MiB")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("revision"), int)
            or isinstance(state.get("revision"), bool)
            or state["revision"] < 0
            or not isinstance(state.get("records"), list)
        ):
            raise ValueError("local Agent Trust Auditor store is invalid")
        return state

    def state(self) -> dict:
        state = self._read()
        by_id: dict[str, dict] = {}
        for record in state["records"]:
            record_revision = _checked_revision(record.get("revision"))
            if record_revision > state["revision"]:
                raise ValueError("local finding revision exceeds the store revision")
            minimized = _minimized_record(record) | {"revision": record_revision}
            previous = by_id.get(minimized["id"])
            if previous is None or previous["revision"] <= record_revision:
                by_id[minimized["id"]] = minimized
        records = sorted(
            by_id.values(), key=lambda row: (row["revision"], row["id"])
        )
        return {
            "revision": state["revision"],
            "records": records,
        }

    def list_records(self) -> list[dict]:
        return self.state()["records"]

    def save_findings(self, records: list[dict], expected_revision: int) -> list[dict]:
        if not isinstance(records, list):
            raise ValueError("findings must be a list")
        incoming = {
            minimized["id"]: minimized
            for minimized in (_minimized_record(record) for record in records)
        }
        if len(incoming) > MAX_DERIVED_FINDINGS:
            raise ValueError(
                f"one finding batch may contain at most {MAX_DERIVED_FINDINGS} records"
            )
        revision = _checked_revision(expected_revision)
        with self._lock:
            with _cross_process_lock(self.path):
                state = self._read()
                if state["revision"] != revision:
                    raise ValueError(
                        "revision changed: "
                        f"expected {revision}, found {state['revision']}"
                    )
                merged: dict[str, dict] = {}
                for record in state["records"]:
                    record_revision = _checked_revision(record.get("revision"))
                    minimized = _minimized_record(record)
                    stored = {**minimized, "revision": record_revision}
                    previous = merged.get(stored["id"])
                    if previous is None or previous["revision"] <= record_revision:
                        merged[stored["id"]] = stored

                saved: list[dict] = []
                for finding_id, minimized in incoming.items():
                    previous = merged.get(finding_id)
                    if previous is not None and {
                        key: value
                        for key, value in previous.items()
                        if key != "revision"
                    } == minimized:
                        saved.append(previous)
                        continue
                    state["revision"] += 1
                    stored = {**minimized, "revision": state["revision"]}
                    merged[finding_id] = stored
                    saved.append(stored)

                incoming_ids = set(incoming)
                ordered = sorted(
                    merged.values(), key=lambda row: (row["revision"], row["id"])
                )
                if len(ordered) > MAX_DERIVED_FINDINGS:
                    remove = len(ordered) - MAX_DERIVED_FINDINGS
                    removable_ids = [
                        row["id"] for row in ordered if row["id"] not in incoming_ids
                    ]
                    if len(removable_ids) < remove:
                        raise ValueError("incoming findings exceed bounded retention")
                    removed = set(removable_ids[:remove])
                    ordered = [row for row in ordered if row["id"] not in removed]
                state["records"] = ordered

                while True:
                    try:
                        payload = json.dumps(
                            state,
                            allow_nan=False,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        ).encode("utf-8")
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "local Agent Trust Auditor store contains invalid JSON"
                        ) from exc
                    if len(payload) <= MAX_DERIVED_STORE_BYTES:
                        break
                    removable = [
                        row for row in state["records"] if row["id"] not in incoming_ids
                    ]
                    if not removable:
                        raise ValueError("incoming findings exceed the 8 MiB store limit")
                    overflow = len(payload) - MAX_DERIVED_STORE_BYTES
                    estimate = max(1, len(payload) // max(1, len(state["records"])))
                    remove = min(
                        len(removable), max(1, math.ceil(overflow / estimate))
                    )
                    removed = {row["id"] for row in removable[:remove]}
                    state["records"] = [
                        row for row in state["records"] if row["id"] not in removed
                    ]
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "wb",
                    dir=self.path.parent,
                    delete=False,
                ) as tmp:
                    tmp.write(payload)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    temp = Path(tmp.name)
                os.replace(temp, self.path)
                return saved


__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "LocalDerivedStore",
    "MAX_DERIVED_FINDINGS",
    "MAX_DERIVED_STORE_BYTES",
    "analyze_snapshot",
    "prepare_analysis",
    "stable_entity_id",
    "stable_observation_id",
]
