"""Production composition for stock continuous-evidence collectors.

The deployment layer constructs immutable ``StockCollectorSource`` manifests
from a tenant's reviewed configuration, then installs the exact registry and
credential-authority pair consumed by ``TrcContinuousEvidenceScheduler``.
Import, attachment, and readiness perform no provider request and resolve no
credential value. Raw scopes, origins, bindings, and secret references never
cross the browser-facing readiness contract.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maverick.paths import canonical_tenant_id, current_tenant_id_strict
from maverick.trc_evidence_collectors import LocalSoc2Collector
from maverick.trc_stock_evidence_collectors import (
    STOCK_COLLECTOR_IDS,
    PinnedStockEvidenceHTTPTransport,
    ProviderControlBinding,
    StockCollectorDirectory,
    StockCollectorLimits,
    StockCollectorSource,
    StockEvidenceConfigurationError,
    StockEvidenceHTTPTransport,
)

from .trc_continuous_evidence_scheduler import (
    TrcContinuousEvidenceScheduler,
    configured_collector_ids,
)

_STOCK_KEYS = frozenset({"enable", "sources"})
_SOURCE_KEYS = frozenset(
    {
        "bindings",
        "capability_revision",
        "collector_key_id",
        "credential_env",
        "definition_id",
        "definition_integrity_sha256",
        "definition_version",
        "enrollment_id",
        "limits",
        "origin",
        "provider",
        "region",
        "scope_ref",
        "stream",
    }
)
_BINDING_KEYS = frozenset({"control_ref", "source_ref"})
_LIMIT_KEYS = frozenset(
    {
        "max_elapsed_seconds",
        "max_pages",
        "max_records",
        "max_response_bytes",
        "page_size",
        "timeout_seconds",
    }
)
_MAX_SOURCES = 64
_SAFE_SECRET_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)


class TrcStockEvidenceCompositionError(RuntimeError):
    """Stock collector composition is missing, malformed, or conflicts."""


def _tenant_config(tenant_id: str) -> Mapping[str, Any]:
    tenant = canonical_tenant_id(tenant_id)
    current = current_tenant_id_strict()
    if current is None or canonical_tenant_id(current) != tenant:
        raise TrcStockEvidenceCompositionError("stock_collector_tenant_scope_mismatch")
    try:
        from maverick.config import config_source_errors, load_config

        errors = config_source_errors(include_tenant=True)
        loaded = load_config() or {}
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise TrcStockEvidenceCompositionError(
            "stock_collector_configuration_unavailable"
        ) from exc
    if errors or not isinstance(loaded, Mapping):
        raise TrcStockEvidenceCompositionError("stock_collector_configuration_unavailable")
    return loaded


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrcStockEvidenceCompositionError(f"{label}_invalid")
    return value


def _source_from_mapping(tenant_id: str, raw: object) -> StockCollectorSource:
    source = _mapping(raw, "stock_collector_source")
    if set(source) - _SOURCE_KEYS:
        raise TrcStockEvidenceCompositionError("stock_collector_source_key_unsupported")
    bindings_raw = source.get("bindings")
    if (
        not isinstance(bindings_raw, list)
        or not bindings_raw
        or len(bindings_raw) > 250
    ):
        raise TrcStockEvidenceCompositionError("stock_collector_bindings_invalid")
    bindings: list[ProviderControlBinding] = []
    for raw_binding in bindings_raw:
        binding = _mapping(raw_binding, "stock_collector_binding")
        if set(binding) != _BINDING_KEYS:
            raise TrcStockEvidenceCompositionError("stock_collector_binding_invalid")
        try:
            bindings.append(
                ProviderControlBinding(
                    control_ref=binding["control_ref"],
                    source_ref=binding["source_ref"],
                )
            )
        except (KeyError, TypeError, ValueError, StockEvidenceConfigurationError) as exc:
            raise TrcStockEvidenceCompositionError(
                "stock_collector_binding_invalid"
            ) from exc
    limits = _mapping(source.get("limits", {}), "stock_collector_limits")
    if set(limits) - _LIMIT_KEYS:
        raise TrcStockEvidenceCompositionError("stock_collector_limit_key_unsupported")
    required = _SOURCE_KEYS - {"limits", "region", "stream"}
    if not required.issubset(source):
        raise TrcStockEvidenceCompositionError("stock_collector_source_field_missing")
    try:
        return StockCollectorSource(
            tenant_id=tenant_id,
            enrollment_id=source["enrollment_id"],
            definition_id=source["definition_id"],
            definition_version=source["definition_version"],
            definition_integrity_sha256=source["definition_integrity_sha256"],
            collector_key_id=source["collector_key_id"],
            capability_revision=source["capability_revision"],
            provider=source["provider"],
            origin=source["origin"],
            scope_ref=source["scope_ref"],
            credential_env=source["credential_env"],
            bindings=tuple(bindings),
            region=source.get("region"),
            stream=source.get("stream"),
            limits=StockCollectorLimits(**dict(limits)),
        )
    except (KeyError, TypeError, ValueError, StockEvidenceConfigurationError) as exc:
        raise TrcStockEvidenceCompositionError("stock_collector_source_invalid") from exc


@dataclass(frozen=True, slots=True)
class StockCollectorSettings:
    """Closed tenant configuration projected into exact core manifests."""

    feature_enabled: bool
    stock_enabled: bool
    enabled_collector_ids: tuple[str, ...]
    sources: tuple[StockCollectorSource, ...]

    @property
    def include_local_soc2(self) -> bool:
        return LocalSoc2Collector.collector_id in self.enabled_collector_ids

    @property
    def enabled_stock_collector_ids(self) -> tuple[str, ...]:
        admitted = set(STOCK_COLLECTOR_IDS)
        return tuple(item for item in self.enabled_collector_ids if item in admitted)


def stock_collector_settings(
    config: object,
    *,
    tenant_id: str,
) -> StockCollectorSettings:
    """Parse one tenant's exact stock-collector table without performing I/O."""

    tenant = canonical_tenant_id(tenant_id)
    enabled = configured_collector_ids(config)
    enabled_ids = tuple(enabled or ())
    feature_enabled = enabled is not None
    root = _mapping(config, "stock_collector_configuration")
    trc = root.get("tech_risk_compliance")
    if trc is None:
        return StockCollectorSettings(False, False, (), ())
    trc_table = _mapping(trc, "tech_risk_compliance")
    feature = trc_table.get("continuous_evidence")
    if feature is None:
        return StockCollectorSettings(False, False, (), ())
    feature_table = _mapping(feature, "continuous_evidence")
    stock = feature_table.get("stock_collectors")
    if stock is None:
        return StockCollectorSettings(feature_enabled, False, enabled_ids, ())
    stock_table = _mapping(stock, "stock_collectors")
    if set(stock_table) - _STOCK_KEYS:
        raise TrcStockEvidenceCompositionError("stock_collector_key_unsupported")
    stock_enabled = stock_table.get("enable", False)
    if type(stock_enabled) is not bool:
        raise TrcStockEvidenceCompositionError("stock_collector_enable_invalid")
    raw_sources = stock_table.get("sources", [])
    if (
        not isinstance(raw_sources, list)
        or len(raw_sources) > _MAX_SOURCES
        or (not stock_enabled and bool(raw_sources))
    ):
        raise TrcStockEvidenceCompositionError("stock_collector_sources_invalid")
    sources = tuple(
        _source_from_mapping(tenant, raw_source) for raw_source in raw_sources
    )
    enrollments = tuple(item.enrollment_id for item in sources)
    if len(set(enrollments)) != len(enrollments):
        raise TrcStockEvidenceCompositionError("stock_collector_enrollment_duplicated")
    configured_ids = {item.collector_id for item in sources}
    if not configured_ids.issubset(enabled_ids):
        raise TrcStockEvidenceCompositionError("stock_collector_not_allowlisted")
    if sources and not feature_enabled:
        raise TrcStockEvidenceCompositionError("stock_collector_parent_disabled")
    return StockCollectorSettings(
        feature_enabled,
        stock_enabled,
        enabled_ids,
        sources,
    )


def _secret_reference_present(name: str) -> bool:
    """Check only reference metadata; never open or return a secret value."""

    if not isinstance(name, str) or not name or any(ch not in _SAFE_SECRET_CHARS for ch in name):
        return False
    if name in os.environ:
        return True
    backend = os.environ.get("MAVERICK_SECRETS_BACKEND", "").strip().lower()
    directory = os.environ.get("MAVERICK_SECRETS_DIR", "").strip()
    if backend != "file" or not directory:
        return False
    base = Path(directory)
    return any((base / candidate).is_file() for candidate in (name, name.lower()))


def _default_transport_factory(_tenant_id: str) -> StockEvidenceHTTPTransport:
    from maverick.secret_provider import get_secret

    return PinnedStockEvidenceHTTPTransport(credential_resolver=get_secret)


@dataclass(frozen=True, slots=True)
class TrcStockEvidenceComposition:
    """Immutable scheduler dependency bundle for pre-built source manifests."""

    directory: StockCollectorDirectory

    def __post_init__(self) -> None:
        if not isinstance(self.directory, StockCollectorDirectory):
            raise TypeError("stock collector directory has the wrong type")

    @classmethod
    def from_sources(
        cls,
        sources: Sequence[StockCollectorSource],
        *,
        transport_factory: Callable[[str], StockEvidenceHTTPTransport] | None = None,
        include_local_soc2: bool = True,
        credential_ref_present: Callable[[str], bool] | None = None,
    ) -> TrcStockEvidenceComposition:
        return cls(
            StockCollectorDirectory(
                sources,
                transport_factory=transport_factory,
                include_local_soc2=include_local_soc2,
                credential_ref_present=credential_ref_present,
            )
        )

    def registry_factory(self, tenant_id: str):
        return self.directory.registry_for_tenant(tenant_id)

    def credential_resolver(
        self,
        tenant_id: str,
        enrollment_id: str,
        enrollment_revision: int,
        credential_profile_sha256: str,
    ):
        return self.directory.resolve_credential(
            tenant_id,
            enrollment_id,
            enrollment_revision,
            credential_profile_sha256,
        )

    def readiness(self, tenant_id: str) -> Mapping[str, object]:
        return self.directory.readiness(tenant_id)

    def build_scheduler(self, **dependencies: Any) -> TrcContinuousEvidenceScheduler:
        """Build the existing supervisor with this exact registry/resolver pair."""

        forbidden = {"registry_factory", "credential_resolver"}.intersection(dependencies)
        if forbidden:
            raise TrcStockEvidenceCompositionError(
                "stock scheduler dependencies cannot replace registry authority"
            )
        return TrcContinuousEvidenceScheduler(
            registry_factory=self.registry_factory,
            credential_resolver=self.credential_resolver,
            **dependencies,
        )


class ConfigDrivenTrcStockEvidenceComposition:
    """Fresh tenant-config composition used by the production dashboard."""

    def __init__(
        self,
        *,
        config_loader: Callable[[str], Mapping[str, Any]] = _tenant_config,
        transport_factory: Callable[[str], StockEvidenceHTTPTransport] = (
            _default_transport_factory
        ),
        credential_ref_present: Callable[[str], bool] = _secret_reference_present,
    ) -> None:
        if not all(
            callable(item)
            for item in (config_loader, transport_factory, credential_ref_present)
        ):
            raise TypeError("stock collector composition dependencies must be callable")
        self._config_loader = config_loader
        self._transport_factory = transport_factory
        self._credential_ref_present = credential_ref_present

    def _settings(self, tenant_id: str) -> StockCollectorSettings:
        tenant = canonical_tenant_id(tenant_id)
        return stock_collector_settings(
            self._config_loader(tenant),
            tenant_id=tenant,
        )

    def _directory(
        self,
        tenant_id: str,
    ) -> tuple[StockCollectorDirectory, StockCollectorSettings]:
        settings = self._settings(tenant_id)
        directory = StockCollectorDirectory(
            settings.sources,
            transport_factory=self._transport_factory,
            include_local_soc2=settings.include_local_soc2,
            credential_ref_present=self._credential_ref_present,
        )
        return directory, settings

    def registry_factory(self, tenant_id: str):
        directory, _settings = self._directory(tenant_id)
        return directory.registry_for_tenant(tenant_id)

    def credential_resolver(
        self,
        tenant_id: str,
        enrollment_id: str,
        enrollment_revision: int,
        credential_profile_sha256: str,
    ):
        directory, _settings = self._directory(tenant_id)
        return directory.resolve_credential(
            tenant_id,
            enrollment_id,
            enrollment_revision,
            credential_profile_sha256,
        )

    def readiness(self, tenant_id: str) -> Mapping[str, object]:
        tenant = canonical_tenant_id(tenant_id)
        directory, settings = self._directory(tenant)
        core = dict(directory.readiness(tenant))
        configured_ids = {item.collector_id for item in settings.sources}
        selected_ids = set(settings.enabled_stock_collector_ids)
        missing_ids = tuple(sorted(selected_ids - configured_ids))
        external_ready = bool(core.get("ready")) if selected_ids else True
        composition_ready = bool(
            settings.feature_enabled
            and (settings.include_local_soc2 or selected_ids)
            and external_ready
            and not missing_ids
        )
        core.update(
            {
                "schema": "lightwork.trc-stock-collector-production-readiness.v1",
                "enabled": settings.feature_enabled,
                "stock_collectors_enabled": settings.stock_enabled,
                "ready": composition_ready,
                "composition_ready": composition_ready,
                "enabled_collector_ids": list(settings.enabled_collector_ids),
                "enabled_stock_collector_ids": list(
                    settings.enabled_stock_collector_ids
                ),
                "configured_stock_collector_ids": sorted(configured_ids),
                "missing_stock_collector_ids": list(missing_ids),
                "definition_and_enrollment_rechecked_per_job": True,
                "status_secret_resolution": False,
                "status_provider_network_io": False,
                "estate_completeness_claimed": False,
                "live_provider_acceptance_verified": False,
            }
        )
        return core

    def build_scheduler(self, **dependencies: Any) -> TrcContinuousEvidenceScheduler:
        forbidden = {"registry_factory", "credential_resolver"}.intersection(dependencies)
        if forbidden:
            raise TrcStockEvidenceCompositionError(
                "stock scheduler dependencies cannot replace registry authority"
            )
        return TrcContinuousEvidenceScheduler(
            registry_factory=self.registry_factory,
            credential_resolver=self.credential_resolver,
            **dependencies,
        )


def _attach(app: Any, composition: object) -> object:
    state = getattr(app, "state", None)
    if state is None:
        raise TypeError("app must expose state")
    required = ("registry_factory", "credential_resolver", "readiness", "build_scheduler")
    if not all(callable(getattr(composition, name, None)) for name in required):
        raise TypeError("stock evidence composition has the wrong type")
    marker = "trc_stock_evidence_composition"
    attached = getattr(state, marker, None)
    if attached is not None:
        if attached is composition:
            return composition
        raise TrcStockEvidenceCompositionError(
            "a different stock evidence composition is already attached"
        )
    installs = {
        "trc_stock_evidence_registry_factory": composition.registry_factory,
        "trc_stock_evidence_credential_resolver": composition.credential_resolver,
        "trc_stock_evidence_readiness_probe": composition.readiness,
        "trc_stock_evidence_scheduler_factory": composition.build_scheduler,
    }
    for name in installs:
        if getattr(state, name, None) is not None:
            raise TrcStockEvidenceCompositionError(
                "stock evidence app-state authority already exists"
            )
    setattr(state, marker, composition)
    for name, value in installs.items():
        setattr(state, name, value)
    return composition


def attach_trc_stock_evidence_composition(
    app: Any,
    composition: TrcStockEvidenceComposition,
) -> TrcStockEvidenceComposition:
    """Attach an explicitly constructed source directory for tests/embedders."""

    if not isinstance(composition, TrcStockEvidenceComposition):
        raise TypeError("stock evidence composition has the wrong type")
    return _attach(app, composition)  # type: ignore[return-value]


def install_trc_stock_evidence_composition(
    app: Any,
    composition: ConfigDrivenTrcStockEvidenceComposition | None = None,
) -> ConfigDrivenTrcStockEvidenceComposition:
    """Install the lazy tenant-config production composition."""

    selected = composition or ConfigDrivenTrcStockEvidenceComposition()
    return _attach(app, selected)  # type: ignore[return-value]


__all__ = [
    "ConfigDrivenTrcStockEvidenceComposition",
    "StockCollectorSettings",
    "TrcStockEvidenceComposition",
    "TrcStockEvidenceCompositionError",
    "attach_trc_stock_evidence_composition",
    "install_trc_stock_evidence_composition",
    "stock_collector_settings",
]
