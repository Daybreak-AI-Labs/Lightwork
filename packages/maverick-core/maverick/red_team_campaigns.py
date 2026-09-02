"""Consent-gated red-team campaign product foundation.

This module turns the local Shield corpus into a durable, scheduled campaign
surface for *explicitly configured* target adapters.  It deliberately does not
discover targets, make a network connection by itself, or retain a prompt or a
target response.  An operator-owned adapter receives one synthetic corpus case
in memory and returns a tightly bounded decision; durable records retain only
case identifiers, corpus/adapter digests, counts, and drift deltas.

It complements :mod:`maverick.red_team_assurance`, which governs pull-based
external-agent campaigns.  This module covers opt-in deployment adapters such
as a Shield pack or a proxied MCP upstream.  A real adapter must still provide
its own authenticated transport, target-side rate limits, authorization,
idempotency for the invocation ID, and a separately reviewed live-deployment
runbook.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

CAMPAIGN_SPEC_SCHEMA = "lightwork.red-team-campaign-spec.v1"
CAMPAIGN_RUN_SCHEMA = "lightwork.red-team-campaign-run.v1"
CAMPAIGN_REPORT_SCHEMA = "lightwork.red-team-campaign-report.v1"
CAMPAIGN_DRIFT_SCHEMA = "lightwork.red-team-campaign-drift.v1"
CAMPAIGN_NOTICE = (
    "This bounded red-team campaign is technical assurance evidence only. It "
    "does not establish security, safety, compliance, certification, or a "
    "legal conclusion."
)

_SPECS = GovernedRecordStore(
    "red_team_campaign_product_specs", "RTCS", "red_team_campaign_spec",
)
_RUNS = GovernedRecordStore(
    "red_team_campaign_product_runs", "RTCR", "red_team_campaign_run",
)
_REPORTS = GovernedRecordStore(
    "red_team_campaign_product_reports", "RTCP", "red_team_campaign_report",
)
_DRIFTS = GovernedRecordStore(
    "red_team_campaign_product_drifts", "RTCD", "red_team_campaign_drift",
)

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")
_DIGEST_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z", re.IGNORECASE)
_TARGET_KINDS = frozenset({"shield_pack", "mcp_upstream"})
_ENVIRONMENTS = frozenset({"consenting_test", "development", "sandbox", "staging", "production"})
_TARGET_DECISIONS = frozenset({"blocked", "allowed", "inconclusive"})
_NON_CONTENT_SIGNAL_CODES = frozenset(
    {
        "adapter_exception",
        "retry_exhausted",
        "shield_allowed",
        "shield_blocked",
        "target_allowed",
        "target_blocked",
        "target_inconclusive",
    }
)
_SPEC_STATUSES = frozenset({"active", "paused"})
_RUN_STATUSES = frozenset({"running", "completed"})
_MAX_CASES = 512
_MAX_SPECS = 2_000
_MAX_RUNS = 20_000
_MAX_REPORTS = 20_000
_MAX_DRIFTS = 20_000
_MAX_CADENCE_SECONDS = 366 * 24 * 60 * 60
_MIN_CADENCE_SECONDS = 60
_MAX_HORIZON_SECONDS = 366 * 24 * 60 * 60
_MIN_LEASE_SECONDS = 30
_MAX_LEASE_SECONDS = 15 * 60
_MAX_ATTEMPTS = 8


class RedTeamCampaignError(RuntimeError):
    """Base failure for the consent-gated campaign product."""


class RedTeamCampaignConsentError(RedTeamCampaignError):
    """A target is not presently covered by explicit adapter consent."""


class RedTeamCampaignStateError(RedTeamCampaignError):
    """A durable campaign binding, lease, or lifecycle state is invalid."""


class RedTeamCampaignBusy(RedTeamCampaignStateError):
    """Another worker has an unexpired lease for the same scheduled run."""


class RedTeamCampaignRetryableError(RedTeamCampaignError):
    """A target adapter reported a transient, retry-safe condition."""

    def __init__(self, code: str = "retryable_target_error") -> None:
        normalized = _code(code, "retryable error code")
        super().__init__(normalized)
        self.code = normalized


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, label: str, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise ValueError(f"{label} is invalid")
    return text


def _prompt_text(value: object, label: str, limit: int = 32_768) -> str:
    """Validate an in-memory synthetic prompt without normalizing its bytes.

    Corpus prompts may intentionally contain newlines or tabs to exercise
    delimiter handling.  They are never durable campaign fields, but their
    exact text must remain available to the adapter and corpus digest.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} is invalid")
    if any(ord(char) < 32 and char not in {"\n", "\r", "\t"} for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _identity(value: object, label: str, limit: int = 128) -> str:
    text = _text(value, label, limit)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _code(value: object, label: str) -> str:
    text = _text(value, label, 64).lower()
    if not _CODE_RE.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _digest(value: object, label: str) -> str:
    text = _text(value, label, 71).lower()
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{label} must be a SHA-256 digest")
    return text.removeprefix("sha256:")


def _number(value: object, label: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{label} is outside its allowed range")
    return result


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _record_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_sha256([str(part) for part in parts])[:24]}"


def _next_scheduled_time(scheduled_for: float, cadence_seconds: int, now: float) -> float:
    elapsed = max(0.0, now - scheduled_for)
    return scheduled_for + (int(elapsed // cadence_seconds) + 1) * cadence_seconds


@dataclass(frozen=True)
class RedTeamCorpusCase:
    """One synthetic corpus case retained only in process memory."""

    case_id: str
    text: str
    expected: str
    category: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identity(self.case_id, "corpus case id"))
        object.__setattr__(self, "text", _prompt_text(self.text, "corpus case text"))
        expected = _code(self.expected, "corpus case expected")
        if expected not in {"block", "allow"}:
            raise ValueError("corpus case expected must be block or allow")
        object.__setattr__(self, "expected", expected)
        object.__setattr__(self, "category", _code(self.category, "corpus case category"))


@dataclass(frozen=True)
class RedTeamCorpus:
    """A fixed corpus version; its raw prompt text never reaches durable state."""

    name: str
    version: str
    cases: tuple[RedTeamCorpusCase, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "corpus name"))
        object.__setattr__(self, "version", _text(self.version, "corpus version", 128))
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("corpus cases must be a non-empty tuple")
        if len(self.cases) > _MAX_CASES or not all(
            isinstance(case, RedTeamCorpusCase) for case in self.cases
        ):
            raise ValueError("corpus cases are invalid")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("corpus case identifiers must be unique")

    @property
    def sha256(self) -> str:
        # This binds the actual synthetic prompt bytes, but only the resulting
        # digest (never the prompt text) is stored in specifications and reports.
        return _sha256(
            {
                "name": self.name,
                "version": self.version,
                "cases": [
                    {
                        "id": case.case_id,
                        "text": case.text,
                        "expected": case.expected,
                        "category": case.category,
                    }
                    for case in self.cases
                ],
            }
        )


@dataclass(frozen=True)
class RedTeamInvocation:
    """Ephemeral adapter call; ``prompt`` must not be persisted by adapters."""

    campaign_spec_id: str
    run_id: str
    invocation_id: str
    case_id: str
    prompt: str
    expected: str
    category: str


@dataclass(frozen=True)
class RedTeamTargetResult:
    """Closed adapter result: a decision and fixed non-content status codes."""

    decision: str
    signal_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        decision = _code(self.decision, "target decision")
        if decision not in _TARGET_DECISIONS:
            raise ValueError("target decision is unsupported")
        if not isinstance(self.signal_codes, tuple) or len(self.signal_codes) > 16:
            raise ValueError("target signal codes must be a bounded tuple")
        codes = tuple(_code(code, "target signal code") for code in self.signal_codes)
        if len(codes) != len(set(codes)):
            raise ValueError("target signal codes must be unique")
        if not set(codes).issubset(_NON_CONTENT_SIGNAL_CODES):
            raise ValueError("target signal codes must be fixed non-content status codes")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "signal_codes", tuple(sorted(codes)))


TargetInvoker = Callable[[RedTeamInvocation], RedTeamTargetResult]


@dataclass(frozen=True)
class RedTeamTargetAdapter:
    """An explicit, operator-configured campaign target.

    Credentials/endpoints remain inside ``invoke`` and are intentionally not
    fields on this object or in any durable campaign binding.
    """

    adapter_id: str
    target_kind: str
    environment: str
    configuration_sha256: str
    consent_reference_sha256: str
    consent_confirmed: bool
    invoke: TargetInvoker

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _identity(self.adapter_id, "adapter id"))
        kind = _code(self.target_kind, "target kind")
        if kind not in _TARGET_KINDS:
            raise ValueError("target kind must be shield_pack or mcp_upstream")
        environment = _code(self.environment, "target environment")
        if environment not in _ENVIRONMENTS:
            raise ValueError("target environment is unsupported")
        if not isinstance(self.consent_confirmed, bool):
            raise ValueError("target consent_confirmed must be a boolean")
        if not callable(self.invoke):
            raise TypeError("target invoke must be callable")
        object.__setattr__(self, "target_kind", kind)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(
            self,
            "configuration_sha256",
            _digest(self.configuration_sha256, "target configuration digest"),
        )
        object.__setattr__(
            self,
            "consent_reference_sha256",
            _digest(self.consent_reference_sha256, "target consent reference digest"),
        )

    def binding(self) -> dict[str, Any]:
        """Closed durable binding with no endpoint, credential, or prompt."""
        return {
            "adapter_id": self.adapter_id,
            "target_kind": self.target_kind,
            "environment": self.environment,
            "configuration_sha256": self.configuration_sha256,
            "consent_reference_sha256": self.consent_reference_sha256,
            "consent_confirmed": self.consent_confirmed,
        }


def make_shield_pack_adapter(
    *,
    adapter_id: str,
    environment: str,
    configuration_sha256: str,
    consent_reference_sha256: str,
    consent_confirmed: bool,
    block_threshold: str = "high",
) -> RedTeamTargetAdapter:
    """Build a local Shield-pack adapter without any outbound transport.

    This is useful for a deployment's installed Shield policy pack. It does
    not exercise a remote model or MCP server; an MCP upstream must be supplied
    through an explicit operator-owned :class:`RedTeamTargetAdapter`.
    """
    threshold = _code(block_threshold, "shield block threshold")
    if threshold not in {"low", "medium", "high", "critical"}:
        raise ValueError("shield block threshold is unsupported")

    def invoke(invocation: RedTeamInvocation) -> RedTeamTargetResult:
        try:
            from maverick_shield.builtin_rules import scan
        except ImportError as exc:  # pragma: no cover - optional dependency seam
            raise RedTeamCampaignStateError("maverick-shield is unavailable") from exc
        blocked, _severity, names = scan(invocation.prompt, block_threshold=threshold)
        # Do not relay rule labels into a report: a custom label could embed
        # prompt content.  The fixed status code is enough to show that the
        # local Shield pack made the decision without retaining raw input or
        # raw detector output.
        del names
        return RedTeamTargetResult(
            "blocked" if blocked else "allowed",
            ("shield_blocked",) if blocked else ("shield_allowed",),
        )

    return RedTeamTargetAdapter(
        adapter_id=adapter_id,
        target_kind="shield_pack",
        environment=environment,
        configuration_sha256=configuration_sha256,
        consent_reference_sha256=consent_reference_sha256,
        consent_confirmed=consent_confirmed,
        invoke=invoke,
    )


def load_shield_corpus() -> RedTeamCorpus:
    """Load the released Shield corpus as synthetic, in-memory campaign cases."""
    try:
        from maverick_shield.corpus_release import build_release
        from maverick_shield.redteam import load_corpus
    except ImportError as exc:  # pragma: no cover - optional dependency seam
        raise RedTeamCampaignStateError("maverick-shield corpus is unavailable") from exc
    try:
        release = build_release()
        cases = tuple(
            RedTeamCorpusCase(
                case_id=case.id,
                text=case.text,
                expected=case.expected,
                category=case.category or "uncategorized",
            )
            for case in load_corpus()
        )
    except Exception as exc:
        raise RedTeamCampaignStateError("maverick-shield corpus release is invalid") from exc
    return RedTeamCorpus(
        name="maverick-shield-redteam",
        version=_text(str(release.get("version") or ""), "shield corpus version", 128),
        cases=cases,
    )


class RedTeamCampaignService:
    """Durable campaign spec/run/report/drift service with leased due runs."""

    def __init__(
        self,
        *,
        target_adapters: Sequence[RedTeamTargetAdapter] = (),
        corpus_loader: Callable[[], RedTeamCorpus] | None = None,
        specs: GovernedRecordStore | None = None,
        runs: GovernedRecordStore | None = None,
        reports: GovernedRecordStore | None = None,
        drifts: GovernedRecordStore | None = None,
        clock: Callable[[], float] | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> None:
        if isinstance(target_adapters, (str, bytes)) or not isinstance(
            target_adapters, Sequence
        ):
            raise ValueError("target_adapters must be a sequence")
        if corpus_loader is not None and not callable(corpus_loader):
            raise TypeError("corpus_loader must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._specs = specs or _SPECS
        self._runs = runs or _RUNS
        self._reports = reports or _REPORTS
        self._drifts = drifts or _DRIFTS
        self._corpus_loader = corpus_loader or load_shield_corpus
        self._clock = clock
        self._lease_seconds = _integer(
            lease_seconds, "lease_seconds", _MIN_LEASE_SECONDS, _MAX_LEASE_SECONDS
        )
        self._max_attempts = _integer(
            max_attempts, "max_attempts", 1, _MAX_ATTEMPTS
        )
        self._adapter_lock = threading.RLock()
        self._adapters: dict[str, RedTeamTargetAdapter] = {}
        for adapter in target_adapters:
            self.register_target_adapter(adapter)

    def _now(self) -> float:
        source = self._clock or self._specs.authoritative_time
        return _number(source(), "authority clock", 0.000001, 100_000_000_000.0)

    def register_target_adapter(self, adapter: RedTeamTargetAdapter) -> None:
        """Register or replace an explicit target adapter in this process."""
        if not isinstance(adapter, RedTeamTargetAdapter):
            raise TypeError("adapter must be a RedTeamTargetAdapter")
        with self._adapter_lock:
            self._adapters[adapter.adapter_id] = adapter

    def revoke_target_adapter(self, adapter_id: str) -> bool:
        """Remove a target adapter; subsequent campaign execution refuses it."""
        identifier = _identity(adapter_id, "adapter id")
        with self._adapter_lock:
            return self._adapters.pop(identifier, None) is not None

    def _adapter(self, adapter_id: str) -> RedTeamTargetAdapter:
        identifier = _identity(adapter_id, "adapter id")
        with self._adapter_lock:
            adapter = self._adapters.get(identifier)
        if adapter is None:
            raise RedTeamCampaignConsentError("target adapter is not explicitly configured")
        if adapter.consent_confirmed is not True:
            raise RedTeamCampaignConsentError("target adapter consent is not confirmed")
        return adapter

    def _assert_target_binding(self, spec: Mapping[str, Any]) -> RedTeamTargetAdapter:
        binding = spec.get("target_binding")
        if not isinstance(binding, Mapping):
            raise RedTeamCampaignStateError("campaign target binding is invalid")
        adapter = self._adapter(str(binding.get("adapter_id") or ""))
        if dict(binding) != adapter.binding():
            raise RedTeamCampaignConsentError(
                "target adapter configuration or consent changed; create a new campaign spec"
            )
        return adapter

    def _corpus(self) -> RedTeamCorpus:
        corpus = self._corpus_loader()
        if not isinstance(corpus, RedTeamCorpus):
            raise RedTeamCampaignStateError("corpus loader must return a RedTeamCorpus")
        return corpus

    @staticmethod
    def _spec_id(idempotency_key: str) -> str:
        return _record_id("RTCS", idempotency_key)

    @staticmethod
    def _run_id(spec_id: str, scheduled_for: float, spec_sha256: str) -> str:
        return _record_id("RTCR", spec_id, f"{scheduled_for:.6f}", spec_sha256)

    @staticmethod
    def _report_id(run_id: str) -> str:
        return _record_id("RTCP", run_id)

    @staticmethod
    def _drift_id(report_id: str) -> str:
        return _record_id("RTCD", report_id)

    @staticmethod
    def _corpus_binding(corpus: RedTeamCorpus, cases: Sequence[RedTeamCorpusCase]) -> dict[str, Any]:
        return {
            "name": corpus.name,
            "version": corpus.version,
            "corpus_sha256": corpus.sha256,
            "case_count": len(cases),
            "cases": [
                {
                    "case_id": case.case_id,
                    "expected": case.expected,
                    "category": case.category,
                }
                for case in sorted(cases, key=lambda item: item.case_id)
            ],
        }

    @staticmethod
    def _selected_cases(
        corpus: RedTeamCorpus,
        case_ids: Sequence[str] | None,
    ) -> tuple[RedTeamCorpusCase, ...]:
        available = {case.case_id: case for case in corpus.cases}
        if case_ids is None:
            selected = set(available)
        else:
            if isinstance(case_ids, (str, bytes)) or not isinstance(case_ids, Sequence):
                raise ValueError("case_ids must be a sequence")
            if len(case_ids) > _MAX_CASES:
                raise ValueError("case_ids exceeds the corpus case limit")
            selected = {_identity(case_id, "case id") for case_id in case_ids}
            if len(selected) != len(case_ids):
                raise ValueError("case_ids must be unique")
        if not selected:
            raise ValueError("at least one corpus case is required")
        unknown = sorted(selected.difference(available))
        if unknown:
            raise ValueError("case_ids contains an unknown corpus case")
        return tuple(sorted((available[case_id] for case_id in selected), key=lambda case: case.case_id))

    def create_campaign_spec(
        self,
        *,
        target_adapter_id: str,
        idempotency_key: str,
        actor: str,
        cadence_seconds: int,
        first_run_at: float | None = None,
        case_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Create one consent-pinned recurring campaign specification.

        Only digests, corpus case metadata, and the adapter binding are stored.
        The selected synthetic prompt text remains in the corpus loader process.
        """
        adapter = self._adapter(target_adapter_id)
        operator = _identity(actor, "actor")
        key = _identity(idempotency_key, "idempotency_key")
        cadence = _integer(
            cadence_seconds, "cadence_seconds", _MIN_CADENCE_SECONDS, _MAX_CADENCE_SECONDS
        )
        now = self._now()
        first = now if first_run_at is None else _number(
            first_run_at, "first_run_at", 0.000001, now + _MAX_HORIZON_SECONDS
        )
        corpus = self._corpus()
        selected_cases = self._selected_cases(corpus, case_ids)
        immutable = {
            "idempotency_key_sha256": _sha256(key),
            "target_binding": adapter.binding(),
            "corpus_binding": self._corpus_binding(corpus, selected_cases),
            "cadence_seconds": cadence,
            "first_run_at": first,
            "max_attempts": self._max_attempts,
            "lease_seconds": self._lease_seconds,
            "notice": CAMPAIGN_NOTICE,
            "legal_certification": False,
        }
        request_sha = _sha256(immutable)
        spec_id = self._spec_id(key)
        record = {
            "id": spec_id,
            "schema": CAMPAIGN_SPEC_SCHEMA,
            **immutable,
            "request_sha256": request_sha,
            "spec_sha256": _sha256(immutable),
            "status": "active",
            "next_run_at": first,
            "pending_run": None,
            "last_run_id": None,
            "last_report_id": None,
            "last_drift_id": None,
            "last_completed_at": None,
            "run_count": 0,
        }
        try:
            return self._specs.create(
                record,
                action="create_red_team_campaign_spec",
                actor=operator,
                max_records=_MAX_SPECS,
            )
        except RecordConflict as exc:
            current = self._specs.get(spec_id)
            if current is not None and current.get("request_sha256") == request_sha:
                return current
            raise RecordConflict(
                "campaign idempotency key is already bound to another specification"
            ) from exc

    def _raw_spec(self, spec_id: str) -> dict[str, Any] | None:
        identifier = _identity(spec_id, "campaign spec id", 64)
        row = self._specs.get(identifier)
        if row is not None:
            if row.get("schema") != CAMPAIGN_SPEC_SCHEMA:
                raise RedTeamCampaignStateError("campaign specification schema is invalid")
            if row.get("status") not in _SPEC_STATUSES:
                raise RedTeamCampaignStateError("campaign specification status is invalid")
        return row

    @staticmethod
    def _spec_view(row: Mapping[str, Any]) -> dict[str, Any]:
        pending = row.get("pending_run")
        return {
            "id": row.get("id"),
            "revision": row.get("revision"),
            "status": row.get("status"),
            "target_binding": row.get("target_binding"),
            "corpus_binding": row.get("corpus_binding"),
            "cadence_seconds": row.get("cadence_seconds"),
            "next_run_at": row.get("next_run_at"),
            "last_run_id": row.get("last_run_id"),
            "last_report_id": row.get("last_report_id"),
            "last_drift_id": row.get("last_drift_id"),
            "last_completed_at": row.get("last_completed_at"),
            "run_count": row.get("run_count"),
            "run_claimed": isinstance(pending, Mapping),
            "notice": row.get("notice"),
            "legal_certification": False,
        }

    def get_campaign_spec(self, spec_id: str) -> dict[str, Any] | None:
        row = self._raw_spec(spec_id)
        return None if row is None else self._spec_view(row)

    def list_campaign_specs(self, *, limit: int = 100) -> dict[str, Any]:
        maximum = _integer(limit, "limit", 1, 500)
        # The namespace is admission-bounded, so materialize its complete
        # closed metadata set before ordering.  Backend storage order is not a
        # schedule order (and may differ between local and shared backends).
        rows = self._specs.list(limit=_MAX_SPECS)
        ordered = sorted(
            rows,
            key=lambda row: (float(row.get("next_run_at") or math.inf), str(row.get("id") or "")),
        )
        return {
            "items": [self._spec_view(row) for row in ordered[:maximum]],
            "count": min(len(ordered), maximum),
            "truncated": len(ordered) > maximum,
            "limit": maximum,
        }

    def due_campaign_specs(self, *, limit: int = 100) -> dict[str, Any]:
        """List deterministically ordered due/expired-lease specs without claiming them."""
        maximum = _integer(limit, "limit", 1, 500)
        now = self._now()
        rows = []
        # Select from the full bounded namespace before applying the caller's
        # limit.  This keeps the first due item deterministic even once a
        # deployment has more than a thousand active campaign specifications.
        for row in self._specs.list(limit=_MAX_SPECS):
            if row.get("status") != "active":
                continue
            next_run = _number(row.get("next_run_at"), "next_run_at", 0.000001, 100_000_000_000.0)
            pending = row.get("pending_run")
            retryable = False
            if pending is not None:
                if not isinstance(pending, Mapping):
                    raise RedTeamCampaignStateError("campaign run claim is invalid")
                expiry = _number(
                    pending.get("lease_expires_at"),
                    "campaign run lease expiry",
                    0.000001,
                    100_000_000_000.0,
                )
                retryable = now >= expiry
            if now >= next_run and (pending is None or retryable):
                rows.append(row)
        ordered = sorted(
            rows,
            key=lambda row: (float(row["next_run_at"]), str(row["id"])),
        )
        return {
            "items": [self._spec_view(row) for row in ordered[:maximum]],
            "count": min(len(ordered), maximum),
            "truncated": len(ordered) > maximum,
            "limit": maximum,
            "selected_at": now,
        }

    def _claim_due_spec(
        self,
        spec_id: str,
        *,
        actor: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        operator = _identity(actor, "actor")
        identifier = _identity(spec_id, "campaign spec id", 64)
        for _attempt in range(4):
            spec = self._raw_spec(identifier)
            if spec is None:
                raise RedTeamCampaignStateError("campaign specification does not exist")
            if spec.get("status") != "active":
                raise RedTeamCampaignStateError("campaign specification is not active")
            self._assert_target_binding(spec)
            now = self._now()
            scheduled_for = _number(
                spec.get("next_run_at"), "next_run_at", 0.000001, 100_000_000_000.0
            )
            if now < scheduled_for:
                raise RedTeamCampaignStateError("campaign specification is not due")
            pending = spec.get("pending_run")
            if pending is not None:
                if not isinstance(pending, Mapping):
                    raise RedTeamCampaignStateError("campaign run claim is invalid")
                expiry = _number(
                    pending.get("lease_expires_at"),
                    "campaign run lease expiry",
                    0.000001,
                    100_000_000_000.0,
                )
                if now < expiry:
                    raise RedTeamCampaignBusy("campaign scheduled run is already leased")
            run_id = self._run_id(
                str(spec["id"]), scheduled_for, _digest(spec.get("spec_sha256"), "spec digest")
            )
            claim = {
                "run_id": run_id,
                "scheduled_for": scheduled_for,
                "claimed_by": operator,
                "claimed_at": now,
                "lease_expires_at": now + int(spec["lease_seconds"]),
                "spec_revision": int(spec.get("revision") or 0),
            }
            claim["claim_sha256"] = _sha256(claim)

            def reserve(
                row: dict[str, Any],
                expected_scheduled_for: float = scheduled_for,
                expected_pending: object = pending,
                value: Mapping[str, Any] = claim,
            ) -> None:
                if row.get("status") != "active":
                    raise RedTeamCampaignStateError("campaign specification is not active")
                if row.get("next_run_at") != expected_scheduled_for:
                    raise RecordConflict("campaign schedule advanced concurrently")
                if row.get("pending_run") != expected_pending:
                    raise RecordConflict("campaign run claim changed concurrently")
                row["pending_run"] = dict(value)

            try:
                saved = self._specs.update(
                    identifier,
                    reserve,
                    expected_revision=int(spec.get("revision") or 0),
                    action="claim_red_team_campaign_run",
                    actor=operator,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign specification disappeared during claim")
            return saved, claim
        raise RedTeamCampaignStateError("campaign specification changed concurrently; retry")

    @staticmethod
    def _assert_claim(spec: Mapping[str, Any], claim: Mapping[str, Any], now: float) -> None:
        pending = spec.get("pending_run")
        if not isinstance(pending, Mapping) or pending.get("claim_sha256") != claim.get("claim_sha256"):
            raise RedTeamCampaignStateError("campaign run lease authority changed")
        if now > _number(
            pending.get("lease_expires_at"), "campaign run lease expiry", 0.000001, 100_000_000_000.0
        ):
            raise RedTeamCampaignStateError("campaign run lease expired")

    def _ensure_run(self, spec: Mapping[str, Any], claim: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        run_id = _identity(claim.get("run_id"), "campaign run id", 64)
        immutable = {
            "campaign_spec_id": spec["id"],
            "campaign_spec_sha256": spec["spec_sha256"],
            "scheduled_for": claim["scheduled_for"],
            "target_binding": spec["target_binding"],
            "corpus_binding": spec["corpus_binding"],
            "max_attempts": spec["max_attempts"],
            "notice": CAMPAIGN_NOTICE,
            "legal_certification": False,
        }
        payload_sha = _sha256(immutable)
        record = {
            "id": run_id,
            "schema": CAMPAIGN_RUN_SCHEMA,
            **immutable,
            "payload_sha256": payload_sha,
            "status": "running",
            "case_outcomes": {},
            "attempt_counts": {},
            "report_id": None,
            "drift_id": None,
            "completed_at": None,
        }
        try:
            return self._runs.create(
                record,
                action="create_red_team_campaign_run",
                actor=actor,
                max_records=_MAX_RUNS,
            )
        except RecordConflict as exc:
            current = self._runs.get(run_id)
            if current is not None and current.get("payload_sha256") == payload_sha:
                return current
            raise RedTeamCampaignStateError(
                "scheduled run identity is bound to another campaign payload"
            ) from exc

    def _raw_run(self, run_id: str) -> dict[str, Any] | None:
        identifier = _identity(run_id, "campaign run id", 64)
        row = self._runs.get(identifier)
        if row is not None:
            if row.get("schema") != CAMPAIGN_RUN_SCHEMA:
                raise RedTeamCampaignStateError("campaign run schema is invalid")
            if row.get("status") not in _RUN_STATUSES:
                raise RedTeamCampaignStateError("campaign run status is invalid")
        return row

    @staticmethod
    def _run_view(row: Mapping[str, Any]) -> dict[str, Any]:
        outcomes = row.get("case_outcomes")
        if not isinstance(outcomes, Mapping):
            raise RedTeamCampaignStateError("campaign run outcomes are invalid")
        return {
            "id": row.get("id"),
            "revision": row.get("revision"),
            "status": row.get("status"),
            "campaign_spec_id": row.get("campaign_spec_id"),
            "scheduled_for": row.get("scheduled_for"),
            "target_binding": row.get("target_binding"),
            "corpus_binding": row.get("corpus_binding"),
            "case_outcomes": [outcomes[key] for key in sorted(outcomes)],
            "report_id": row.get("report_id"),
            "drift_id": row.get("drift_id"),
            "completed_at": row.get("completed_at"),
            "notice": row.get("notice"),
            "legal_certification": False,
        }

    def get_campaign_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._raw_run(run_id)
        return None if row is None else self._run_view(row)

    @staticmethod
    def _classify_case(
        case: RedTeamCorpusCase,
        result: RedTeamTargetResult,
        *,
        invocation_id: str,
        attempts: int,
    ) -> dict[str, Any]:
        if result.decision == "inconclusive":
            outcome = "inconclusive"
        elif case.expected == "block" and result.decision == "blocked":
            outcome = "blocked_as_expected"
        elif case.expected == "block":
            outcome = "missed_attack"
        elif result.decision == "blocked":
            outcome = "overblocked"
        else:
            outcome = "allowed_as_expected"
        return {
            "case_id": case.case_id,
            "expected": case.expected,
            "category": case.category,
            "target_decision": result.decision,
            "outcome": outcome,
            "signal_codes": list(result.signal_codes),
            "invocation_id": invocation_id,
            "attempts": attempts,
        }

    def _start_case_attempt(self, run_id: str, case_id: str, *, actor: str) -> int:
        identifier = _identity(run_id, "campaign run id", 64)
        case = _identity(case_id, "case id")
        for _attempt in range(4):
            run = self._raw_run(identifier)
            if run is None:
                raise RedTeamCampaignStateError("campaign run does not exist")
            outcomes = run.get("case_outcomes")
            counts = run.get("attempt_counts")
            if not isinstance(outcomes, Mapping) or not isinstance(counts, Mapping):
                raise RedTeamCampaignStateError("campaign run attempt state is invalid")
            if case in outcomes:
                return int(outcomes[case].get("attempts") or 0)
            current_count = int(counts.get(case) or 0)
            maximum = _integer(run.get("max_attempts"), "run max_attempts", 1, _MAX_ATTEMPTS)
            if current_count >= maximum:
                return current_count

            def mark_attempt(row: dict[str, Any], count: int = current_count) -> None:
                if row.get("status") != "running":
                    raise RedTeamCampaignStateError("campaign run is not running")
                current = row.get("attempt_counts")
                if not isinstance(current, Mapping) or int(current.get(case) or 0) != count:
                    raise RecordConflict("campaign run attempt count changed concurrently")
                row["attempt_counts"] = {**dict(current), case: count + 1}

            try:
                saved = self._runs.update(
                    identifier,
                    mark_attempt,
                    expected_revision=int(run.get("revision") or 0),
                    action="attempt_red_team_campaign_case",
                    actor=actor,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign run disappeared during attempt")
            return current_count + 1
        raise RedTeamCampaignStateError("campaign run attempt changed concurrently; retry")

    def _record_case_outcome(
        self,
        run_id: str,
        outcome: Mapping[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        identifier = _identity(run_id, "campaign run id", 64)
        case_id = _identity(outcome.get("case_id"), "case id")
        closed = {
            "case_id",
            "expected",
            "category",
            "target_decision",
            "outcome",
            "signal_codes",
            "invocation_id",
            "attempts",
        }
        if set(outcome) != closed:
            raise RedTeamCampaignStateError("campaign outcome fields are invalid")
        for _attempt in range(4):
            run = self._raw_run(identifier)
            if run is None:
                raise RedTeamCampaignStateError("campaign run does not exist")
            outcomes = run.get("case_outcomes")
            if not isinstance(outcomes, Mapping):
                raise RedTeamCampaignStateError("campaign run outcomes are invalid")
            existing = outcomes.get(case_id)
            if existing is not None:
                if existing == dict(outcome):
                    return run
                raise RedTeamCampaignStateError("campaign case already has another outcome")

            def commit(row: dict[str, Any], value: Mapping[str, Any] = outcome) -> None:
                if row.get("status") != "running":
                    raise RedTeamCampaignStateError("campaign run is not running")
                current = row.get("case_outcomes")
                if not isinstance(current, Mapping) or case_id in current:
                    raise RecordConflict("campaign case outcome changed concurrently")
                row["case_outcomes"] = {**dict(current), case_id: dict(value)}

            try:
                saved = self._runs.update(
                    identifier,
                    commit,
                    expected_revision=int(run.get("revision") or 0),
                    action="record_red_team_campaign_case",
                    actor=actor,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign run disappeared during result commit")
            return saved
        raise RedTeamCampaignStateError("campaign outcome changed concurrently; retry")

    @staticmethod
    def _planned_cases(spec: Mapping[str, Any], corpus: RedTeamCorpus) -> tuple[RedTeamCorpusCase, ...]:
        binding = spec.get("corpus_binding")
        if not isinstance(binding, Mapping):
            raise RedTeamCampaignStateError("campaign corpus binding is invalid")
        expected = RedTeamCampaignService._corpus_binding(corpus, corpus.cases)
        if (
            binding.get("name") != expected["name"]
            or binding.get("version") != expected["version"]
            or binding.get("corpus_sha256") != expected["corpus_sha256"]
        ):
            raise RedTeamCampaignStateError(
                "campaign corpus changed; create a new consent-reviewed specification"
            )
        manifest = binding.get("cases")
        if not isinstance(manifest, list) or not manifest:
            raise RedTeamCampaignStateError("campaign corpus case manifest is invalid")
        available = {case.case_id: case for case in corpus.cases}
        planned: list[RedTeamCorpusCase] = []
        for row in manifest:
            if not isinstance(row, Mapping):
                raise RedTeamCampaignStateError("campaign corpus case manifest is invalid")
            case_id = _identity(row.get("case_id"), "case id")
            case = available.get(case_id)
            if case is None or {
                "case_id": case.case_id,
                "expected": case.expected,
                "category": case.category,
            } != dict(row):
                raise RedTeamCampaignStateError("campaign corpus case binding changed")
            planned.append(case)
        return tuple(sorted(planned, key=lambda case: case.case_id))

    def _release_claim(self, spec_id: str, claim: Mapping[str, Any], *, actor: str) -> None:
        identifier = _identity(spec_id, "campaign spec id", 64)
        for _attempt in range(4):
            spec = self._raw_spec(identifier)
            if spec is None:
                raise RedTeamCampaignStateError("campaign specification does not exist")
            pending = spec.get("pending_run")
            if not isinstance(pending, Mapping) or pending.get("claim_sha256") != claim.get("claim_sha256"):
                return

            def release(row: dict[str, Any]) -> None:
                current = row.get("pending_run")
                if not isinstance(current, Mapping) or current.get("claim_sha256") != claim.get("claim_sha256"):
                    raise RecordConflict("campaign run lease authority changed")
                row["pending_run"] = None

            try:
                saved = self._specs.update(
                    identifier,
                    release,
                    expected_revision=int(spec.get("revision") or 0),
                    action="release_red_team_campaign_run",
                    actor=actor,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign specification disappeared during release")
            return
        raise RedTeamCampaignStateError("campaign run lease changed concurrently; retry")

    def _release_claim_after_abort(
        self,
        spec_id: str,
        claim: Mapping[str, Any],
        *,
        actor: str,
    ) -> None:
        """Best-effort release without replacing the original abort reason.

        Another worker may have replaced or completed the exact claim by the
        time the abort is handled.  In that case, it is safer to leave its
        state alone and let the bounded lease resolve than to surface a second
        internal error instead of the consent/state disposition.
        """
        try:
            self._release_claim(spec_id, claim, actor=actor)
        except RedTeamCampaignStateError:
            return

    @staticmethod
    def _score(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        counts = {
            "blocked_as_expected": 0,
            "allowed_as_expected": 0,
            "missed_attack": 0,
            "overblocked": 0,
            "inconclusive": 0,
        }
        for row in outcomes:
            outcome = row.get("outcome")
            if outcome not in counts:
                raise RedTeamCampaignStateError("campaign outcome is invalid")
            counts[outcome] += 1
        missed = sorted(row["case_id"] for row in outcomes if row["outcome"] == "missed_attack")
        overblocked = sorted(row["case_id"] for row in outcomes if row["outcome"] == "overblocked")
        inconclusive = sorted(row["case_id"] for row in outcomes if row["outcome"] == "inconclusive")
        if missed:
            result = "failed"
        elif inconclusive:
            result = "inconclusive"
        elif overblocked:
            result = "degraded"
        else:
            result = "passed"
        return {
            "result": result,
            "counts": counts,
            "missed_case_ids": missed,
            "overblocked_case_ids": overblocked,
            "inconclusive_case_ids": inconclusive,
        }

    def _ensure_report(
        self,
        *,
        spec: Mapping[str, Any],
        run: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        binding = run.get("corpus_binding")
        outcomes = run.get("case_outcomes")
        if not isinstance(binding, Mapping) or not isinstance(outcomes, Mapping):
            raise RedTeamCampaignStateError("campaign run evidence is invalid")
        planned = binding.get("cases")
        if not isinstance(planned, list) or set(outcomes) != {
            row.get("case_id") for row in planned if isinstance(row, Mapping)
        }:
            raise RedTeamCampaignStateError("campaign run is not complete")
        ordered = [outcomes[key] for key in sorted(outcomes)]
        score = self._score(ordered)
        report_id = self._report_id(str(run["id"]))
        immutable = {
            "campaign_spec_id": spec["id"],
            "campaign_spec_sha256": spec["spec_sha256"],
            "run_id": run["id"],
            "run_payload_sha256": run["payload_sha256"],
            "scheduled_for": run["scheduled_for"],
            "target_binding": run["target_binding"],
            "corpus_binding": binding,
            "case_outcomes": ordered,
            "score": score,
            "notice": CAMPAIGN_NOTICE,
            "legal_certification": False,
        }
        payload_sha = _sha256(immutable)
        record = {
            "id": report_id,
            "schema": CAMPAIGN_REPORT_SCHEMA,
            **immutable,
            "payload_sha256": payload_sha,
        }
        try:
            return self._reports.create(
                record,
                action="create_red_team_campaign_report",
                actor=actor,
                max_records=_MAX_REPORTS,
            )
        except RecordConflict as exc:
            current = self._reports.get(report_id)
            if current is not None and current.get("payload_sha256") == payload_sha:
                return current
            raise RedTeamCampaignStateError("campaign report identity changed") from exc

    def _ensure_drift(
        self,
        *,
        spec: Mapping[str, Any],
        report: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        prior_id = spec.get("last_report_id")
        prior = self._reports.get(str(prior_id)) if isinstance(prior_id, str) else None
        comparable = bool(
            isinstance(prior, Mapping)
            and prior.get("schema") == CAMPAIGN_REPORT_SCHEMA
            and prior.get("target_binding") == report.get("target_binding")
            and (prior.get("corpus_binding") or {}).get("corpus_sha256")
            == (report.get("corpus_binding") or {}).get("corpus_sha256")
        )
        current_outcomes = {
            row["case_id"]: row["outcome"]
            for row in report.get("case_outcomes") or []
            if isinstance(row, Mapping)
        }
        prior_outcomes = {
            row["case_id"]: row["outcome"]
            for row in (prior or {}).get("case_outcomes") or []
            if isinstance(row, Mapping)
        } if comparable else {}
        changed = sorted(
            case_id
            for case_id in set(current_outcomes).intersection(prior_outcomes)
            if current_outcomes[case_id] != prior_outcomes[case_id]
        )
        new_missed = sorted(
            case_id for case_id in changed if current_outcomes[case_id] == "missed_attack"
        )
        remediated_missed = sorted(
            case_id for case_id in changed if prior_outcomes[case_id] == "missed_attack"
        )
        new_overblocked = sorted(
            case_id for case_id in changed if current_outcomes[case_id] == "overblocked"
        )
        remediated_overblocked = sorted(
            case_id for case_id in changed if prior_outcomes[case_id] == "overblocked"
        )
        current_counts = dict((report.get("score") or {}).get("counts") or {})
        prior_counts = dict((prior or {}).get("score") or {}).get("counts") or {}
        immutable = {
            "campaign_spec_id": spec["id"],
            "report_id": report["id"],
            "report_payload_sha256": report["payload_sha256"],
            "baseline_report_id": prior.get("id") if comparable else None,
            "baseline_report_payload_sha256": prior.get("payload_sha256") if comparable else None,
            "comparable": comparable,
            "changed_case_ids": changed,
            "new_missed_case_ids": new_missed,
            "remediated_missed_case_ids": remediated_missed,
            "new_overblocked_case_ids": new_overblocked,
            "remediated_overblocked_case_ids": remediated_overblocked,
            "count_delta": {
                key: int(current_counts.get(key) or 0) - int(prior_counts.get(key) or 0)
                for key in sorted(set(current_counts).union(prior_counts))
            } if comparable else {},
            "notice": CAMPAIGN_NOTICE,
            "legal_certification": False,
        }
        payload_sha = _sha256(immutable)
        drift_id = self._drift_id(str(report["id"]))
        record = {
            "id": drift_id,
            "schema": CAMPAIGN_DRIFT_SCHEMA,
            **immutable,
            "payload_sha256": payload_sha,
        }
        try:
            return self._drifts.create(
                record,
                action="create_red_team_campaign_drift",
                actor=actor,
                max_records=_MAX_DRIFTS,
            )
        except RecordConflict as exc:
            current = self._drifts.get(drift_id)
            if current is not None and current.get("payload_sha256") == payload_sha:
                return current
            raise RedTeamCampaignStateError("campaign drift identity changed") from exc

    def _complete_run(
        self,
        run_id: str,
        *,
        report: Mapping[str, Any],
        drift: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        identifier = _identity(run_id, "campaign run id", 64)
        for _attempt in range(4):
            run = self._raw_run(identifier)
            if run is None:
                raise RedTeamCampaignStateError("campaign run does not exist")
            if run.get("status") == "completed":
                if run.get("report_id") != report.get("id") or run.get("drift_id") != drift.get("id"):
                    raise RedTeamCampaignStateError("completed campaign run report changed")
                return run

            def complete(row: dict[str, Any]) -> None:
                if row.get("status") != "running":
                    raise RedTeamCampaignStateError("campaign run is not running")
                row["status"] = "completed"
                row["report_id"] = report["id"]
                row["drift_id"] = drift["id"]
                row["completed_at"] = self._now()

            try:
                saved = self._runs.update(
                    identifier,
                    complete,
                    expected_revision=int(run.get("revision") or 0),
                    action="complete_red_team_campaign_run",
                    actor=actor,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign run disappeared during completion")
            return saved
        raise RedTeamCampaignStateError("campaign run changed concurrently; retry")

    def _complete_schedule(
        self,
        spec_id: str,
        *,
        claim: Mapping[str, Any],
        run: Mapping[str, Any],
        report: Mapping[str, Any],
        drift: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        identifier = _identity(spec_id, "campaign spec id", 64)
        for _attempt in range(4):
            spec = self._raw_spec(identifier)
            if spec is None:
                raise RedTeamCampaignStateError("campaign specification does not exist")
            if spec.get("last_run_id") == run.get("id"):
                return spec
            self._assert_claim(spec, claim, self._now())
            scheduled_for = float(claim["scheduled_for"])
            cadence = _integer(spec.get("cadence_seconds"), "cadence_seconds", _MIN_CADENCE_SECONDS, _MAX_CADENCE_SECONDS)

            def advance(
                row: dict[str, Any],
                expected_scheduled_for: float = scheduled_for,
                interval: int = cadence,
            ) -> None:
                self._assert_claim(row, claim, self._now())
                if row.get("next_run_at") != expected_scheduled_for:
                    raise RecordConflict("campaign schedule advanced concurrently")
                now = self._now()
                row["pending_run"] = None
                row["next_run_at"] = _next_scheduled_time(
                    expected_scheduled_for, interval, now
                )
                row["last_run_id"] = run["id"]
                row["last_report_id"] = report["id"]
                row["last_drift_id"] = drift["id"]
                row["last_completed_at"] = now
                row["run_count"] = int(row.get("run_count") or 0) + 1

            try:
                saved = self._specs.update(
                    identifier,
                    advance,
                    expected_revision=int(spec.get("revision") or 0),
                    action="complete_red_team_campaign_schedule",
                    actor=actor,
                )
            except RecordConflict:
                continue
            if saved is None:
                raise RedTeamCampaignStateError("campaign specification disappeared during completion")
            return saved
        raise RedTeamCampaignStateError("campaign schedule changed concurrently; retry")

    def _execute_claimed_run(
        self,
        spec: Mapping[str, Any],
        claim: Mapping[str, Any],
        *,
        actor: str,
    ) -> tuple[str, dict[str, Any]]:
        """Run/resume a claimed schedule. ``retry_pending`` makes no assertion."""
        self._assert_claim(spec, claim, self._now())
        run = self._ensure_run(spec, claim, actor=actor)
        if run.get("status") == "completed":
            report = self._reports.get(str(run.get("report_id") or ""))
            drift = self._drifts.get(str(run.get("drift_id") or ""))
            if not isinstance(report, Mapping) or not isinstance(drift, Mapping):
                raise RedTeamCampaignStateError("completed run evidence is unavailable")
            self._complete_schedule(
                str(spec["id"]), claim=claim, run=run, report=report, drift=drift, actor=actor
            )
            return "completed", run
        corpus = self._corpus()
        planned = self._planned_cases(spec, corpus)
        maximum = _integer(run.get("max_attempts"), "run max_attempts", 1, _MAX_ATTEMPTS)
        for case in planned:
            latest_spec = self._raw_spec(str(spec["id"]))
            if latest_spec is None:
                raise RedTeamCampaignStateError("campaign specification disappeared during run")
            self._assert_claim(latest_spec, claim, self._now())
            adapter = self._assert_target_binding(latest_spec)
            current_run = self._raw_run(str(run["id"]))
            if current_run is None:
                raise RedTeamCampaignStateError("campaign run disappeared during execution")
            outcomes = current_run.get("case_outcomes")
            if not isinstance(outcomes, Mapping):
                raise RedTeamCampaignStateError("campaign run outcomes are invalid")
            if case.case_id in outcomes:
                continue
            attempts = self._start_case_attempt(str(run["id"]), case.case_id, actor=actor)
            invocation = RedTeamInvocation(
                campaign_spec_id=str(spec["id"]),
                run_id=str(run["id"]),
                invocation_id=_record_id("RTCIN", run["id"], case.case_id),
                case_id=case.case_id,
                prompt=case.text,
                expected=case.expected,
                category=case.category,
            )
            try:
                result = adapter.invoke(invocation)
                if not isinstance(result, RedTeamTargetResult):
                    raise RedTeamCampaignStateError("target adapter returned an unclosed result")
            except RedTeamCampaignRetryableError:
                if attempts < maximum:
                    self._release_claim(str(spec["id"]), claim, actor=actor)
                    return "retry_pending", self._raw_run(str(run["id"])) or run
                result = RedTeamTargetResult("inconclusive", ("retry_exhausted",))
            except RedTeamCampaignStateError:
                raise
            except Exception:
                # A target's exception text may contain response data, endpoint
                # details, or credentials. Persist only this fixed outcome code.
                result = RedTeamTargetResult("inconclusive", ("adapter_exception",))
            outcome = self._classify_case(
                case,
                result,
                invocation_id=invocation.invocation_id,
                attempts=attempts,
            )
            run = self._record_case_outcome(str(run["id"]), outcome, actor=actor)
        run = self._raw_run(str(run["id"]))
        if run is None:
            raise RedTeamCampaignStateError("campaign run disappeared before report")
        report = self._ensure_report(spec=spec, run=run, actor=actor)
        drift = self._ensure_drift(spec=spec, report=report, actor=actor)
        completed = self._complete_run(str(run["id"]), report=report, drift=drift, actor=actor)
        self._complete_schedule(
            str(spec["id"]), claim=claim, run=completed, report=report, drift=drift, actor=actor
        )
        return "completed", completed

    def run_due_campaigns(self, *, actor: str, limit: int = 100) -> dict[str, Any]:
        """Claim and execute due specs in deterministic order.

        The report never names a raw corpus prompt or a target response. A
        retryable adapter failure returns the same durable run identity on the
        next call and passes the same invocation IDs back to the adapter.
        """
        operator = _identity(actor, "actor")
        maximum = _integer(limit, "limit", 1, 500)
        selected = self.due_campaign_specs(limit=maximum)["items"]
        completed: list[dict[str, Any]] = []
        retrying: list[str] = []
        skipped: list[dict[str, str]] = []
        for view in selected:
            spec_id = str(view["id"])
            claim: Mapping[str, Any] | None = None
            try:
                spec, claim = self._claim_due_spec(spec_id, actor=operator)
                disposition, run = self._execute_claimed_run(spec, claim, actor=operator)
            except RedTeamCampaignBusy:
                skipped.append({"campaign_spec_id": spec_id, "reason": "leased"})
                continue
            except RedTeamCampaignConsentError:
                # A target can be revoked or reconfigured after it was leased.
                # Release only our exact claim so the specification is not
                # needlessly locked while an operator reviews the mismatch.
                if claim is not None:
                    self._release_claim_after_abort(spec_id, claim, actor=operator)
                skipped.append({"campaign_spec_id": spec_id, "reason": "consent_or_configuration"})
                continue
            except RedTeamCampaignStateError:
                # State changes are retryable by an operator or later worker;
                # never strand a lease we still demonstrably own.
                if claim is not None:
                    self._release_claim_after_abort(spec_id, claim, actor=operator)
                skipped.append({"campaign_spec_id": spec_id, "reason": "state_changed"})
                continue
            if disposition == "retry_pending":
                retrying.append(str(run["id"]))
            else:
                completed.append(self._run_view(run))
        return {
            "runs": completed,
            "count": len(completed),
            "retry_pending_run_ids": retrying,
            "skipped": skipped,
            "selected_count": len(selected),
        }

    def get_campaign_report(self, report_id: str) -> dict[str, Any] | None:
        identifier = _identity(report_id, "campaign report id", 64)
        row = self._reports.get(identifier)
        if row is not None and row.get("schema") != CAMPAIGN_REPORT_SCHEMA:
            raise RedTeamCampaignStateError("campaign report schema is invalid")
        return row

    def get_campaign_drift(self, drift_id: str) -> dict[str, Any] | None:
        identifier = _identity(drift_id, "campaign drift id", 64)
        row = self._drifts.get(identifier)
        if row is not None and row.get("schema") != CAMPAIGN_DRIFT_SCHEMA:
            raise RedTeamCampaignStateError("campaign drift schema is invalid")
        return row


__all__ = [
    "CAMPAIGN_DRIFT_SCHEMA",
    "CAMPAIGN_NOTICE",
    "CAMPAIGN_REPORT_SCHEMA",
    "CAMPAIGN_RUN_SCHEMA",
    "CAMPAIGN_SPEC_SCHEMA",
    "RedTeamCampaignBusy",
    "RedTeamCampaignConsentError",
    "RedTeamCampaignError",
    "RedTeamCampaignRetryableError",
    "RedTeamCampaignService",
    "RedTeamCampaignStateError",
    "RedTeamCorpus",
    "RedTeamCorpusCase",
    "RedTeamInvocation",
    "RedTeamTargetAdapter",
    "RedTeamTargetResult",
    "load_shield_corpus",
    "make_shield_pack_adapter",
]
