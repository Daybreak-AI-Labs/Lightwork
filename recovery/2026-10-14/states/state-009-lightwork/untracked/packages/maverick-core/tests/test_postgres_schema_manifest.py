"""Adverse checks for exact live Postgres catalog fingerprints."""

from __future__ import annotations

import copy

import pytest
from maverick import postgres_schema_manifest as manifest

_ROWS = {
    "relation": [
        '["flow_authority_records", "r", "p", true]',
        '["tenant_quota_usage", "r", "p", true]',
    ],
    "column": [
        '["flow_authority_records", 4, "document", "text", true, "", "", "", "default"]',
        '["tenant_quota_usage", 7, "policy_generation", "bigint", true, "", "", "-1", "default"]',
    ],
    "constraint": [],
    "index": [
        '["facts", "uq_pg_facts_tenant_key", "btree", true, true, false, false, true, true, true, false, true, "CREATE UNIQUE INDEX uq_pg_facts_tenant_key ON public.facts USING btree (COALESCE(tenant_id, \'\'::text), key)", ""]',
    ],
    "sequence": [],
    "routine": [
        '["mvk_flow_authority_immutable", "", "trigger", "f", "plpgsql", true, "v", "u", false, false, false, "reviewed body"]',
    ],
    "trigger": [
        '["flow_authority_records", "flow_authority_no_immutable_update", "O", false, false, "CREATE TRIGGER flow_authority_no_immutable_update BEFORE UPDATE OR DELETE ON flow_authority_records FOR EACH ROW EXECUTE FUNCTION mvk_flow_authority_immutable()", "mvk_flow_authority_immutable", ""]',
    ],
    "standalone-type": [],
}


class _CatalogCursor:
    def __init__(self, rows):
        self.rows = rows
        self.current = []

    def execute(self, sql: str) -> None:
        markers = {
            "FROM pg_catalog.pg_class AS c": "relation",
            "FROM pg_catalog.pg_attribute AS a": "column",
            "FROM pg_catalog.pg_constraint AS con": "constraint",
            "FROM pg_catalog.pg_index AS index": "index",
            "FROM pg_catalog.pg_sequence AS sequence": "sequence",
            "FROM pg_catalog.pg_proc AS routine": "routine",
            "FROM pg_catalog.pg_trigger AS trigger": "trigger",
            "FROM pg_catalog.pg_type AS type": "standalone-type",
        }
        category = next(value for marker, value in markers.items() if marker in sql)
        self.current = [(value,) for value in self.rows[category]]

    def fetchall(self):
        return list(self.current)


def _accept_synthetic_manifest(monkeypatch, rows) -> None:
    digest = manifest.catalog_manifest_sha256(_CatalogCursor(rows))
    monkeypatch.setattr(manifest, "_EXPECTED_SHA256_BY_HEAD", {42: digest})


def test_reviewed_manifest_accepts_the_exact_live_catalog(monkeypatch) -> None:
    rows = copy.deepcopy(_ROWS)
    _accept_synthetic_manifest(monkeypatch, rows)

    assert manifest.verify_catalog_manifest(_CatalogCursor(rows), head=42)


@pytest.mark.parametrize(
    ("category", "marker"),
    [
        ("trigger", "flow_authority_no_immutable_update"),
        ("column", "policy_generation"),
        ("index", "uq_pg_facts_tenant_key"),
    ],
)
def test_exact_current_ledger_cannot_hide_partial_restore_catalog_drift(
    monkeypatch,
    category: str,
    marker: str,
) -> None:
    reviewed = copy.deepcopy(_ROWS)
    _accept_synthetic_manifest(monkeypatch, reviewed)
    corrupted = copy.deepcopy(reviewed)
    corrupted[category] = [row for row in corrupted[category] if marker not in row]

    with pytest.raises(
        manifest.PostgresSchemaManifestError,
        match="live Postgres catalog differs",
    ):
        manifest.verify_catalog_manifest(_CatalogCursor(corrupted), head=42)


def test_only_empty_adjacent_and_current_heads_have_reviewed_fingerprints() -> None:
    assert set(manifest._EXPECTED_SHA256_BY_HEAD) == {0, 41, 42}
    for head in (0, 41, 42):
        assert len(manifest.expected_catalog_sha256(head)) == 64

    with pytest.raises(
        manifest.PostgresSchemaManifestError,
        match="no reviewed live-catalog fingerprint",
    ):
        manifest.expected_catalog_sha256(40)
