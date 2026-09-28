"""All-settings catalog rows for the learning group.

Kept apart from settings_catalog.py so each group can be extended without
editing the shared catalog. entries receives the catalog's own row and
column types (a plain import would be circular)."""
from __future__ import annotations


def entries(S, C, Column) -> tuple:  # noqa: ARG001 -- filled by the group
    return ()
