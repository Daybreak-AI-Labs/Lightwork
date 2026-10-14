"""Shared ingress authority for signed cross-deployment federation.

The transport is deliberately not the source of truth. Replay claims, rate
windows, and correlation ownership/result records are tenant-scoped governed
CAS documents, so two service replicas make one admission decision. Postgres
selection, tenant RLS, encryption admission, and audit-outbox durability come
from :mod:`maverick.governed_records` through ``AuthorityDocumentStore``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import uuid
from typing import Any

from .authority_documents import AuthorityDocumentStore
from .governed_records import GovernedRecordBackend


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _finite_positive(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


class GovernedFederationState:
    """Atomic shared implementation of the federation state protocol."""

    shared = True

    def __init__(
        self,
        *,
        correlation_ttl_s: float,
        correlation_max: int,
        correlation_per_peer_max: int,
        replay_max_per_scope: int = 10_000,
        rate_max_keys: int = 8_192,
        backend: GovernedRecordBackend | None = None,
    ) -> None:
        for value, name in (
            (correlation_max, "correlation_max"),
            (correlation_per_peer_max, "correlation_per_peer_max"),
            (replay_max_per_scope, "replay_max_per_scope"),
            (rate_max_keys, "rate_max_keys"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 1_000_000
            ):
                raise ValueError(f"{name} must be an integer from 1 to 1000000")
        if not _finite_positive(correlation_ttl_s):
            raise ValueError("correlation_ttl_s must be finite and positive")
        self._correlation_ttl_s = float(correlation_ttl_s)
        self._correlation_max = correlation_max
        self._correlation_per_peer_max = min(
            correlation_per_peer_max,
            correlation_max,
        )
        self._replay_max = replay_max_per_scope
        self._rate_max_keys = rate_max_keys
        self._replays: AuthorityDocumentStore[bool] = AuthorityDocumentStore(
            "federation_ingress_replay",
            "FIR",
            "federation_ingress_replay_claims",
            backend=backend,
            required_backend="postgres",
        )
        self._rates: AuthorityDocumentStore[bool] = AuthorityDocumentStore(
            "federation_ingress_rate",
            "FRT",
            "federation_ingress_rate_limits",
            backend=backend,
            required_backend="postgres",
        )
        self._correlations: AuthorityDocumentStore[Any] = AuthorityDocumentStore(
            "federation_ingress_correlation",
            "FIC",
            "federation_ingress_correlations",
            backend=backend,
            required_backend="postgres",
        )

    @staticmethod
    def _replay_scopes(document: dict) -> dict[str, dict[str, float]]:
        raw = document.setdefault("scopes", {})
        if not isinstance(raw, dict):
            raise ValueError("federation replay scopes must be an object")
        scopes: dict[str, dict[str, float]] = {}
        for scope_key, entries in raw.items():
            if not _hash_key(scope_key) or not isinstance(entries, dict):
                raise ValueError("federation replay authority is malformed")
            checked: dict[str, float] = {}
            for claim_key, expiry in entries.items():
                if not _hash_key(claim_key) or not _finite_positive(expiry):
                    raise ValueError("federation replay authority is malformed")
                checked[claim_key] = float(expiry)
            scopes[scope_key] = checked
        return scopes

    def claim_replay(
        self,
        scope: str,
        sig: str,
        *,
        expires_at: float,
        now: float,
    ) -> bool:
        if not scope or not sig or not _finite_positive(expires_at) or not _finite_positive(now):
            raise ValueError("invalid federation replay claim")
        scope_key = _hash_text(scope)
        claim_key = _hash_text(sig)

        def _claim(document: dict, authority_now: float) -> bool:
            if not _finite_positive(authority_now):
                raise ValueError("federation replay authority clock is invalid")
            scopes = self._replay_scopes(document)
            pruned = {
                item_scope: {
                    key: expiry
                    for key, expiry in entries.items()
                    if expiry >= float(authority_now)
                }
                for item_scope, entries in scopes.items()
            }
            pruned = {key: entries for key, entries in pruned.items() if entries}
            entries = pruned.setdefault(scope_key, {})
            if claim_key in entries:
                document["scopes"] = pruned
                return True
            if len(entries) >= self._replay_max:
                raise ValueError("federation replay ledger capacity reached")
            entries[claim_key] = float(expires_at)
            document["scopes"] = pruned
            return False

        return self._replays.mutate_with_authoritative_time(
            _claim,
            action="federation-replay.claim",
            actor="federation-ingress",
        )

    @staticmethod
    def _rate_windows(document: dict) -> dict[str, list[float]]:
        raw = document.setdefault("windows", {})
        if not isinstance(raw, dict):
            raise ValueError("federation rate windows must be an object")
        windows: dict[str, list[float]] = {}
        for key, hits in raw.items():
            if not _hash_key(key) or not isinstance(hits, list):
                raise ValueError("federation rate authority is malformed")
            checked: list[float] = []
            for hit in hits:
                if not _finite_positive(hit):
                    raise ValueError("federation rate authority is malformed")
                checked.append(float(hit))
            windows[key] = checked
        return windows

    def rate_ok(self, key: str, *, now: float, limit: int) -> bool:
        if (
            not key
            or not _finite_positive(now)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
        ):
            raise ValueError("invalid federation rate claim")
        rate_key = _hash_text(key)

        def _claim(document: dict, authority_now: float) -> bool:
            if not _finite_positive(authority_now):
                raise ValueError("federation rate authority clock is invalid")
            cutoff = float(authority_now) - 60.0
            windows = {
                item_key: [hit for hit in hits if hit >= cutoff]
                for item_key, hits in self._rate_windows(document).items()
            }
            windows = {item_key: hits for item_key, hits in windows.items() if hits}
            if rate_key not in windows and len(windows) >= self._rate_max_keys:
                document["windows"] = windows
                return False
            hits = windows.setdefault(rate_key, [])
            if len(hits) >= limit:
                document["windows"] = windows
                return False
            hits.append(float(authority_now))
            document["windows"] = windows
            return True

        # ``now`` remains in the protocol for local-state/test compatibility,
        # but it is never authoritative in the shared store. A fast replica
        # must not prune accurate peers' hits and reopen the fleet-wide cap.
        return self._rates.mutate_with_authoritative_time(
            _claim,
            action="federation-rate.claim",
            actor="federation-ingress",
        )

    @staticmethod
    def _correlation_entries(document: dict) -> dict[str, dict[str, Any]]:
        raw = document.setdefault("entries", {})
        if not isinstance(raw, dict):
            raise ValueError("federation correlations must be an object")
        entries: dict[str, dict[str, Any]] = {}
        for key, entry in raw.items():
            if not _hash_key(key) or not isinstance(entry, dict):
                raise ValueError("federation correlation authority is malformed")
            peer = entry.get("peer")
            digest = entry.get("digest")
            status = entry.get("status")
            owner = entry.get("owner_token")
            result = entry.get("result")
            created_at = entry.get("created_at")
            if (
                not _hash_key(peer)
                or not _hash_key(digest)
                or status not in {"in_progress", "complete"}
                or not isinstance(owner, str)
                or len(owner) != 32
                or any(ch not in "0123456789abcdef" for ch in owner)
                or not _finite_positive(created_at)
                or (status == "in_progress" and result is not None)
                or (status == "complete" and not isinstance(result, dict))
            ):
                raise ValueError("federation correlation authority is malformed")
            entries[key] = {
                "peer": peer,
                "digest": digest,
                "status": status,
                "owner_token": owner,
                "result": dict(result) if isinstance(result, dict) else None,
                "created_at": float(created_at),
            }
        return entries

    @staticmethod
    def _correlation_key(peer: str, corr: str) -> str:
        return _hash_text(f"{peer}\0{corr}")

    def _prune_correlations(
        self,
        entries: dict[str, dict[str, Any]],
        *,
        now: float,
    ) -> dict[str, dict[str, Any]]:
        """Forget claims only after the advertised idempotency window.

        An abandoned ``in_progress`` claim must not occupy a fleet-wide slot
        forever.  It is retained for exactly the same authority-clock window
        as a completed result, during which a different operation remains
        unable to execute the correlation.  Once that documented retention
        window ends, both forms are eligible for bounded pruning.
        """
        cutoff = now - self._correlation_ttl_s
        return {
            key: entry
            for key, entry in entries.items()
            if entry["created_at"] >= cutoff
        }

    def claim_correlation(
        self,
        peer: str,
        corr: str,
        digest: str,
        *,
        now: float,
        operation_id: str | None = None,
    ) -> tuple[str, dict[str, Any] | None, str]:
        if not peer or not corr or not _hash_key(digest) or not _finite_positive(now):
            raise ValueError("invalid federation correlation claim")
        owner = operation_id if operation_id is not None else uuid.uuid4().hex
        if (
            not isinstance(owner, str)
            or len(owner) != 32
            or any(ch not in "0123456789abcdef" for ch in owner)
        ):
            raise ValueError("invalid federation correlation operation id")
        peer_key = _hash_text(peer)
        correlation_key = self._correlation_key(peer, corr)

        def _claim(
            document: dict,
            authority_now: float,
        ) -> tuple[str, dict[str, Any] | None, str]:
            if not _finite_positive(authority_now):
                raise ValueError("federation correlation authority clock is invalid")
            entries = self._prune_correlations(
                self._correlation_entries(document),
                now=float(authority_now),
            )
            existing = entries.get(correlation_key)
            if existing is not None:
                document["entries"] = entries
                if not hmac.compare_digest(existing["digest"], digest):
                    return "conflict", None, ""
                if existing["status"] == "complete":
                    return "cached", dict(existing["result"]), ""
                # The operation id is generated once by the request handler,
                # before any CAS attempt.  If the commit succeeded but its
                # acknowledgement was lost, a bounded retry can recover only
                # that exact owner.  A concurrent request has another token
                # and remains busy, so it cannot start a second goal.
                if hmac.compare_digest(existing["owner_token"], owner):
                    return "owner", None, owner
                return "busy", None, ""
            peer_count = sum(1 for entry in entries.values() if entry["peer"] == peer_key)
            if (
                len(entries) >= self._correlation_max
                or peer_count >= self._correlation_per_peer_max
            ):
                document["entries"] = entries
                return "capacity", None, ""
            entries[correlation_key] = {
                "peer": peer_key,
                "digest": digest,
                "status": "in_progress",
                "owner_token": owner,
                "result": None,
                "created_at": float(authority_now),
            }
            document["entries"] = entries
            return "owner", None, owner

        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                return self._correlations.mutate_with_authoritative_time(
                    _claim,
                    action="federation-correlation.claim",
                    actor="federation-ingress",
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 - commit may have succeeded
                last_error = exc
        assert last_error is not None
        raise last_error

    def correlation_result(
        self,
        peer: str,
        corr: str,
        digest: str,
    ) -> tuple[str, dict[str, Any] | None]:
        entry = self._correlation_entries(self._correlations.read()).get(
            self._correlation_key(peer, corr)
        )
        if entry is None:
            return "missing", None
        if not hmac.compare_digest(entry["digest"], digest):
            return "conflict", None
        if entry["status"] != "complete":
            return "busy", None
        return "cached", dict(entry["result"])

    @staticmethod
    def _validated_result(result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ValueError("federation correlation result must be an object")
        encoded = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("federation correlation result is too large")
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):  # pragma: no cover - guarded above
            raise ValueError("federation correlation result must be an object")
        return decoded

    def finish_correlation(
        self,
        peer: str,
        corr: str,
        digest: str,
        owner_token: str,
        result: dict[str, Any],
    ) -> None:
        correlation_key = self._correlation_key(peer, corr)
        checked_result = self._validated_result(result)

        def _finish(document: dict) -> None:
            entries = self._correlation_entries(document)
            entry = entries.get(correlation_key)
            if (
                entry is None
                or not hmac.compare_digest(entry["digest"], digest)
                or not hmac.compare_digest(entry["owner_token"], owner_token)
            ):
                raise ValueError("federation correlation ownership changed")
            if entry["status"] == "complete":
                if entry["result"] != checked_result:
                    raise ValueError("federation correlation result changed")
                # Idempotent recovery for a commit whose acknowledgement was
                # lost.  Only the original owner and exact validated result
                # can converge through this branch.
                document["entries"] = entries
                return
            if entry["status"] != "in_progress":
                raise ValueError("federation correlation ownership changed")
            entry["status"] = "complete"
            entry["result"] = checked_result
            document["entries"] = entries

        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                self._correlations.mutate(
                    _finish,
                    action="federation-correlation.finish",
                    actor="federation-ingress",
                )
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 - commit may have succeeded
                last_error = exc
        assert last_error is not None
        raise last_error

    def release_correlation(
        self,
        peer: str,
        corr: str,
        digest: str,
        owner_token: str,
    ) -> None:
        correlation_key = self._correlation_key(peer, corr)

        def _release(document: dict) -> None:
            entries = self._correlation_entries(document)
            entry = entries.get(correlation_key)
            if (
                entry is not None
                and entry["status"] == "in_progress"
                and hmac.compare_digest(entry["digest"], digest)
                and hmac.compare_digest(entry["owner_token"], owner_token)
            ):
                entries.pop(correlation_key, None)
            document["entries"] = entries

        self._correlations.mutate(
            _release,
            action="federation-correlation.release",
            actor="federation-ingress",
        )


__all__ = ["GovernedFederationState"]
