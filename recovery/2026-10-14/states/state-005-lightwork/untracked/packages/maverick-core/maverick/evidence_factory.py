"""Production evidence campaigns with explicit provenance and honest verdicts.

The evidence factory coordinates existing, safe checks without turning their
absence into proof.  A campaign is a governed CAS record, every drill is a
separate governed record, and the finalized campaign is a canonical manifest
signed through the same trusted-key path as :mod:`maverick.proof_pack`.

V1 deliberately does not execute destructive drills.  Collectors are injected,
bounded by a supervisory timeout, and must return an explicit status.  The
factory stores redacted structured results and hashes of sanitized artifact
content, never the artifact content itself.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.metadata
import json
import math
import multiprocessing
import os
import platform
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from .governed_records import GovernedRecordStore

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
ERROR = "ERROR"

STATUSES = frozenset({PASS, FAIL, NOT_RUN, INSUFFICIENT_DATA, ERROR})
MODES = frozenset({"offline", "integration", "live", "operator_attested"})

_SCHEMA = "lightwork.production-evidence-campaign.v1"
_CAMPAIGN_RECORD_SCHEMA = "lightwork.evidence-campaign-record.v1"
_DRILL_RECORD_SCHEMA = "lightwork.evidence-drill-record.v1"
_NAME_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{7,64}\Z")
_IMAGE_DIGEST_RE = re.compile(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}\Z")
_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|passwd|credential|authorization|api[_-]?key|"
    r"private[_-]?key|signing[_-]?key|dsn|database[_-]?url|redis[_-]?url)",
    re.IGNORECASE,
)
_MAX_DATA_DEPTH = 8
_MAX_COLLECTION_ITEMS = 256
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_ARTIFACTS = 64
_MAX_CALLBACK_RESPONSE_BYTES = 80 * 1024 * 1024
_MAX_COLLECTORS = 256
_MAX_ATTEMPT_HISTORY = 64
# A claim lives for the sum of its supervised collector deadlines plus bounded
# allowances for child-process startup/teardown and parent-side sanitization,
# redaction, hashing, and CAS settlement of the <=80 MiB callback frame.
# Recovery uses the persisted deadline, never a caller-selected timeout, so an
# operator cannot steal a healthy claim by supplying a smaller staleness window.
_CLAIM_GRACE_SECONDS = 60.0
_PROCESS_OVERHEAD_SECONDS = 5.0
_COLLECTOR_SETTLEMENT_SECONDS = 300.0
_LEGACY_DRILL_CLAIM_SECONDS = (
    3600.0 + _CLAIM_GRACE_SECONDS + _COLLECTOR_SETTLEMENT_SECONDS
)
_DEFAULT_DEPENDENCIES = (
    "maverick-core",
    "cryptography",
    "psycopg",
    "redis",
    "arq",
    "fastapi",
)

_CAMPAIGNS = GovernedRecordStore(
    "evidence_campaigns", "ECM", "production_evidence_campaign"
)
_DRILLS = GovernedRecordStore(
    "evidence_drills", "EDR", "production_evidence_drill"
)


class EvidenceFactoryDisabled(RuntimeError):
    """Raised when a mutation is attempted without the explicit config gate."""


@dataclass(frozen=True)
class Provenance:
    """Immutable deployment identity bound into every campaign manifest."""

    commit: str
    image_digest: str
    config_sha256: str
    deployment_id: str
    migrations: Mapping[str, Any]
    dependencies: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        commit = _required(self.commit, "commit", 256).lower()
        image_digest = _required(
            self.image_digest, "image_digest", 512
        ).lower()
        out = {
            "commit": commit,
            "image_digest": image_digest,
            "config_sha256": str(self.config_sha256 or "").strip().lower(),
            "deployment_id": _required(
                self.deployment_id, "deployment_id", 256
            ),
            "migrations": _redact_tree(dict(self.migrations)),
            "dependencies": {
                _required(name, "dependency name", 128): _required(
                    version, "dependency version", 256
                )
                for name, version in sorted(self.dependencies.items())
            },
        }
        if not _SHA256_RE.fullmatch(out["config_sha256"]):
            raise ValueError("config_sha256 must be a lowercase SHA-256 digest")
        if not _COMMIT_RE.fullmatch(commit):
            raise ValueError("commit must be a 7-64 character Git object id")
        if not _IMAGE_DIGEST_RE.fullmatch(image_digest):
            raise ValueError("image_digest must be an immutable SHA-256 digest")
        if not out["migrations"]:
            raise ValueError("migration versions are required")
        if not out["dependencies"]:
            raise ValueError("dependency versions are required")
        return out


@dataclass(frozen=True)
class ServiceObjectives:
    """Operator-declared service objectives; observations never invent them."""

    slo: Mapping[str, Any]
    rto_seconds: float
    rpo_seconds: float

    def to_dict(self) -> dict[str, Any]:
        if not isinstance(self.slo, Mapping) or not self.slo:
            raise ValueError("at least one declared SLO is required")
        if len(self.slo) > 64:
            raise ValueError("declared SLOs exceed the 64-item limit")
        slo: dict[str, Any] = {}
        for raw_name, raw_value in sorted(self.slo.items()):
            name = _required(raw_name, "SLO name", 128)
            if isinstance(raw_value, bool):
                value: Any = raw_value
            elif isinstance(raw_value, int):
                value = raw_value
            elif isinstance(raw_value, float):
                if not math.isfinite(raw_value):
                    raise ValueError(f"SLO {name!r} must be finite")
                value = raw_value
            elif isinstance(raw_value, str):
                value = _redact_text(raw_value)[:1000]
            else:
                raise ValueError(f"SLO {name!r} has an unsupported value")
            slo[name] = value
        rto = _nonnegative_number(self.rto_seconds, "rto_seconds")
        rpo = _nonnegative_number(self.rpo_seconds, "rpo_seconds")
        return {"slo": slo, "rto_seconds": rto, "rpo_seconds": rpo}


@dataclass(frozen=True)
class OperatorWitness:
    """A named human witness whose statement is bound by the final signature."""

    identity: str
    role: str
    statement: str
    witnessed_at: float

    def to_dict(self) -> dict[str, Any]:
        timestamp = float(self.witnessed_at)
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError("witnessed_at must be a positive timestamp")
        return {
            "identity": _required(self.identity, "witness identity", 256),
            "role": _required(self.role, "witness role", 256),
            "statement": _redact_text(
                _required(self.statement, "witness statement", 4000)
            ),
            "witnessed_at": timestamp,
        }


@dataclass(frozen=True)
class ArtifactInput:
    """In-memory collector artifact; only sanitized hash metadata is retained."""

    name: str
    content: bytes | str
    media_type: str = "application/octet-stream"


@dataclass(frozen=True)
class CollectorOutcome:
    """The only accepted collector return type.

    A truthy object or a bare boolean is intentionally not interpreted as a
    pass.  Collectors must choose one status from :data:`STATUSES`.
    """

    status: str
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactInput, ...] = ()


@dataclass(frozen=True)
class CollectorContext:
    campaign_id: str
    provenance: Mapping[str, Any]
    objectives: Mapping[str, Any]
    started_at: float
    collection_attempt_id: str = ""


@dataclass(frozen=True)
class CollectorSpec:
    name: str
    mode: str
    collect: Callable[[CollectorContext], CollectorOutcome]
    timeout_seconds: float = 30.0
    drill: bool = False
    destructive: bool = False

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(str(self.name or "")):
            raise ValueError("collector name is invalid")
        if self.mode not in MODES:
            raise ValueError(f"collector mode must be one of {sorted(MODES)}")
        if not callable(self.collect):
            raise ValueError("collector callback must be callable")
        timeout = float(self.timeout_seconds)
        if not math.isfinite(timeout) or not 0.01 <= timeout <= 3600:
            raise ValueError("collector timeout must be between 0.01 and 3600 seconds")
        if self.destructive:
            raise ValueError("destructive collectors are not supported by evidence factory v1")


def enabled() -> bool:
    """Deployment-global, strict feature gate; malformed policy fails closed."""
    try:
        from .config import config_source_errors, load_global_config

        cfg = load_global_config()
        if config_source_errors(include_tenant=False):
            return False
        section = cfg.get("evidence_factory")
        return isinstance(section, dict) and section.get("enable") is True
    except Exception:  # pragma: no cover - uncertainty cannot enable evidence writes
        return False


def _require_enabled() -> None:
    if not enabled():
        raise EvidenceFactoryDisabled(
            "production evidence factory requires [evidence_factory] enable = true"
        )


def _required(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text[:limit]


def _nonnegative_number(value: object, label: str) -> float:
    out = float(value)
    if not math.isfinite(out) or out < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return out


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _redact_text(value: str) -> str:
    text = str(value)
    try:
        from .safety.secret_detector import redact

        return redact(text)[0]
    except Exception:  # pragma: no cover - fail closed if the detector is unavailable
        return "[REDACTED:unavailable]" if text else ""


def _redact_tree(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
    """Return a bounded JSON-safe tree with secret-bearing keys removed."""
    if _depth > _MAX_DATA_DEPTH:
        return "[TRUNCATED:depth]"
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[REDACTED:non-finite]"
    if isinstance(value, str):
        return _redact_text(value)[:8000]
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)} bytes]"

    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        return "[TRUNCATED:cycle]"
    seen.add(identity)
    try:
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            items = islice(value.items(), _MAX_COLLECTION_ITEMS)
            for raw_key, raw_value in items:
                key = str(raw_key or "")[:256]
                if _SENSITIVE_KEY.search(key):
                    out[key] = "[REDACTED:sensitive-key]"
                else:
                    out[key] = _redact_tree(
                        raw_value, _depth=_depth + 1, _seen=seen
                    )
            return {key: out[key] for key in sorted(out)}
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(islice(value, _MAX_COLLECTION_ITEMS))
            if isinstance(value, (set, frozenset)):
                items.sort(key=lambda item: str(item))
            return [
                _redact_tree(item, _depth=_depth + 1, _seen=seen)
                for item in items
            ]
        return _redact_text(str(value))[:8000]
    finally:
        seen.discard(identity)


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Hash configuration structure after removing all secret values."""
    if not isinstance(config, Mapping):
        raise ValueError("config must be an object")
    return hashlib.sha256(_canonical(_redact_tree(dict(config)))).hexdigest()


def dependency_versions(
    names: Iterable[str] = _DEFAULT_DEPENDENCIES,
) -> dict[str, str]:
    """Return explicit installed/not-installed versions for critical packages."""
    versions = {"python": platform.python_version()}
    for raw_name in names:
        name = _required(raw_name, "dependency name", 128)
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"
        versions[name] = str(version)
    return {name: versions[name] for name in sorted(versions)}


def build_provenance(
    *,
    commit: str | None = None,
    image_digest: str | None = None,
    deployment_id: str | None = None,
    config: Mapping[str, Any] | None = None,
    migrations: Mapping[str, Any] | None = None,
    dependencies: Mapping[str, str] | None = None,
) -> Provenance:
    """Build strict runtime provenance, refusing unbound deployment identity."""
    if config is None:
        from .config import config_source_errors, load_global_config

        config = load_global_config()
        if config_source_errors(include_tenant=False):
            raise ValueError("configuration sources are unreadable or corrupt")
    if migrations is None:
        from .migration_governance import fingerprint

        migrations = fingerprint()
    return Provenance(
        commit=commit or os.environ.get("MAVERICK_GIT_COMMIT") or os.environ.get("GITHUB_SHA", ""),
        image_digest=image_digest or os.environ.get("MAVERICK_IMAGE_DIGEST", ""),
        config_sha256=config_fingerprint(config),
        deployment_id=deployment_id or os.environ.get("MAVERICK_DEPLOYMENT_ID", ""),
        migrations=dict(migrations),
        dependencies=dict(dependencies or dependency_versions()),
    )


def _controls(values: Iterable[object] | None) -> list[str]:
    if values is None:
        return []
    return sorted({_required(value, "control id", 128) for value in list(values)[:512]})


def _assert_expected_revision(record: Mapping[str, Any], expected: int) -> None:
    """Reject a stale caller before a collector or signer performs any work."""
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    current = record.get("revision")
    if current != expected:
        from .privacy_ops import RecordConflict

        raise RecordConflict(
            f"evidence record changed (expected revision {expected}, found {current})"
        )


def _claim_timestamp(value: object, label: str) -> float:
    """Return a finite positive wall-clock timestamp or fail closed."""
    if isinstance(value, bool):
        raise ValueError(f"{label} is invalid")
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError(f"{label} is invalid")
    return timestamp


def _collection_lease_expires(
    started_at: float,
    specs: Iterable[CollectorSpec],
) -> float:
    deadlines = [float(spec.timeout_seconds) for spec in specs]
    return (
        started_at
        + math.fsum(deadlines)
        + _CLAIM_GRACE_SECONDS
        + len(deadlines)
        * (_PROCESS_OVERHEAD_SECONDS + _COLLECTOR_SETTLEMENT_SECONDS)
    )


def _attempt_lease_expires(attempt: Mapping[str, Any]) -> float:
    """Read a persisted lease, conservatively upgrading a pre-lease claim."""
    explicit = attempt.get("lease_expires_at")
    if explicit is not None:
        return _claim_timestamp(explicit, "collection claim lease")
    started = _claim_timestamp(attempt.get("started_at"), "collection claim start")
    collectors = attempt.get("collectors")
    if not isinstance(collectors, list) or len(collectors) > _MAX_COLLECTORS:
        raise ValueError("collection claim collectors are invalid")
    timeouts: list[float] = []
    for collector in collectors:
        if not isinstance(collector, Mapping):
            raise ValueError("collection claim collector is invalid")
        timeout = collector.get("timeout_seconds")
        if isinstance(timeout, bool):
            raise ValueError("collection claim timeout is invalid")
        try:
            seconds = float(timeout)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("collection claim timeout is invalid") from exc
        if not math.isfinite(seconds) or not 0.01 <= seconds <= 3600:
            raise ValueError("collection claim timeout is invalid")
        timeouts.append(seconds)
    return (
        started
        + math.fsum(timeouts)
        + _CLAIM_GRACE_SECONDS
        + len(timeouts)
        * (_PROCESS_OVERHEAD_SECONDS + _COLLECTOR_SETTLEMENT_SECONDS)
    )


def _drill_lease_expires(drill: Mapping[str, Any]) -> float:
    explicit = drill.get("lease_expires_at")
    if explicit is not None:
        return _claim_timestamp(explicit, "drill claim lease")
    # Older records did not persist the collector timeout.  The maximum legal
    # callback timeout plus the same grace period is the earliest safe recovery
    # point for those records.
    started = _claim_timestamp(drill.get("started_at"), "drill claim start")
    return started + _LEGACY_DRILL_CLAIM_SECONDS


def _authority_now(
    store: GovernedRecordStore,
    supplied: float | None,
    label: str,
) -> float:
    """Use backend time for shared leases; local tests may inject a timestamp."""
    if supplied is None:
        return _claim_timestamp(store.authoritative_time(), label)
    if store.backend_kind != "local":
        raise ValueError(f"{label} override is not allowed with shared authority")
    return _claim_timestamp(supplied, label)


def _abort_payload(
    *,
    actor: str,
    reason: str,
    aborted_at: float,
    recovery: bool,
) -> dict[str, Any]:
    return {
        "actor": _redact_text(_required(actor, "actor", 256)),
        "reason": _redact_text(_required(reason, "abort reason", 2000)),
        "aborted_at": aborted_at,
        "recovery": bool(recovery),
    }


def start_campaign(
    *,
    title: str,
    provenance: Provenance,
    objectives: ServiceObjectives,
    actor: str,
    controls: Iterable[object] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Create an empty governed campaign ready for one collection pass."""
    _require_enabled()
    started = time.time() if now is None else float(now)
    if not math.isfinite(started) or started <= 0:
        raise ValueError("campaign start must be a positive timestamp")
    record = {
        "id": _CAMPAIGNS.new_id(),
        "schema": _CAMPAIGN_RECORD_SCHEMA,
        "title": _redact_text(_required(title, "campaign title", 300)),
        "status": "collecting",
        "started_at": started,
        "provenance": provenance.to_dict(),
        "objectives": objectives.to_dict(),
        "controls": _controls(controls),
        "results": [],
        "manifest": None,
        "evidence_graph_node_id": "",
        "collection_attempt": None,
        "collection_attempt_history": [],
        "abort": None,
    }
    return _CAMPAIGNS.create(record, action="start_campaign", actor=actor)


def _artifact_metadata(artifact: ArtifactInput) -> dict[str, Any]:
    name = _required(artifact.name, "artifact name", 256)
    media_type = _required(artifact.media_type, "artifact media_type", 128)
    raw = artifact.content.encode("utf-8") if isinstance(artifact.content, str) else bytes(artifact.content)
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds the 64 MiB evidence limit")
    redactions = 0
    sanitized = raw
    if isinstance(artifact.content, str) or media_type.startswith("text/") or "json" in media_type:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if "json" in media_type:
                try:
                    text = _canonical(_redact_tree(json.loads(text))).decode("utf-8")
                except (TypeError, ValueError):
                    pass
            try:
                from .safety.secret_detector import redact

                text, matches = redact(text)
                redactions = len(matches)
            except Exception:  # pragma: no cover - do not hash possibly sensitive text
                text = "[REDACTED:unavailable]"
                redactions = 1
            sanitized = text.encode("utf-8")
    return {
        "name": _redact_text(name),
        "media_type": media_type,
        "sha256": hashlib.sha256(sanitized).hexdigest(),
        "size_bytes": len(sanitized),
        "redactions_applied": redactions,
    }


def _normalize_outcome(outcome: object) -> dict[str, Any]:
    if not isinstance(outcome, CollectorOutcome):
        raise TypeError("collector must return CollectorOutcome")
    status = str(outcome.status or "").strip().upper()
    if status not in STATUSES:
        raise ValueError(f"collector status must be one of {sorted(STATUSES)}")
    if not isinstance(outcome.data, Mapping):
        raise ValueError("collector data must be an object")
    artifact_inputs = tuple(islice(outcome.artifacts, _MAX_ARTIFACTS + 1))
    if len(artifact_inputs) > _MAX_ARTIFACTS:
        raise ValueError(f"collector artifacts exceed the {_MAX_ARTIFACTS}-item limit")
    artifacts = [_artifact_metadata(item) for item in artifact_inputs]
    names = [item["name"] for item in artifacts]
    if len(names) != len(set(names)):
        raise ValueError("collector artifact names must be unique")
    return {
        "status": status,
        "summary": _redact_text(_required(outcome.summary, "collector summary", 8000)),
        "data": _redact_tree(outcome.data),
        "artifacts": artifacts,
    }


def _collector_process_entry(connection, payload: bytes) -> None:
    """Deserialize and execute one collector inside a killable child process."""
    try:
        import cloudpickle

        callback, context = cloudpickle.loads(payload)
        try:
            response = ("result", callback(context))
        except BaseException as exc:  # noqa: BLE001 - converted to inert metadata
            response = ("error", type(exc).__name__, str(exc))
        try:
            serialized = cloudpickle.dumps(response)
            if len(serialized) > _MAX_CALLBACK_RESPONSE_BYTES:
                serialized = cloudpickle.dumps(
                    (
                        "error",
                        "CollectorResponseTooLarge",
                        "collector response exceeded the process boundary limit",
                    )
                )
            connection.send_bytes(serialized)
        except BaseException as exc:  # noqa: BLE001 - child must report safely
            fallback = cloudpickle.dumps(
                ("error", "CollectorSerializationError", type(exc).__name__)
            )
            connection.send_bytes(fallback)
    finally:
        connection.close()


def _run_callback(
    spec: CollectorSpec,
    context: CollectorContext,
) -> tuple[object | None, tuple[str, str] | None, bool]:
    """Run one callback in a spawned process and terminate it on timeout.

    A timed-out thread keeps executing and can mutate state after the campaign
    has recorded an error.  The spawned boundary is deliberately killable and
    receives only the immutable collector context. Collectors must therefore be
    serializable and must acquire any read-only clients inside the child.
    """
    try:
        import cloudpickle

        payload = cloudpickle.dumps((spec.collect, context))
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - serialization fails closed
        return None, ("CollectorSerializationError", str(exc)), False

    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_collector_process_entry,
        args=(sender, payload),
        name=f"evidence-collector-{spec.name}",
        daemon=True,
    )
    try:
        process.start()
    except (KeyboardInterrupt, SystemExit):
        receiver.close()
        sender.close()
        raise
    except BaseException as exc:  # noqa: BLE001 - process start fails closed
        receiver.close()
        sender.close()
        return None, ("CollectorProcessError", str(exc)), False
    sender.close()
    process.join(float(spec.timeout_seconds))
    if process.is_alive():
        process.terminate()
        process.join(1.0)
        if process.is_alive():  # pragma: no cover - terminate normally suffices
            process.kill()
            process.join(1.0)
        receiver.close()
        return None, None, True
    try:
        if not receiver.poll():
            return None, (
                "CollectorProcessError",
                f"collector exited with code {process.exitcode} without a result",
            ), False
        raw = receiver.recv_bytes(_MAX_CALLBACK_RESPONSE_BYTES)
        response = cloudpickle.loads(raw)
    except BaseException as exc:  # noqa: BLE001 - corrupt child response fails closed
        return None, ("CollectorProcessError", str(exc)), False
    finally:
        receiver.close()
    if not isinstance(response, tuple) or not response:
        return None, ("CollectorProcessError", "collector returned an invalid frame"), False
    if response[0] == "result" and len(response) == 2:
        return response[1], None, False
    if response[0] == "error" and len(response) == 3:
        return None, (str(response[1]), str(response[2])), False
    return None, ("CollectorProcessError", "collector returned an invalid frame"), False


def _not_run_result(spec: CollectorSpec, summary: str, *, now: float) -> dict[str, Any]:
    return {
        "name": spec.name,
        "mode": spec.mode,
        "status": NOT_RUN,
        "summary": summary,
        "data": {},
        "artifacts": [],
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "witness": None,
        "drill_id": "",
    }


def _run_collector(
    spec: CollectorSpec,
    context: CollectorContext,
    *,
    actor: str,
    witness: OperatorWitness | None,
) -> dict[str, Any]:
    started = _DRILLS.authoritative_time() if spec.drill else time.time()
    requires_witness = spec.drill or spec.mode == "operator_attested"
    witness_payload: dict[str, Any] | None = None
    if witness is not None:
        witness_payload = witness.to_dict()
    drill = (
        _start_drill(
            spec,
            context,
            actor=actor,
            witness_payload=witness_payload,
            started_at=started,
        )
        if spec.drill
        else None
    )
    if requires_witness:
        if witness is None:
            result = _not_run_result(
                spec,
                "operator witness required; collector was not executed",
                now=started,
            )
            return _complete_drill(drill, result, actor=actor)

    outcome, raised, timed_out = _run_callback(spec, context)
    finished = time.time()
    if timed_out:
        normalized = {
            "status": ERROR,
            "summary": f"collector timed out after {float(spec.timeout_seconds):g} seconds",
            "data": {"error_type": "TimeoutError"},
            "artifacts": [],
        }
    elif raised is not None:
        error_type, error_message = raised
        normalized = {
            "status": ERROR,
            "summary": (
                f"collector raised {_redact_text(error_type)}: "
                f"{_redact_text(error_message)}"
            ),
            "data": {"error_type": _redact_text(error_type)},
            "artifacts": [],
        }
    else:
        try:
            normalized = _normalize_outcome(outcome)
        except Exception as exc:
            normalized = {
                "status": ERROR,
                "summary": f"invalid collector result: {type(exc).__name__}: {_redact_text(str(exc))}",
                "data": {"error_type": type(exc).__name__},
                "artifacts": [],
            }
    result = {
        "name": spec.name,
        "mode": spec.mode,
        **normalized,
        "started_at": started,
        "finished_at": finished,
        "duration_ms": max(0, int(round((finished - started) * 1000))),
        "witness": witness_payload,
        "drill_id": "",
    }
    return _complete_drill(drill, result, actor=actor)


def _start_drill(
    spec: CollectorSpec,
    context: CollectorContext,
    *,
    actor: str,
    witness_payload: dict[str, Any] | None,
    started_at: float,
) -> dict[str, Any]:
    """Persist the running drill before any callback can create an effect."""
    return _DRILLS.create(
        {
            "id": _DRILLS.new_id(),
            "schema": _DRILL_RECORD_SCHEMA,
            "campaign_id": context.campaign_id,
            "collection_attempt_id": context.collection_attempt_id,
            "collector": spec.name,
            "mode": spec.mode,
            "status": "running",
            "started_at": started_at,
            "timeout_seconds": float(spec.timeout_seconds),
            "lease_expires_at": (
                started_at
                + float(spec.timeout_seconds)
                + _CLAIM_GRACE_SECONDS
                + _COLLECTOR_SETTLEMENT_SECONDS
            ),
            "witness": copy.deepcopy(witness_payload),
            "result": None,
            "abort": None,
        },
        action="start_drill",
        actor=actor,
    )


def _complete_drill(
    drill: dict[str, Any] | None,
    result: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    if drill is None:
        return result
    result["drill_id"] = drill["id"]
    result["drill_revision"] = int(drill["revision"]) + 1

    def _complete(row: dict[str, Any]) -> None:
        if row.get("status") != "running":
            raise ValueError("drill is not running")
        row["status"] = str(result["status"])
        row["result"] = copy.deepcopy(result)

    completed = _DRILLS.update(
        drill["id"],
        _complete,
        expected_revision=drill["revision"],
        action="complete_drill",
        actor=actor,
    )
    if completed is None:  # pragma: no cover - governed record cannot vanish normally
        raise RuntimeError("drill disappeared during completion")
    if completed["revision"] != result["drill_revision"]:  # pragma: no cover
        raise RuntimeError("drill revision did not match committed result")
    return result


def collect_campaign(
    campaign_id: str,
    *,
    expected_revision: int,
    collectors: Iterable[CollectorSpec],
    actor: str,
    witnesses: Mapping[str, OperatorWitness] | None = None,
) -> dict[str, Any]:
    """Run one ordered collector set and commit results with campaign CAS."""
    _require_enabled()
    campaign = _CAMPAIGNS.get(str(campaign_id or ""))
    if campaign is None:
        raise KeyError(campaign_id)
    _assert_expected_revision(campaign, expected_revision)
    if campaign.get("status") != "collecting":
        raise ValueError("campaign is not collecting")
    specs = list(collectors)
    if len(specs) > _MAX_COLLECTORS:
        raise ValueError(f"campaign collectors exceed the {_MAX_COLLECTORS}-item limit")
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise ValueError("collector names must be unique within a campaign")
    witness_map = dict(witnesses or {})
    attempt_id = uuid.uuid4().hex
    reserved_at = _CAMPAIGNS.authoritative_time()
    lease_expires_at = _collection_lease_expires(reserved_at, specs)

    def _reserve(row: dict[str, Any]) -> None:
        if row.get("status") != "collecting":
            raise ValueError("campaign is not collecting")
        row["status"] = "collection_running"
        row["collection_attempt"] = {
            "id": attempt_id,
            "actor": _redact_text(_required(actor, "actor", 256)),
            "started_at": reserved_at,
            "lease_expires_at": lease_expires_at,
            "status": "running",
            "collectors": [
                {
                    "name": spec.name,
                    "mode": spec.mode,
                    "drill": spec.drill,
                    "timeout_seconds": float(spec.timeout_seconds),
                }
                for spec in specs
            ],
        }

    reserved = _CAMPAIGNS.update(
        campaign["id"],
        _reserve,
        expected_revision=expected_revision,
        action="reserve_collection",
        actor=actor,
    )
    if reserved is None:  # pragma: no cover
        raise RuntimeError("campaign disappeared during collection reservation")
    context = CollectorContext(
        campaign_id=reserved["id"],
        provenance=copy.deepcopy(reserved["provenance"]),
        objectives=copy.deepcopy(reserved["objectives"]),
        started_at=float(reserved["started_at"]),
        collection_attempt_id=attempt_id,
    )
    results = [
        _run_collector(
            spec,
            context,
            actor=actor,
            witness=witness_map.get(spec.name),
        )
        for spec in specs
    ]
    collected_at = _CAMPAIGNS.authoritative_time()

    def _commit(row: dict[str, Any]) -> None:
        attempt = row.get("collection_attempt")
        if (
            row.get("status") != "collection_running"
            or not isinstance(attempt, dict)
            or attempt.get("id") != attempt_id
        ):
            raise ValueError("campaign collection reservation is not owned")
        row["results"] = results
        row["status"] = "collected"
        row["collected_at"] = collected_at
        attempt["finished_at"] = row["collected_at"]
        attempt["status"] = "completed"

    saved = _CAMPAIGNS.update(
        campaign["id"],
        _commit,
        expected_revision=reserved["revision"],
        action="collect_campaign",
        actor=actor,
    )
    if saved is None:  # pragma: no cover
        raise RuntimeError("campaign disappeared during collection")
    return saved


def _abort_drill_claim(
    drill_id: str,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    now: float,
    recovery: bool,
    require_stale: bool,
    expected_attempt_id: str = "",
) -> dict[str, Any]:
    drill = _DRILLS.get(str(drill_id or ""))
    if drill is None:
        raise KeyError(drill_id)
    _assert_expected_revision(drill, expected_revision)
    if drill.get("status") != "running":
        raise ValueError("drill is not running")
    if expected_attempt_id and str(drill.get("collection_attempt_id") or "") not in {
        "",
        expected_attempt_id,
    }:
        raise ValueError("drill is owned by a different collection attempt")
    if require_stale and now < _drill_lease_expires(drill):
        raise ValueError("drill claim is not stale")
    abort = _abort_payload(
        actor=actor,
        reason=reason,
        aborted_at=now,
        recovery=recovery,
    )

    def _abort(row: dict[str, Any]) -> None:
        if row.get("status") != "running":
            raise ValueError("drill is not running")
        if expected_attempt_id and str(row.get("collection_attempt_id") or "") not in {
            "",
            expected_attempt_id,
        }:
            raise ValueError("drill is owned by a different collection attempt")
        if require_stale and now < _drill_lease_expires(row):
            raise ValueError("drill claim is not stale")
        row["status"] = "aborted"
        row["finished_at"] = now
        row["abort"] = copy.deepcopy(abort)

    saved = _DRILLS.update(
        drill["id"],
        _abort,
        expected_revision=expected_revision,
        action="recover_stale_drill" if recovery else "abort_drill",
        actor=actor,
    )
    if saved is None:  # pragma: no cover
        raise RuntimeError("drill disappeared during abort")
    return saved


def abort_drill(
    drill_id: str,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Fence and terminally abort a running drill claim.

    This revokes the drill owner's right to commit; it cannot synchronously
    terminate a callback running in another process. Evidence Factory v1's
    destructive-collector prohibition is therefore still the safety boundary.
    """
    _require_enabled()
    aborted_at = _authority_now(_DRILLS, now, "abort time")
    return _abort_drill_claim(
        drill_id,
        expected_revision=expected_revision,
        actor=actor,
        reason=reason,
        now=aborted_at,
        recovery=False,
        require_stale=False,
    )


def recover_stale_drill(
    drill_id: str,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Abort a drill only after its persisted owner lease has expired."""
    _require_enabled()
    recovered_at = _authority_now(_DRILLS, now, "recovery time")
    return _abort_drill_claim(
        drill_id,
        expected_revision=expected_revision,
        actor=actor,
        reason=reason,
        now=recovered_at,
        recovery=True,
        require_stale=True,
    )


def _abort_attempt_drills(
    campaign_id: str,
    attempt_id: str,
    *,
    actor: str,
    reason: str,
    now: float,
    recovery: bool,
) -> None:
    """Best-effort CAS sweep after the campaign claim itself is fenced."""
    from .privacy_ops import RecordConflict

    for drill in list_drills(campaign_id=campaign_id):
        if drill.get("status") != "running":
            continue
        drill_attempt = str(drill.get("collection_attempt_id") or "")
        if drill_attempt not in {"", attempt_id}:
            continue
        try:
            _abort_drill_claim(
                drill["id"],
                expected_revision=drill["revision"],
                actor=actor,
                reason=reason,
                now=now,
                recovery=recovery,
                # The stale campaign deadline covers every sequential collector
                # deadline. Once that campaign claim is fenced, its drills are
                # orphaned even if an old drill omitted its own lease metadata.
                require_stale=False,
                expected_attempt_id=attempt_id,
            )
        except (RecordConflict, ValueError):
            # A legitimate owner completion or another abort may win between
            # list_drills() and the fresh precondition/CAS above. Treat only a
            # recognized terminal record for this exact attempt as benign;
            # missing, corrupt, still-running, or differently owned state must
            # remain visible to the recovery operator.
            latest = _DRILLS.get(str(drill.get("id") or ""))
            terminal = set(STATUSES) | {"aborted"}
            if (
                isinstance(latest, dict)
                and latest.get("status") in terminal
                and str(latest.get("collection_attempt_id") or "")
                in {"", attempt_id}
            ):
                continue
            raise


def recover_stale_campaign(
    campaign_id: str,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Fence an expired collection owner and reopen the campaign for retry."""
    _require_enabled()
    recovered_at = _authority_now(_CAMPAIGNS, now, "recovery time")
    campaign = _CAMPAIGNS.get(str(campaign_id or ""))
    if campaign is None:
        raise KeyError(campaign_id)
    _assert_expected_revision(campaign, expected_revision)
    attempt = campaign.get("collection_attempt")
    if campaign.get("status") != "collection_running" or not isinstance(
        attempt, Mapping
    ):
        raise ValueError("campaign has no running collection claim")
    attempt_id = _required(attempt.get("id"), "collection attempt id", 128)
    if recovered_at < _attempt_lease_expires(attempt):
        raise ValueError("campaign collection claim is not stale")
    abort = _abort_payload(
        actor=actor,
        reason=reason,
        aborted_at=recovered_at,
        recovery=True,
    )

    def _recover(row: dict[str, Any]) -> None:
        active = row.get("collection_attempt")
        if (
            row.get("status") != "collection_running"
            or not isinstance(active, dict)
            or active.get("id") != attempt_id
        ):
            raise ValueError("campaign collection reservation is not owned")
        if recovered_at < _attempt_lease_expires(active):
            raise ValueError("campaign collection claim is not stale")
        history = row.get("collection_attempt_history")
        if history is None:
            history = []
        if not isinstance(history, list):
            raise ValueError("campaign collection history is invalid")
        abandoned = copy.deepcopy(active)
        abandoned["status"] = "aborted"
        abandoned["finished_at"] = recovered_at
        abandoned["abort"] = copy.deepcopy(abort)
        history.append(abandoned)
        row["collection_attempt_history"] = history[-_MAX_ATTEMPT_HISTORY:]
        row["collection_attempt"] = None
        row["status"] = "collecting"
        row["last_recovery"] = {
            **copy.deepcopy(abort),
            "collection_attempt_id": attempt_id,
        }

    saved = _CAMPAIGNS.update(
        campaign["id"],
        _recover,
        expected_revision=expected_revision,
        action="recover_stale_collection",
        actor=actor,
    )
    if saved is None:  # pragma: no cover
        raise RuntimeError("campaign disappeared during recovery")
    # The campaign CAS above is the fence. A late owner cannot commit because
    # it still holds the previous revision and attempt id. Sweep any orphaned
    # drill records afterward; a crash here cannot wedge the reopened campaign.
    _abort_attempt_drills(
        campaign["id"],
        attempt_id,
        actor=actor,
        reason=reason,
        now=recovered_at,
        recovery=True,
    )
    return saved


def abort_campaign(
    campaign_id: str,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Fence and terminally abort a collecting or running campaign."""
    _require_enabled()
    aborted_at = _authority_now(_CAMPAIGNS, now, "abort time")
    campaign = _CAMPAIGNS.get(str(campaign_id or ""))
    if campaign is None:
        raise KeyError(campaign_id)
    _assert_expected_revision(campaign, expected_revision)
    if campaign.get("status") not in {"collecting", "collection_running"}:
        raise ValueError("campaign cannot be aborted from its current status")
    active = campaign.get("collection_attempt")
    attempt_id = (
        _required(active.get("id"), "collection attempt id", 128)
        if isinstance(active, Mapping)
        else ""
    )
    abort = _abort_payload(
        actor=actor,
        reason=reason,
        aborted_at=aborted_at,
        recovery=False,
    )

    def _abort(row: dict[str, Any]) -> None:
        if row.get("status") not in {"collecting", "collection_running"}:
            raise ValueError("campaign cannot be aborted from its current status")
        current = row.get("collection_attempt")
        if attempt_id and (
            not isinstance(current, dict) or current.get("id") != attempt_id
        ):
            raise ValueError("campaign collection reservation is not owned")
        if isinstance(current, dict):
            current["status"] = "aborted"
            current["finished_at"] = aborted_at
            current["abort"] = copy.deepcopy(abort)
        row["status"] = "aborted"
        row["aborted_at"] = aborted_at
        row["abort"] = copy.deepcopy(abort)

    saved = _CAMPAIGNS.update(
        campaign["id"],
        _abort,
        expected_revision=expected_revision,
        action="abort_campaign",
        actor=actor,
    )
    if saved is None:  # pragma: no cover
        raise RuntimeError("campaign disappeared during abort")
    if attempt_id:
        _abort_attempt_drills(
            campaign["id"],
            attempt_id,
            actor=actor,
            reason=reason,
            now=aborted_at,
            recovery=False,
        )
    return saved


def _overall_status(results: Iterable[Mapping[str, Any]]) -> str:
    statuses = [str(result.get("status") or "") for result in results]
    if not statuses:
        return NOT_RUN
    for status in (ERROR, FAIL, INSUFFICIENT_DATA, NOT_RUN):
        if status in statuses:
            return status
    return PASS if all(status == PASS for status in statuses) else ERROR


def _manifest_from_campaign(campaign: Mapping[str, Any], *, issued_at: float) -> dict[str, Any]:
    results = copy.deepcopy(list(campaign.get("results") or []))
    overall = _overall_status(results)
    counts = {status: sum(result.get("status") == status for result in results) for status in sorted(STATUSES)}
    payload: dict[str, Any] = {
        "kind": "maverick-production-evidence-campaign",
        "schema": _SCHEMA,
        "version": 1,
        "campaign_id": str(campaign["id"]),
        "title": str(campaign["title"]),
        "started_at": float(campaign["started_at"]),
        "issued_at": float(issued_at),
        "provenance": copy.deepcopy(campaign["provenance"]),
        "objectives": copy.deepcopy(campaign["objectives"]),
        "controls": list(campaign.get("controls") or []),
        "collector_modes": sorted({str(result.get("mode")) for result in results}),
        "status_vocabulary": sorted(STATUSES),
        "results": results,
        "result_counts": counts,
        "overall_status": overall,
        "passed": overall == PASS,
        "witnessed_drills": sum(
            bool(result.get("drill_id") and result.get("witness")) for result in results
        ),
    }
    payload["payload_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    from .proof_pack import sign

    return sign(payload)


def finalize_campaign(
    campaign_id: str,
    *,
    expected_revision: int,
    actor: str,
    now: float | None = None,
    ingest_graph: bool = True,
) -> dict[str, Any]:
    """Finalize, attempt signing, and optionally bind a pending-review node."""
    _require_enabled()
    campaign = _CAMPAIGNS.get(str(campaign_id or ""))
    if campaign is None:
        raise KeyError(campaign_id)
    _assert_expected_revision(campaign, expected_revision)
    if campaign.get("status") != "collected":
        raise ValueError("campaign must be collected before finalization")
    issued = time.time() if now is None else float(now)
    if not math.isfinite(issued) or issued <= 0:
        raise ValueError("manifest issue time must be a positive timestamp")
    manifest = _manifest_from_campaign(campaign, issued_at=issued)

    def _finalize(row: dict[str, Any]) -> None:
        if row.get("status") != "collected":
            raise ValueError("campaign must be collected before finalization")
        row["status"] = "finalized"
        row["finalized_at"] = issued
        row["manifest"] = manifest
        row["manifest_sha256"] = manifest["payload_sha256"]
        row["signature_status"] = "signed" if manifest.get("signature") else "unsigned"

    finalized = _CAMPAIGNS.update(
        campaign["id"],
        _finalize,
        expected_revision=expected_revision,
        action="finalize_campaign",
        actor=actor,
    )
    if finalized is None:  # pragma: no cover
        raise RuntimeError("campaign disappeared during finalization")

    if not ingest_graph:
        return finalized
    try:
        from . import evidence_graph

        if not evidence_graph.enabled():
            return finalized
        node = ingest_campaign(finalized, actor=actor)
        graph_result = {"status": "ingested", "node_id": node["id"]}
    except Exception as exc:  # graph projection must not invalidate the manifest
        graph_result = {
            "status": ERROR,
            "error_type": type(exc).__name__,
            "detail": _redact_text(str(exc)),
        }

    def _bind(row: dict[str, Any]) -> None:
        row["evidence_graph"] = graph_result
        row["evidence_graph_node_id"] = str(graph_result.get("node_id") or "")

    bound = _CAMPAIGNS.update(
        finalized["id"],
        _bind,
        expected_revision=finalized["revision"],
        action="bind_evidence_graph",
        actor=actor,
    )
    return finalized if bound is None else bound


def ingest_campaign(campaign: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    """Project a finalized campaign into the review-gated evidence graph."""
    manifest = campaign.get("manifest")
    if campaign.get("status") != "finalized" or not isinstance(manifest, dict):
        raise ValueError("only a finalized campaign can be ingested")
    from . import evidence_graph

    signed = isinstance(manifest.get("signature"), dict)

    return evidence_graph.ingest(
        source="evidence_factory",
        source_id=str(campaign["id"]),
        evidence_type="production_evidence_campaign",
        title=str(campaign["title"]),
        summary=(
            f"{'Signed' if signed else 'Unsigned'} production evidence campaign; "
            "overall status "
            f"{manifest.get('overall_status')}. Human review is still required."
        ),
        controls=list(campaign.get("controls") or []),
        attributes={
            "manifest_sha256": str(manifest.get("payload_sha256") or ""),
            "overall_status": str(manifest.get("overall_status") or ""),
            "collector_count": len(manifest.get("results") or []),
            "passed": manifest.get("passed") is True,
            "signed": signed,
            "witnessed_drills": int(manifest.get("witnessed_drills") or 0),
            "modes": list(manifest.get("collector_modes") or []),
        },
        links=[{
            "relation": "projects",
            "target_type": "evidence_campaign",
            "target_id": str(campaign["id"]),
        }],
        actor=actor,
        observed_at=float(manifest["issued_at"]),
    )


def verify_manifest(
    manifest: Mapping[str, Any],
    *,
    trusted_pubkey_hex: str | None,
) -> tuple[bool, str]:
    """Verify structure, canonical payload hash, verdict and trusted signature."""
    if not isinstance(manifest, Mapping):
        return False, "manifest must be an object"
    document = copy.deepcopy(dict(manifest))
    if document.get("schema") != _SCHEMA or document.get("version") != 1:
        return False, "unsupported evidence manifest schema"
    provenance = document.get("provenance")
    objectives = document.get("objectives")
    if not isinstance(provenance, dict) or not isinstance(objectives, dict):
        return False, "manifest provenance/objectives are missing"
    try:
        Provenance(
            commit=provenance.get("commit", ""),
            image_digest=provenance.get("image_digest", ""),
            config_sha256=provenance.get("config_sha256", ""),
            deployment_id=provenance.get("deployment_id", ""),
            migrations=provenance.get("migrations") or {},
            dependencies=provenance.get("dependencies") or {},
        ).to_dict()
        ServiceObjectives(
            slo=objectives.get("slo") or {},
            rto_seconds=objectives.get("rto_seconds"),
            rpo_seconds=objectives.get("rpo_seconds"),
        ).to_dict()
    except (TypeError, ValueError) as exc:
        return False, f"manifest provenance/objectives are invalid: {exc}"
    results = document.get("results")
    if not isinstance(results, list):
        return False, "manifest results must be a list"
    for result in results:
        if not isinstance(result, dict):
            return False, "manifest contains a malformed collector result"
        if result.get("status") not in STATUSES or result.get("mode") not in MODES:
            return False, "manifest contains an invalid collector status or mode"
        if not _NAME_RE.fullmatch(str(result.get("name") or "")):
            return False, "manifest contains an invalid collector name"
        if (
            result.get("mode") == "operator_attested"
            and result.get("status") != NOT_RUN
            and not isinstance(result.get("witness"), dict)
        ):
            return False, "operator-attested collector is missing its witness"
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list):
            return False, "manifest collector artifacts must be a list"
        for artifact in artifacts:
            if (
                not isinstance(artifact, dict)
                or not _SHA256_RE.fullmatch(str(artifact.get("sha256") or ""))
                or not isinstance(artifact.get("size_bytes"), int)
            ):
                return False, "manifest contains invalid artifact metadata"
    overall = _overall_status(results)
    if document.get("overall_status") != overall or document.get("passed") is not (overall == PASS):
        return False, "manifest overall verdict is inconsistent with collector results"
    expected_counts = {
        status: sum(result.get("status") == status for result in results)
        for status in sorted(STATUSES)
    }
    if document.get("result_counts") != expected_counts:
        return False, "manifest result counts are inconsistent"
    expected_modes = sorted({str(result.get("mode")) for result in results})
    if document.get("collector_modes") != expected_modes:
        return False, "manifest collector modes are inconsistent"
    witnessed = sum(
        bool(result.get("drill_id") and result.get("witness")) for result in results
    )
    if document.get("witnessed_drills") != witnessed:
        return False, "manifest witnessed-drill count is inconsistent"
    supplied_hash = str(document.get("payload_sha256") or "")
    unsigned_payload = {
        key: value
        for key, value in document.items()
        if key not in {"signature", "payload_sha256"}
    }
    expected_hash = hashlib.sha256(_canonical(unsigned_payload)).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected_hash):
        return False, "manifest canonical payload hash does not match"
    from .proof_pack import verify

    return verify(document, trusted_pubkey_hex=trusted_pubkey_hex)


def get_campaign(campaign_id: str) -> dict[str, Any] | None:
    return _CAMPAIGNS.get(str(campaign_id or ""))


def list_campaigns() -> list[dict[str, Any]]:
    return _CAMPAIGNS.list()


def get_drill(drill_id: str) -> dict[str, Any] | None:
    return _DRILLS.get(str(drill_id or ""))


def list_drills(*, campaign_id: str | None = None) -> list[dict[str, Any]]:
    rows = _DRILLS.list()
    if campaign_id is None:
        return rows
    return [row for row in rows if row.get("campaign_id") == campaign_id]


def _json_artifact(name: str, value: object) -> ArtifactInput:
    return ArtifactInput(
        name=name,
        content=_canonical(_redact_tree(value)),
        media_type="application/json",
    )


def default_collectors(
    *,
    readiness_probe: Callable[[], object] | None = None,
    audit_probe: Callable[[], object] | None = None,
    migration_probe: Callable[[], object] | None = None,
    reliability_probe: Callable[[], object] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[CollectorSpec, ...]:
    """Compose safe existing checks; probes remain injectable for tests/embedders."""

    def _readiness(_context: CollectorContext) -> CollectorOutcome:
        probe = readiness_probe
        if probe is None:
            from .deployment import verify_deployment

            probe = verify_deployment
        raw = probe()
        if not isinstance(raw, (list, tuple)) or not raw:
            return CollectorOutcome(
                INSUFFICIENT_DATA,
                "readiness probe produced no checks",
                data={"checks": []},
            )
        checks = []
        for item in raw:
            if isinstance(item, Mapping):
                checks.append({
                    "name": str(item.get("name", "")),
                    "passed": item.get("passed"),
                    "detail": str(item.get("detail", "")),
                })
            else:
                checks.append({
                    "name": str(getattr(item, "name", "")),
                    "passed": getattr(item, "passed", None),
                    "detail": str(getattr(item, "detail", "")),
                })
        if any(not isinstance(item["passed"], bool) for item in checks):
            return CollectorOutcome(ERROR, "readiness probe returned an invalid verdict", data={"checks": checks})
        passed = all(item["passed"] for item in checks)
        return CollectorOutcome(
            PASS if passed else FAIL,
            f"{sum(item['passed'] for item in checks)}/{len(checks)} readiness guarantees passed",
            data={"checks": checks},
            artifacts=(_json_artifact("readiness.json", {"checks": checks}),),
        )

    def _audit(_context: CollectorContext) -> CollectorOutcome:
        probe = audit_probe
        if probe is None:
            from .soc2 import collect_soc2_evidence

            def probe():
                return collect_soc2_evidence().get("audit_log", {})
        raw = probe()
        if not isinstance(raw, Mapping):
            return CollectorOutcome(ERROR, "audit probe returned an invalid result")
        status = str(raw.get("status") or "unknown")
        verdict = {
            "ok": PASS,
            "broken": FAIL,
            "empty": INSUFFICIENT_DATA,
            "unsigned": INSUFFICIENT_DATA,
            "no_crypto": INSUFFICIENT_DATA,
        }.get(status, ERROR)
        return CollectorOutcome(
            verdict,
            f"audit chain status: {status}",
            data=dict(raw),
            artifacts=(_json_artifact("audit-chain.json", raw),),
        )

    def _migration(_context: CollectorContext) -> CollectorOutcome:
        probe = migration_probe
        if probe is None:
            from .migration_governance import validate

            probe = validate
        raw = probe()
        if not isinstance(raw, (list, tuple)):
            return CollectorOutcome(ERROR, "migration governance returned an invalid result")
        problems = [str(item) for item in raw]
        return CollectorOutcome(
            FAIL if problems else PASS,
            f"migration governance found {len(problems)} problem(s)",
            data={"problems": problems},
            artifacts=(_json_artifact("migration-governance.json", {"problems": problems}),),
        )

    def _reliability(_context: CollectorContext) -> CollectorOutcome:
        probe = reliability_probe
        if probe is None:
            from .reliability_cert import certify

            probe = certify
        raw = probe()
        if not isinstance(raw, Mapping) or not isinstance(raw.get("checks"), Mapping):
            return CollectorOutcome(ERROR, "reliability certification returned an invalid result")
        checks = dict(raw["checks"])
        if not checks:
            return CollectorOutcome(
                INSUFFICIENT_DATA,
                "reliability certification ran no checks",
                data=dict(raw),
            )
        verdicts = [row.get("passed") for row in checks.values() if isinstance(row, Mapping)]
        if len(verdicts) != len(checks) or any(not isinstance(value, bool) for value in verdicts):
            return CollectorOutcome(ERROR, "reliability checks contain an invalid verdict", data=dict(raw))
        passed = all(verdicts)
        return CollectorOutcome(
            PASS if passed else FAIL,
            f"{sum(verdicts)}/{len(verdicts)} reliability checks passed",
            data=dict(raw),
            artifacts=(_json_artifact("reliability-cert.json", raw),),
        )

    return (
        CollectorSpec("readiness", "integration", _readiness, timeout_seconds),
        CollectorSpec("audit_chain", "integration", _audit, timeout_seconds),
        CollectorSpec("migration_governance", "offline", _migration, timeout_seconds),
        CollectorSpec("reliability", "integration", _reliability, timeout_seconds),
    )


__all__ = [
    "ArtifactInput",
    "CollectorContext",
    "CollectorOutcome",
    "CollectorSpec",
    "ERROR",
    "EvidenceFactoryDisabled",
    "FAIL",
    "INSUFFICIENT_DATA",
    "MODES",
    "NOT_RUN",
    "OperatorWitness",
    "PASS",
    "Provenance",
    "STATUSES",
    "ServiceObjectives",
    "abort_campaign",
    "abort_drill",
    "build_provenance",
    "collect_campaign",
    "config_fingerprint",
    "default_collectors",
    "dependency_versions",
    "enabled",
    "finalize_campaign",
    "get_campaign",
    "get_drill",
    "ingest_campaign",
    "list_campaigns",
    "list_drills",
    "recover_stale_campaign",
    "recover_stale_drill",
    "start_campaign",
    "verify_manifest",
]
