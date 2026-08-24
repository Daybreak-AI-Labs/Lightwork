"""Data map: the governed inventory of where personal data actually lives.

The discovery half of a privacy program: a register of data assets (tables,
sites, shares, datasets) with a pure-Python column classifier that flags
likely personal-data categories, a warehouse importer that reads an
``information_schema.columns`` CSV export, and a SharePoint connector that
enumerates sites via Microsoft Graph. Assets link to the specialist records
that govern them (RoPA entries, assessments, vendors, AI systems).

Classification is name-based heuristics over column and asset names — a
triage signal for a human data-mapping exercise, not a scanner that has
read the data. Never legal advice.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from .governed_records import GovernedRecordStore

ASSET_SCHEMA = "lightwork.data-asset.v1"
ASSET_KINDS = ("table", "dataset", "file_share", "site", "bucket", "other")
LINK_KINDS = ("ropa", "assessment", "vendor", "ai_system")

#: Personal-data categories the classifier can flag, by column-name pattern.
CLASSIFIER_CATEGORIES = (
    "email", "phone", "name", "address", "national_id", "date_of_birth",
    "financial", "health", "credentials", "ip_address", "location",
)
#: GDPR Art. 9-adjacent categories that mark an asset special-category.
SPECIAL_CATEGORIES = frozenset({"health"})

_ASSETS = GovernedRecordStore("data_map", "DMA", "data_asset")
_MAX_LIST = 500
_MAX_LINKS = 50
_MAX_COLUMNS = 400
_MAX_IMPORT_ROWS = 10_000
_MAX_IMPORT_TABLES = 500
_MAX_SITES = 50
_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_GRAPH_TIMEOUT_SECONDS = 20.0

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(expression))
    for category, expression in (
        ("email", r"e?[_-]?mail"),
        ("phone", r"phone|mobile|msisdn|fax"),
        ("name", r"(?:^|_)(?:first|last|middle|full|sur|given|family|maiden)"
                 r"[_-]?name|(?:^|_)name(?:$|_)"),
        ("address", r"address|street|postcode|postal|zip[_-]?code|city"),
        ("national_id", r"ssn|social[_-]?security|passport|national[_-]?id|"
                        r"tax[_-]?id|driver[_-]?licen[cs]e|nino|cpf|aadhaar"),
        ("date_of_birth", r"(?:birth[_-]?date|date[_-]?of[_-]?birth|"
                          r"(?:^|_)dob(?:$|_)|birthday)"),
        ("financial", r"iban|swift|account[_-]?number|routing|credit[_-]?card|"
                      r"card[_-]?number|(?:^|_)pan(?:$|_)|salary|payroll"),
        ("health", r"health|medical|diagnos|prescription|allerg|disabilit|"
                   r"blood[_-]?type|icd[_-]?10"),
        ("credentials", r"password|passwd|secret|api[_-]?key|token|"
                        r"private[_-]?key|credential"),
        ("ip_address", r"ip[_-]?addr|(?:^|_)ipv?[46]?(?:$|_)"),
        ("location", r"latitude|longitude|geo[_-]?|gps"),
    )
)


class DataMapError(RuntimeError):
    """Base failure for the governed data map."""


class DataMapConfigError(DataMapError):
    """The feature is not enabled or configured safely."""


def get_data_map_config() -> dict[str, Any]:
    from .config import load_global_config

    try:
        config = load_global_config()
    except Exception as exc:
        raise DataMapConfigError("data_map configuration is unavailable") from exc
    section = (config or {}).get("data_map") or {}
    if not isinstance(section, dict):
        raise DataMapConfigError("data_map must be a table")
    flag = section.get("enable", False)
    if not isinstance(flag, bool):
        raise DataMapConfigError("data_map.enable must be a boolean")
    token_env = str(
        section.get("graph_token_env") or "MAVERICK_GRAPH_TOKEN"
    ).strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", token_env):
        raise DataMapConfigError(
            "data_map.graph_token_env must name an environment variable "
            "(UPPER_SNAKE_CASE)"
        )
    return {"enable": flag, "graph_token_env": token_env}


def enabled() -> bool:
    try:
        return get_data_map_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> dict[str, Any]:
    settings = get_data_map_config()
    if settings["enable"] is not True:
        raise DataMapConfigError("the data map requires [data_map] enable = true")
    return settings


def _text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must be 1-{limit} characters")
    return text


def classify_column(column_name: str) -> list[str]:
    """The categories a column name suggests; empty when nothing matches."""

    lowered = str(column_name or "").strip().lower()
    if not lowered:
        return []
    return [
        category for category, pattern in _PATTERNS if pattern.search(lowered)
    ]


def classify_columns(column_names: list[str]) -> dict[str, list[str]]:
    """Map each flagged column to its categories (clean columns omitted)."""

    flagged: dict[str, list[str]] = {}
    for raw in list(column_names)[:_MAX_COLUMNS]:
        categories = classify_column(str(raw))
        if categories:
            flagged[str(raw).strip()] = categories
    return flagged


def _asset_id(system: str, name: str) -> str:
    return (
        "DMA-"
        + hashlib.sha256(
            f"{system.lower()}:{name.lower()}".encode()
        ).hexdigest()[:24]
    )


def _categories_of(flagged: Mapping[str, list[str]]) -> list[str]:
    found: set[str] = set()
    for categories in flagged.values():
        found.update(categories)
    return sorted(found)


def register_data_asset(
    name: str,
    *,
    system: str,
    kind: str,
    owner: str,
    actor: str,
    columns: list[str] | None = None,
    location: str = "",
    source: str = "manual",
) -> dict[str, Any]:
    """Register or refresh one data asset; re-registration reclassifies."""

    _require_enabled()
    if kind not in ASSET_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(ASSET_KINDS)}")
    asset_name = _text(name, "name", 512)
    system_name = _text(system, "system", 256)
    steward = _text(owner, "owner", 256)
    operator = _text(actor, "actor", 256)
    origin = _text(source, "source", 64)
    where = str(location or "").strip()[:1024]
    names = [str(column) for column in (columns or [])][:_MAX_COLUMNS]
    flagged = classify_columns(names)
    categories = _categories_of(flagged)
    record_id = _asset_id(system_name, asset_name)
    body = {
        "name": asset_name,
        "system": system_name,
        "kind": kind,
        "owner": steward,
        "location": where,
        "source": origin,
        "column_count": len(names),
        "flagged_columns": flagged,
        "categories": categories,
        "special_category": bool(SPECIAL_CATEGORIES.intersection(categories)),
        "last_classified_at": time.time(),
    }
    current = _ASSETS.get(record_id)
    if current is None:
        record = {
            "id": record_id,
            "schema": ASSET_SCHEMA,
            "links": [],
            "registered_at": time.time(),
            **body,
        }
        return dict(
            _ASSETS.create(
                record,
                action="register_data_asset",
                actor=operator,
                max_records=10_000,
            )
        )

    def mutate(value: dict[str, Any]) -> None:
        value.update(body)

    saved = _ASSETS.update(
        record_id,
        mutate,
        expected_revision=int(current.get("revision") or 0),
        action="register_data_asset",
        actor=operator,
    )
    if saved is None:
        raise DataMapError("the data asset changed during save")
    return dict(saved)


def link_asset_record(
    asset_id: str,
    *,
    kind: str,
    reference: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Attach one specialist record (by reference) to the asset row."""

    _require_enabled()
    if kind not in LINK_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(LINK_KINDS)}")
    operator = _text(actor, "actor", 256)
    where = _text(reference, "reference", 512)

    def mutate(value: dict[str, Any]) -> None:
        links = list(value.get("links") or [])
        links.append(
            {
                "kind": kind,
                "reference": where,
                "linked_by": operator,
                "linked_at": time.time(),
            }
        )
        value["links"] = links[-_MAX_LINKS:]

    saved = _ASSETS.update(
        _text(asset_id, "asset_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="link_asset_record",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"data asset is not registered: {asset_id}")
    return dict(saved)


def get_data_asset(asset_id: str) -> dict[str, Any] | None:
    _require_enabled()
    row = _ASSETS.get(_text(asset_id, "asset_id", 64))
    return dict(row) if row else None


def list_data_assets(*, system: str | None = None) -> list[dict[str, Any]]:
    _require_enabled()
    rows = _ASSETS.list(limit=_MAX_LIST)
    if system:
        wanted = str(system).strip().lower()
        rows = [
            row for row in rows
            if str(row.get("system") or "").lower() == wanted
        ]
    rows.sort(key=lambda row: (str(row.get("system")), str(row.get("name"))))
    return [dict(row) for row in rows]


def data_map_posture() -> dict[str, Any]:
    _require_enabled()
    rows = _ASSETS.list(limit=_MAX_LIST * 4)
    by_system: dict[str, int] = {}
    by_category: dict[str, int] = {}
    flagged = 0
    special = 0
    unlinked_flagged = 0
    for row in rows:
        system = str(row.get("system") or "unknown")
        by_system[system] = by_system.get(system, 0) + 1
        categories = list(row.get("categories") or [])
        if categories:
            flagged += 1
            if not row.get("links"):
                unlinked_flagged += 1
        if row.get("special_category"):
            special += 1
        for category in categories:
            by_category[category] = by_category.get(category, 0) + 1
    return {
        "assets": len(rows),
        "flagged_assets": flagged,
        "special_category_assets": special,
        "unlinked_flagged_assets": unlinked_flagged,
        "by_system": dict(sorted(by_system.items())),
        "by_category": dict(sorted(by_category.items())),
    }


#: information_schema.columns export aliases (case-insensitive).
_SCHEMA_CSV_HEADERS = {
    "table": ("table_name", "table", "object_name"),
    "schema": ("table_schema", "schema", "schema_name", "database"),
    "column": ("column_name", "column", "field", "field_name"),
}


def import_information_schema_csv(
    csv_text: str,
    *,
    system: str,
    owner: str,
    imported_by: str = "",
) -> dict[str, int]:
    """Import a warehouse ``information_schema.columns`` CSV as table assets.

    Works with the stock exports of Snowflake, BigQuery, Redshift, and
    Postgres (``SELECT table_schema, table_name, column_name FROM
    information_schema.columns``). One asset per table, columns classified.
    """

    _require_enabled()
    import csv as _csv
    import io

    reader = _csv.DictReader(io.StringIO(csv_text or ""))
    tables: dict[str, list[str]] = {}
    skipped = 0
    for row in list(reader)[:_MAX_IMPORT_ROWS]:
        low = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
        }
        entry: dict[str, str] = {}
        for field, aliases in _SCHEMA_CSV_HEADERS.items():
            for alias in aliases:
                if low.get(alias):
                    entry[field] = low[alias]
                    break
        if not entry.get("table") or not entry.get("column"):
            skipped += 1
            continue
        qualified = entry["table"]
        if entry.get("schema"):
            qualified = f"{entry['schema']}.{qualified}"
        columns = tables.setdefault(qualified, [])
        if len(columns) < _MAX_COLUMNS:
            columns.append(entry["column"])
    imported = 0
    for qualified, columns in list(tables.items())[:_MAX_IMPORT_TABLES]:
        try:
            register_data_asset(
                qualified,
                system=system,
                kind="table",
                owner=owner,
                actor=imported_by or "import:information_schema",
                columns=columns,
                source="information_schema",
            )
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


def _default_graph_fetch(url: str, token: str) -> dict[str, Any]:
    """GET through the SSRF-pinned, enterprise-egress-guarded client."""

    from .enterprise import enterprise_egress_denial
    from .tools._ssrf import BlockedHost, safe_client

    deny = enterprise_egress_denial(url, tool="data_map")
    if deny:
        raise DataMapError(deny)
    try:
        with safe_client(url, timeout=_GRAPH_TIMEOUT_SECONDS) as client:
            response = client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
    except BlockedHost as exc:
        raise DataMapError("blocked host (SSRF guard)") from exc
    if response.status_code != 200:
        raise DataMapError(
            f"Microsoft Graph returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise DataMapError("Microsoft Graph returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise DataMapError("Microsoft Graph returned an unexpected shape")
    return payload


def sync_sharepoint_sites(
    *,
    actor: str,
    owner: str,
    search: str = "",
    fetcher: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Enumerate SharePoint sites via Microsoft Graph into site assets.

    Uses ``GET /sites?search={query}`` when a query is given, else
    ``GET /sites/getAllSites`` (both need the ``Sites.Read.All``
    application permission). One page, bounded — a census, not a crawler.
    """

    settings = _require_enabled()
    query = str(search or "").strip()[:256]
    if fetcher is None:
        import os
        from urllib.parse import quote

        token = os.environ.get(settings["graph_token_env"], "")
        if not token:
            raise DataMapConfigError(
                "the SharePoint sync needs a Microsoft Graph token in "
                f"${settings['graph_token_env']} (Sites.Read.All)"
            )
        if query:
            url = f"{_GRAPH_ROOT}/sites?search={quote(query)}"
        else:
            url = f"{_GRAPH_ROOT}/sites/getAllSites"

        def fetch(target: str) -> dict[str, Any]:
            return _default_graph_fetch(target, token)

        payload = fetch(url)
    else:
        suffix = f"sites?search={query}" if query else "sites/getAllSites"
        payload = fetcher(f"{_GRAPH_ROOT}/{suffix}")
    rows = payload.get("value")
    if not isinstance(rows, list):
        raise DataMapError("Microsoft Graph returned an unexpected shape")
    imported = 0
    skipped = 0
    for row in rows[:_MAX_SITES]:
        if not isinstance(row, dict):
            skipped += 1
            continue
        name = str(
            row.get("displayName") or row.get("name") or ""
        ).strip()
        if not name:
            skipped += 1
            continue
        try:
            register_data_asset(
                name,
                system="sharepoint",
                kind="site",
                owner=owner,
                actor=actor,
                location=str(row.get("webUrl") or "").strip(),
                source="microsoft_graph",
            )
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


__all__ = [
    "ASSET_KINDS",
    "ASSET_SCHEMA",
    "CLASSIFIER_CATEGORIES",
    "DataMapConfigError",
    "DataMapError",
    "LINK_KINDS",
    "SPECIAL_CATEGORIES",
    "classify_column",
    "classify_columns",
    "data_map_posture",
    "enabled",
    "get_data_asset",
    "get_data_map_config",
    "import_information_schema_csv",
    "link_asset_record",
    "list_data_assets",
    "register_data_asset",
    "sync_sharepoint_sites",
]
