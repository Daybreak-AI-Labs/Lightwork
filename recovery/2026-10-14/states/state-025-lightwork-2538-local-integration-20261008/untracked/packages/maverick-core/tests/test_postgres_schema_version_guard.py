from __future__ import annotations

import pytest


class _MigrationCursor:
    def __init__(self, version: int | None):
        self.version = version
        self.query = ""

    def execute(self, query: str) -> None:
        self.query = query

    def fetchone(self):
        if "to_regclass" in self.query:
            return ("schema_migrations" if self.version is not None else None,)
        return (self.version,)


def test_postgres_refuses_schema_ahead_of_this_installation():
    from maverick.world_model_backends.postgres import (
        _PG_SCHEMA_VERSION,
        PostgresWorldModel,
    )

    with pytest.raises(RuntimeError, match="schema is newer than this Lightwork"):
        PostgresWorldModel._migration_version(_MigrationCursor(_PG_SCHEMA_VERSION + 1))


def test_postgres_accepts_current_or_uninitialized_schema():
    from maverick.world_model_backends.postgres import (
        _PG_SCHEMA_VERSION,
        PostgresWorldModel,
    )

    assert PostgresWorldModel._migration_version(_MigrationCursor(_PG_SCHEMA_VERSION)) == _PG_SCHEMA_VERSION
    assert PostgresWorldModel._migration_version(_MigrationCursor(None)) is None
