"""Goal writes must use exact tenant ownership, even in compatibility mode."""
from __future__ import annotations

from contextlib import contextmanager

import maverick.world_model_backends.postgres as pg
import pytest


class _Cursor:
    rowcount = 1

    def __init__(self, rows=()):
        self.statements = []
        self.rows = list(rows)

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchall(self):
        return self.rows


@pytest.fixture
def tenant_world(monkeypatch):
    monkeypatch.setattr(pg, "_active_tenant", lambda: "tenant-b")
    monkeypatch.setattr(pg, "_strict_tenant_isolation", lambda: False)
    monkeypatch.setattr(pg, "_seal", lambda value: value)
    cursor = _Cursor()

    @contextmanager
    def tx():
        yield cursor

    world = object.__new__(pg.PostgresWorldModel)
    monkeypatch.setattr(world, "_tx", tx)
    return world, cursor


@pytest.mark.parametrize(
    "mutate",
    [
        lambda world: world.set_goal_partial_result(12, "partial"),
        lambda world: world.touch_goal(12, expected_owner="user:b"),
        lambda world: world.set_goal_domain(12, "operations"),
        lambda world: world.set_goal_title(12, "renamed"),
    ],
)
def test_goal_mutators_do_not_inherit_legacy_null_rows(tenant_world, mutate):
    world, cursor = tenant_world

    mutate(world)

    statement, params = cursor.statements[-1]
    assert "tenant_id = %s" in statement
    assert params[-1] == "tenant-b"


def test_goal_parent_change_requires_exact_same_tenant(tenant_world):
    world, cursor = tenant_world
    cursor.rows = [(12,), (34,)]

    world.set_goal_parent(12, 34)

    assert len(cursor.statements) == 2
    select, select_params = cursor.statements[0]
    update, update_params = cursor.statements[1]
    assert "tenant_id = %s" in select
    assert select_params == (12, 34, "tenant-b")
    assert "tenant_id = %s" in update
    assert update_params[-1] == "tenant-b"


def test_goal_parent_change_refuses_legacy_or_foreign_parent(tenant_world):
    world, cursor = tenant_world
    # The child is in tenant B, but a legacy NULL or foreign-tenant parent is
    # excluded by the exact predicate and therefore cannot receive the edge.
    cursor.rows = [(12,)]

    with pytest.raises(ValueError, match="same tenant"):
        world.set_goal_parent(12, 34)

    assert len(cursor.statements) == 1
    assert "tenant_id = %s" in cursor.statements[0][0]


def test_goal_parent_change_does_not_disclose_parent_when_child_is_unavailable(tenant_world):
    world, cursor = tenant_world
    cursor.rows = [(34,)]

    world.set_goal_parent(12, 34)

    assert len(cursor.statements) == 1
    assert "tenant_id = %s" in cursor.statements[0][0]
