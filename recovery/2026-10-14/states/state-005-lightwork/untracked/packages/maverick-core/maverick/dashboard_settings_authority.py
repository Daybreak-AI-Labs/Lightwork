"""Tenant-scoped dashboard settings over the governed authority plane.

The dashboard package owns validation and the public settings API.  Core
configuration readers also need the resulting overlay, so the persistence
adapter lives in ``maverick-core`` rather than making core import the dashboard
package.  Postgres is mandatory here: callers select this adapter only after
the governed-record deployment policy resolves to ``postgres``.
"""
from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from .authority_documents import AuthorityDocumentStore

_T = TypeVar("_T")
_AUTHORITY: DashboardSettingsAuthority | None = None
_AUTHORITY_LOCK = threading.Lock()
_MAX_DOCUMENT_BYTES = 1024 * 1024
_MAX_DEPTH = 8
_MAX_ENTRIES = 4096
_MAX_LIST_ITEMS = 1024
_MAX_KEY_CHARS = 256
_MAX_STRING_BYTES = 64 * 1024
_ALLOWED_SECTIONS = frozenset({
    "agent_trust_auditor",
    "budget",
    "capabilities",
    "channels",
    "consequence",
    "cra_ops",
    "credit",
    "data_engine",
    "dreaming",
    "ekko",
    "env_hunt",
    "evidence_factory",
    "evidence_graph",
    "experience",
    "features",
    "flows",
    "jit_rl",
    "operations_scientist",
    "providers",
    "reasoning_reward",
    "reflexion",
    "rehearsal",
    "security_ops",
    "security_suite_control",
    "self_harness",
    "self_improvement",
    "self_learning",
    "self_modify",
    "skill_synthesis",
    "telemetry",
    "threat_hunt",
    "value",
    "webhooks",
})


class DashboardSettingsAuthorityError(RuntimeError):
    """The shared dashboard-settings authority is unavailable or ambiguous."""


def _count(entries: list[int], amount: int) -> None:
    entries[0] += amount
    if entries[0] > _MAX_ENTRIES:
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings exceed the entry limit"
        )


def _validate_node(value: object, depth: int, entries: list[int]) -> None:
    if depth > _MAX_DEPTH:
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings exceed the nesting limit"
        )
    if isinstance(value, dict):
        _count(entries, len(value))
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > _MAX_KEY_CHARS
                or not key.isprintable()
            ):
                raise DashboardSettingsAuthorityError(
                    "shared dashboard settings contain an invalid key"
                )
            _validate_node(item, depth + 1, entries)
        return
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings exceed the list limit"
            )
        _count(entries, len(value))
        for item in value:
            _validate_node(item, depth + 1, entries)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings contain an out-of-range integer"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings contain a non-finite number"
            )
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings contain an oversized string"
            )
        return
    raise DashboardSettingsAuthorityError(
        "shared dashboard settings contain an unsupported value"
    )


def _validate_security_metadata(document: dict[str, Any]) -> None:
    metadata = document.get("security_suite_control")
    if not metadata:
        return
    revision = metadata.get("revision")
    if (
        set(metadata) != {"revision"}
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings have invalid security-suite metadata"
        )


def _validate_document(document: object) -> dict[str, Any]:
    """Reject malformed or pathologically large shared policy documents."""
    if not isinstance(document, dict):
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings root must be an object"
        )
    if any(not isinstance(key, str) for key in document):
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings contain an invalid section"
        )
    if set(document).difference(_ALLOWED_SECTIONS):
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings contain an unknown section"
        )
    if any(not isinstance(value, dict) for value in document.values()):
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings sections must be objects"
        )

    _validate_node(document, 0, [0])
    try:
        encoded = json.dumps(
            document,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings are not bounded JSON"
        ) from exc
    if len(encoded) > _MAX_DOCUMENT_BYTES:
        raise DashboardSettingsAuthorityError(
            "shared dashboard settings exceed the size limit"
        )
    _validate_security_metadata(document)
    return document


class DashboardSettingsAuthority:
    """One tenant-scoped, CAS-updated dashboard settings document."""

    def __init__(self, store: AuthorityDocumentStore | None = None) -> None:
        self._store = store or AuthorityDocumentStore(
            "dashboard_settings",
            "DSS",
            "dashboard_settings",
            document_name="overlay",
            required_backend="postgres",
        )

    @staticmethod
    def _require_tenant() -> None:
        from .paths import current_tenant_id_strict

        if current_tenant_id_strict() is None:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings require an active tenant"
            )

    def _require_postgres(self) -> None:
        try:
            kind = self._store.backend_kind
        except Exception as exc:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings backend is unavailable"
            ) from exc
        if kind != "postgres":
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings require the Postgres backend"
            )

    @staticmethod
    def _refuse_unmigrated_local_overlay() -> None:
        from .config import dashboard_overrides_path

        path = dashboard_overrides_path()
        try:
            path.stat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise DashboardSettingsAuthorityError(
                "local dashboard settings cutover state is unavailable"
            ) from exc
        raise DashboardSettingsAuthorityError(
            "local dashboard settings exist; migrate them into Postgres and "
            "archive the local overlay before enabling shared settings"
        )

    def read(self) -> dict[str, Any]:
        self._require_tenant()
        self._refuse_unmigrated_local_overlay()
        self._require_postgres()
        try:
            from .config import without_shared_dashboard_settings

            with without_shared_dashboard_settings():
                document = self._store.read()
            self._require_postgres()
            return _validate_document(document)
        except DashboardSettingsAuthorityError:
            raise
        except Exception as exc:
            raise DashboardSettingsAuthorityError(
                "shared dashboard settings could not be read"
            ) from exc

    def mutate(
        self,
        callback: Callable[[dict[str, Any]], _T],
        *,
        action: str,
        actor: str,
    ) -> _T:
        self._require_tenant()
        self._refuse_unmigrated_local_overlay()
        self._require_postgres()
        # Callback exceptions are part of the dashboard API's validation/CAS
        # contract and must retain their type. Persistence exceptions still
        # fail closed at the caller boundary; no local fallback is attempted.
        def _validated_callback(document: dict[str, Any]) -> _T:
            _validate_document(document)
            result = callback(document)
            _validate_document(document)
            return result

        from .config import without_shared_dashboard_settings

        with without_shared_dashboard_settings():
            result = self._store.mutate(
                _validated_callback,
                action=action,
                actor=actor,
            )
        self._require_postgres()
        return result


def dashboard_settings_authority() -> DashboardSettingsAuthority:
    """Return the process-local adapter for the shared tenant document."""
    global _AUTHORITY

    with _AUTHORITY_LOCK:
        if _AUTHORITY is None:
            _AUTHORITY = DashboardSettingsAuthority()
        return _AUTHORITY


__all__ = [
    "DashboardSettingsAuthority",
    "DashboardSettingsAuthorityError",
    "dashboard_settings_authority",
]
