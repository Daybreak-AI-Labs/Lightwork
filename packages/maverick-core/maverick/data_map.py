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
#: Categories that identify a person — the assets a DSAR search plan
#: should send a human to. Owned here, next to the classifier vocabulary
#: it subsets, so a new category is weighed for DSAR relevance in one place.
IDENTIFYING_CATEGORIES = frozenset({
    "email", "phone", "name", "address", "national_id",
    "date_of_birth", "ip_address", "location",
})

_ASSETS = GovernedRecordStore("data_map", "DMA", "data_asset")
_MAX_LIST = 500
#: The register's capacity, and a full-scan window one past it: posture
#: counts and the DSAR worksheet must see every asset, not an mtime-ranked
#: subset.
_MAX_RECORDS = 10_000
_SCAN_LIMIT = _MAX_RECORDS + 1
_MAX_LINKS = 50
_MAX_COLUMNS = 400
_MAX_IMPORT_ROWS = 10_000
_MAX_IMPORT_TABLES = 500
_MAX_SITES = 50
_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_GRAPH_TIMEOUT_SECONDS = 20.0
#: File-content scan bounds: a bounded sample of a site's document library,
#: not a crawler. Text formats only -- the deterministic classifier reads
#: characters, not office-binary containers.
_MAX_FILES_SCANNED = 25
_MAX_FILE_SAMPLE_CHARS = 100_000
_MAX_FILE_BYTES = 5_000_000
_TEXT_FILE_SUFFIXES = (".csv", ".json", ".log", ".md", ".ndjson",
                       ".tsv", ".txt", ".yaml", ".yml")
#: Graph identifiers appear inside request paths; refuse anything that could
#: smuggle path segments or query strings (site ids are hostname,guid,guid
#: composites; item ids are base64ish and may contain "!").
_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9!_.,-]{1,512}$")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(expression))
    for category, expression in (
        ("email", r"(?:^|[_-])e[_-]?mail(?:$|[_-])"),
        ("phone", r"(?:^|[_-])(?:tele[_-]?phone|phone|mobile|msisdn|fax)"
                  r"(?:$|[_-])"),
        ("name", r"(?:^|_)(?:first|last|middle|full|sur|given|family|maiden)"
                 r"[_-]?name|(?:^|_)name(?:$|_)"),
        ("address", r"address|street|postcode|postal|zip[_-]?code|city"),
        ("national_id", r"ssn|social[_-]?security|passport|national[_-]?id|"
                        r"tax[_-]?id|driver[_-]?licen[cs]e|nino|cpf|aadhaar"),
        ("date_of_birth", r"(?:birth[_-]?date|date[_-]?of[_-]?birth|"
                          r"(?:^|_)dob(?:$|_)|birthday)"),
        ("financial", r"iban|swift|account[_-]?number|routing|credit[_-]?card|"
                      r"card[_-]?number|(?:^|_)pan(?:$|_)|salary|payroll"),
        ("health", r"(?:^|[_-])health(?:$|[_-])|medical|diagnos|prescription|"
                   r"allerg|disabilit|blood[_-]?type|icd[_-]?10"),
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

    # CamelCase carries its word boundaries in case; materialize them as
    # underscores BEFORE lowering so the anchored patterns still see them
    # ("MobileNumber" -> "mobile_number"). The anchors are what keep
    # "mail_server", "microphone_enabled" and "healthcheck_status" clean.
    lowered = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])", "_", str(column_name or "").strip()
    ).lower()
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
    max_records: int | None = _MAX_RECORDS,
) -> dict[str, Any]:
    """Register or refresh one data asset; re-registration reclassifies.

    ``max_records`` is the capacity cap enforced on create; bulk importers
    that pre-check capacity once for the whole batch pass ``None`` so each
    row does not repeat the store's full-namespace capacity scan.
    """

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
                max_records=max_records,
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
    rows = _ASSETS.list(limit=_SCAN_LIMIT)
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
    rows = _ASSETS.list(limit=_SCAN_LIMIT)
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
    from .csv_alias import alias_rows

    tables: dict[str, list[str]] = {}
    skipped = 0
    for entry in alias_rows(csv_text, _SCHEMA_CSV_HEADERS,
                            limit=_MAX_IMPORT_ROWS):
        if not entry.get("table") or not entry.get("column"):
            skipped += 1
            continue
        qualified = entry["table"]
        if entry.get("schema"):
            qualified = f"{entry['schema']}.{qualified}"
        columns = tables.setdefault(qualified, [])
        if len(columns) < _MAX_COLUMNS:
            columns.append(entry["column"])
    # Capacity is checked ONCE for the whole batch: the store's per-create
    # capacity check is a full namespace scan, which would turn a 500-table
    # import into 500 scans. Existing tables refresh in place and consume
    # no budget; new tables past the cap are skipped, not raised.
    system_name = _text(system, "system", 256)
    existing = {
        str(row.get("id") or "") for row in _ASSETS.list(limit=_SCAN_LIMIT)
    }
    budget = _MAX_RECORDS - len(existing)
    imported = 0
    for qualified, columns in list(tables.items())[:_MAX_IMPORT_TABLES]:
        is_new = _asset_id(system_name, qualified) not in existing
        if is_new and budget <= 0:
            skipped += 1
            continue
        try:
            register_data_asset(
                qualified,
                system=system_name,
                kind="table",
                owner=owner,
                actor=imported_by or "import:information_schema",
                columns=columns,
                source="information_schema",
                max_records=None,
            )
            imported += 1
            if is_new:
                budget -= 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


#: Cap on one content-verification sample; the classifier has its own
#: global ceiling, this keeps register writes bounded.
_MAX_CONTENT_SAMPLE = 200_000


def classify_asset_content(
    asset_id: str,
    text_sample: str,
    *,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Content-verify one asset from a bounded sample of its actual data.

    Where :func:`classify_column` reads NAMES, this runs the
    deterministic content classifier over a real sample (rows the
    operator pastes or pipes in) and records what was actually FOUND —
    ``content_categories`` with per-category counts, merged into the
    asset's categories. A content-verified category is evidence; a
    name-suggested one is a hint, and the record now distinguishes them.
    """

    _require_enabled()
    from .content_classifier import classify_text

    operator = _text(actor, "actor", 256)
    sample = str(text_sample or "")
    if not sample.strip():
        raise ValueError("text_sample must not be empty")
    if len(sample) > _MAX_CONTENT_SAMPLE:
        raise ValueError(
            f"text_sample must be at most {_MAX_CONTENT_SAMPLE} characters")
    report = classify_text(sample)

    def mutate(value: dict[str, Any]) -> None:
        merged = sorted(
            set(value.get("categories") or []) | set(report["categories"])
        )
        value["categories"] = merged
        value["content_categories"] = report["categories"]
        value["content_counts"] = report["counts"]
        value["content_verified_at"] = time.time()
        value["content_verified_by"] = operator
        value["special_category"] = bool(
            SPECIAL_CATEGORIES.intersection(merged))

    saved = _ASSETS.update(
        _text(asset_id, "asset_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="classify_asset_content",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"data asset is not registered: {asset_id}")
    return dict(saved)


def _sql_identifier(part: str) -> str:
    return '"' + part.replace('"', '""').upper() + '"'


def masking_policy_sql(
    *, system: str = "snowflake", unmasked_role: str = "DATA_STEWARD",
) -> str:
    """Deterministic Snowflake masking-policy DDL from the data map.

    The classifier's findings, pushed to where the data lives: one
    ``CREATE MASKING POLICY IF NOT EXISTS`` per flagged category and one
    ``ALTER TABLE ... SET MASKING POLICY`` per flagged column, for every
    ``table`` asset in the named system. ``unmasked_role`` names the
    Snowflake role that sees clear text. A column flagged with several
    categories gets the first policy and a comment naming the rest —
    Snowflake allows one masking policy per column. Emitted as SQL text
    for the operator's review pipeline — Lightwork never executes it,
    and the header says so. Snowflake only in v1; other engines are a
    decision.
    """

    _require_enabled()
    engine = str(system or "").strip().lower()
    if engine != "snowflake":
        raise ValueError("only system='snowflake' is supported in v1")
    role = str(unmasked_role or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", role):
        raise ValueError(
            "unmasked_role must be an UPPER_SNAKE_CASE identifier")
    rows = sorted(
        (
            row for row in _ASSETS.list(limit=_SCAN_LIMIT)
            if row.get("kind") == "table" and (row.get("flagged_columns"))
        ),
        key=lambda row: str(row.get("name") or ""),
    )
    categories_in_use = sorted(
        {
            category
            for row in rows
            for cols in (row.get("flagged_columns") or {}).values()
            for category in cols
        }
    )
    lines = [
        "-- Lightwork data-map masking policies (Snowflake).",
        "-- Generated from the governed register; REVIEW before applying.",
        "-- Lightwork emits this DDL and never executes it.",
        "",
    ]
    for category in categories_in_use:
        policy = _sql_identifier(f"LIGHTWORK_MASK_{category}")
        lines.append(
            f"CREATE MASKING POLICY IF NOT EXISTS {policy} AS "
            "(val string) RETURNS string ->\n"
            f"  CASE WHEN CURRENT_ROLE() IN ('{role}') THEN val "
            f"ELSE '[MASKED:{category}]' END;"
        )
    for row in rows:
        table = ".".join(
            _sql_identifier(part)
            for part in str(row.get("name") or "").split(".")
        )
        for column, cats in sorted(
            (row.get("flagged_columns") or {}).items()
        ):
            if not cats:
                continue
            ordered = sorted(cats)
            policy = _sql_identifier(f"LIGHTWORK_MASK_{ordered[0]}")
            extra = (
                f"  -- also flagged: {', '.join(ordered[1:])} "
                "(Snowflake allows one policy per column)"
                if len(ordered) > 1 else ""
            )
            lines.append(
                f"ALTER TABLE {table} MODIFY COLUMN "
                f"{_sql_identifier(column)} SET MASKING POLICY {policy};"
                + extra
            )
    if not rows:
        lines.append("-- No flagged table assets in the register yet.")
    return "\n".join(lines) + "\n"


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


def _default_graph_download(url: str, token: str) -> str:
    """Download one drive item's text through the SSRF-pinned client.

    ``GET .../content`` answers ``302 Found`` with a preauthenticated
    download URL in ``Location`` (no Authorization header on that hop --
    sending the bearer to a non-Graph host would leak it). The pinned
    client never follows redirects itself; the one expected hop is
    re-validated through ``safe_client`` like any other URL.
    """

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
        if response.status_code in (301, 302, 307, 308):
            target = str(response.headers.get("location") or "")
            if not target.startswith("https://"):
                raise DataMapError(
                    "Microsoft Graph redirected to a non-https URL")
            deny = enterprise_egress_denial(target, tool="data_map")
            if deny:
                raise DataMapError(deny)
            with safe_client(target, timeout=_GRAPH_TIMEOUT_SECONDS) as hop:
                response = hop.get(target)
    except BlockedHost as exc:
        raise DataMapError("blocked host (SSRF guard)") from exc
    if response.status_code != 200:
        raise DataMapError(
            f"file download returned HTTP {response.status_code}")
    return response.text[:_MAX_FILE_SAMPLE_CHARS]


def scan_sharepoint_site_files(
    asset_id: str,
    *,
    site_id: str,
    actor: str,
    expected_revision: int,
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    downloader: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Content-scan a SharePoint site's document library into its asset.

    Lists ``GET /sites/{site-id}/drive/root/children`` (application
    permission ``Files.Read.All``; ``Sites.Read.All`` also grants it), then
    samples up to ``_MAX_FILES_SCANNED`` text-format files via
    ``GET .../items/{id}/content`` and runs the deterministic content
    classifier over each. What lands on the asset is EVIDENCE, not
    content: per-category counts, which file names were flagged, and the
    verification stamp -- never a byte of the files themselves.

    ``site_id`` is the Graph site identifier from the census
    (``sync_sharepoint_sites``). One listing page, bounded -- a sample,
    not a crawler; ``skipped`` counts what the bounds excluded.
    """

    settings = _require_enabled()
    from .content_classifier import classify_text

    operator = _text(actor, "actor", 256)
    site = str(site_id or "").strip()
    if not _GRAPH_ID_RE.fullmatch(site):
        raise ValueError("site_id must be a Microsoft Graph site identifier")
    if fetcher is None or downloader is None:
        import os

        token = os.environ.get(settings["graph_token_env"], "")
        if not token:
            raise DataMapConfigError(
                "the SharePoint file scan needs a Microsoft Graph token in "
                f"${settings['graph_token_env']} (Files.Read.All)"
            )
        if fetcher is None:
            def fetcher(target: str) -> dict[str, Any]:
                return _default_graph_fetch(target, token)
        if downloader is None:
            def downloader(target: str) -> str:
                return _default_graph_download(target, token)

    listing = fetcher(
        f"{_GRAPH_ROOT}/sites/{site}/drive/root/children"
        f"?$top={_MAX_FILES_SCANNED}")
    rows = listing.get("value")
    if not isinstance(rows, list):
        raise DataMapError("Microsoft Graph returned an unexpected shape")

    counts: dict[str, int] = {}
    flagged_files: list[dict[str, Any]] = []
    sampled = 0
    skipped = 0
    for row in rows[:_MAX_FILES_SCANNED]:
        if not isinstance(row, dict) or "file" not in row:
            skipped += 1
            continue
        name = str(row.get("name") or "")
        item_id = str(row.get("id") or "")
        size = int(row.get("size") or 0)
        if (not name.lower().endswith(_TEXT_FILE_SUFFIXES)
                or not _GRAPH_ID_RE.fullmatch(item_id)
                or size > _MAX_FILE_BYTES):
            skipped += 1
            continue
        try:
            sample = downloader(
                f"{_GRAPH_ROOT}/sites/{site}/drive/items/{item_id}/content")
        except DataMapError:
            skipped += 1
            continue
        sampled += 1
        report = classify_text(sample[:_MAX_FILE_SAMPLE_CHARS])
        if report["categories"]:
            flagged_files.append(
                {"name": name[:256], "categories": report["categories"]})
        for category, n in report["counts"].items():
            counts[category] = counts.get(category, 0) + int(n)

    found = sorted(counts)

    def mutate(value: dict[str, Any]) -> None:
        merged = sorted(set(value.get("categories") or []) | set(found))
        value["categories"] = merged
        value["content_categories"] = found
        value["content_counts"] = dict(sorted(counts.items()))
        value["content_verified_at"] = time.time()
        value["content_verified_by"] = operator
        value["special_category"] = bool(
            SPECIAL_CATEGORIES.intersection(merged))
        value["file_scan"] = {
            "site_id": site,
            "files_listed": len(rows),
            "files_sampled": sampled,
            "files_skipped": skipped,
            "flagged_files": flagged_files[:_MAX_FILES_SCANNED],
        }

    saved = _ASSETS.update(
        _text(asset_id, "asset_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="scan_sharepoint_site_files",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"data asset is not registered: {asset_id}")
    return dict(saved)


__all__ = [
    "ASSET_KINDS",
    "ASSET_SCHEMA",
    "CLASSIFIER_CATEGORIES",
    "DataMapConfigError",
    "DataMapError",
    "IDENTIFYING_CATEGORIES",
    "LINK_KINDS",
    "classify_asset_content",
    "masking_policy_sql",
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
    "scan_sharepoint_site_files",
    "sync_sharepoint_sites",
]
