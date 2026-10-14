"""Exact live-catalog fingerprints for reviewed production Postgres schemas.

The migration ledger proves which SQL was *recorded* as applied.  It cannot
prove that a partial restore, manual repair, or later owner DDL did not remove a
column, constraint, index, sequence, function, or trigger.  This module hashes
the live ``public`` catalog instead.  Every intentional migration therefore
requires a reviewed fingerprint update as well as ``migrations.lock.json``.

The queries deliberately describe semantic catalog state rather than OIDs,
which vary between databases.  Object ownership is normalized to the invariant
that every application object has the same owner as the dedicated ``public``
schema; the migration runner separately proves that owner is its current
NOSUPERUSER/NOBYPASSRLS role.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class PostgresSchemaManifestError(RuntimeError):
    """The live Postgres catalog is not an exact reviewed schema."""


_CATALOG_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "relation",
        """
        SELECT jsonb_build_array(
          c.relname,
          c.relkind,
          c.relpersistence,
          c.relowner = n.nspowner
        )::text
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
        ORDER BY c.relkind, c.relname
        """,
    ),
    (
        "column",
        """
        SELECT jsonb_build_array(
          c.relname,
          a.attnum,
          a.attname,
          pg_catalog.format_type(a.atttypid, a.atttypmod),
          a.attnotnull,
          a.attidentity,
          a.attgenerated,
          COALESCE(pg_catalog.pg_get_expr(d.adbin, d.adrelid, true), ''),
          COALESCE(coll.collname, '')
        )::text
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef AS d
          ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = a.attcollation
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
        """,
    ),
    (
        "constraint",
        """
        SELECT jsonb_build_array(
          COALESCE(c.relname, ''),
          con.conname,
          con.contype,
          pg_catalog.pg_get_constraintdef(con.oid, true),
          con.condeferrable,
          con.condeferred,
          con.convalidated,
          con.connoinherit
        )::text
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_namespace AS n ON n.oid = con.connamespace
        LEFT JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        WHERE n.nspname = 'public'
        ORDER BY COALESCE(c.relname, ''), con.conname
        """,
    ),
    (
        "index",
        """
        SELECT jsonb_build_array(
          table_class.relname,
          index_class.relname,
          access_method.amname,
          index_class.relowner = namespace.nspowner,
          index.indisunique,
          index.indisprimary,
          index.indisexclusion,
          index.indimmediate,
          index.indisvalid,
          index.indisready,
          index.indisreplident,
          index.indislive,
          pg_catalog.pg_get_indexdef(index.indexrelid),
          COALESCE(
            pg_catalog.pg_get_expr(index.indpred, index.indrelid, true),
            ''
          )
        )::text
        FROM pg_catalog.pg_index AS index
        JOIN pg_catalog.pg_class AS index_class
          ON index_class.oid = index.indexrelid
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = index.indrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = table_class.relnamespace
        JOIN pg_catalog.pg_am AS access_method
          ON access_method.oid = index_class.relam
        WHERE namespace.nspname = 'public'
        ORDER BY table_class.relname, index_class.relname
        """,
    ),
    (
        "sequence",
        """
        SELECT jsonb_build_array(
          sequence_class.relname,
          sequence_class.relowner = namespace.nspowner,
          pg_catalog.format_type(sequence.seqtypid, NULL),
          sequence.seqstart,
          sequence.seqincrement,
          sequence.seqmax,
          sequence.seqmin,
          sequence.seqcache,
          sequence.seqcycle,
          COALESCE(owner_class.relname, ''),
          COALESCE(owner_attribute.attname, '')
        )::text
        FROM pg_catalog.pg_sequence AS sequence
        JOIN pg_catalog.pg_class AS sequence_class
          ON sequence_class.oid = sequence.seqrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = sequence_class.relnamespace
        LEFT JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
         AND dependency.deptype IN ('a', 'i')
        LEFT JOIN pg_catalog.pg_class AS owner_class
          ON owner_class.oid = dependency.refobjid
        LEFT JOIN pg_catalog.pg_attribute AS owner_attribute
          ON owner_attribute.attrelid = dependency.refobjid
         AND owner_attribute.attnum = dependency.refobjsubid
        WHERE namespace.nspname = 'public'
        ORDER BY sequence_class.relname
        """,
    ),
    (
        "routine",
        """
        SELECT jsonb_build_array(
          routine.proname,
          pg_catalog.pg_get_function_identity_arguments(routine.oid),
          pg_catalog.pg_get_function_result(routine.oid),
          routine.prokind,
          language.lanname,
          routine.proowner = namespace.nspowner,
          routine.provolatile,
          routine.proparallel,
          routine.proisstrict,
          routine.prosecdef,
          routine.proleakproof,
          CASE
            WHEN routine.prokind IN ('f', 'p')
            THEN pg_catalog.pg_get_functiondef(routine.oid)
            ELSE ''
          END
        )::text
        FROM pg_catalog.pg_proc AS routine
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = routine.pronamespace
        JOIN pg_catalog.pg_language AS language
          ON language.oid = routine.prolang
        WHERE namespace.nspname = 'public'
        ORDER BY routine.proname,
                 pg_catalog.pg_get_function_identity_arguments(routine.oid)
        """,
    ),
    (
        "trigger",
        """
        SELECT jsonb_build_array(
          table_class.relname,
          trigger.tgname,
          trigger.tgenabled,
          trigger.tgdeferrable,
          trigger.tginitdeferred,
          pg_catalog.pg_get_triggerdef(trigger.oid, true),
          routine.proname,
          pg_catalog.pg_get_function_identity_arguments(routine.oid)
        )::text
        FROM pg_catalog.pg_trigger AS trigger
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = trigger.tgrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = table_class.relnamespace
        JOIN pg_catalog.pg_proc AS routine
          ON routine.oid = trigger.tgfoid
        WHERE namespace.nspname = 'public'
          AND NOT trigger.tgisinternal
        ORDER BY table_class.relname, trigger.tgname
        """,
    ),
    (
        "standalone-type",
        """
        SELECT jsonb_build_array(
          type.typname,
          type.typtype,
          pg_catalog.format_type(type.typbasetype, type.typtypmod),
          type.typnotnull,
          COALESCE(type.typdefault, ''),
          type.typowner = namespace.nspowner,
          COALESCE((
            SELECT jsonb_agg(enum.enumlabel ORDER BY enum.enumsortorder)
            FROM pg_catalog.pg_enum AS enum
            WHERE enum.enumtypid = type.oid
          ), '[]'::jsonb)
        )::text
        FROM pg_catalog.pg_type AS type
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = type.typnamespace
        WHERE namespace.nspname = 'public'
          AND type.typrelid = 0
          AND type.typtype IN ('d', 'e', 'r', 'm')
        ORDER BY type.typname
        """,
    ),
)


# Generated from the reviewed ladder against PostgreSQL 16.14.  The contract's
# live qualification separately binds the exact managed PostgreSQL offering and
# version before production admission.
_EXPECTED_SHA256_BY_HEAD: dict[int, str] = {
    0: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    41: "feb215cb99a31cc7501177e8a615988e2388554d98257babdef3088ed492ee1c",
    42: "821e82bff61f16a930eac52cb48843041d74c6688accc363d90ce551fabfed25",
}


def read_catalog_manifest(cur: Any) -> tuple[str, ...]:
    """Return a stable, exhaustive public-schema catalog manifest."""

    manifest: list[str] = []
    for category, query in _CATALOG_QUERIES:
        cur.execute(query)
        for row in cur.fetchall():
            if len(row) != 1 or not isinstance(row[0], str):
                raise PostgresSchemaManifestError(
                    f"Postgres {category} catalog returned an invalid row"
                )
            manifest.append(f"{category}\0{row[0]}")
    return tuple(manifest)


def catalog_manifest_sha256(cur: Any) -> str:
    """Hash the exact normalized public-schema catalog returned by Postgres."""

    encoded = json.dumps(
        read_catalog_manifest(cur),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def catalog_query_sha256() -> str:
    """Bind the fingerprint to the reviewed catalog-query semantics."""

    normalized = [
        [category, " ".join(query.split())]
        for category, query in _CATALOG_QUERIES
    ]
    encoded = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expected_catalog_sha256(head: int) -> str:
    """Return the reviewed live-catalog digest for one admitted schema head."""

    try:
        expected = _EXPECTED_SHA256_BY_HEAD[head]
    except KeyError as exc:
        raise PostgresSchemaManifestError(
            f"schema head {head} has no reviewed live-catalog fingerprint"
        ) from exc
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise PostgresSchemaManifestError(
            f"schema head {head} has no finalized live-catalog fingerprint"
        )
    return expected


def verify_catalog_manifest(cur: Any, *, head: int) -> str:
    """Fail closed unless the live catalog equals the reviewed fingerprint."""

    expected = expected_catalog_sha256(head)
    actual = catalog_manifest_sha256(cur)
    if actual != expected:
        raise PostgresSchemaManifestError(
            "live Postgres catalog differs from the reviewed schema fingerprint "
            f"for head {head}"
        )
    return actual


__all__ = [
    "PostgresSchemaManifestError",
    "catalog_manifest_sha256",
    "catalog_query_sha256",
    "expected_catalog_sha256",
    "read_catalog_manifest",
    "verify_catalog_manifest",
]
