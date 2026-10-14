"""Fake-transaction parity proof for Postgres sign-off authority."""
from __future__ import annotations

from contextlib import contextmanager

from maverick.api_idempotency import request_sha256
from maverick.signoff_authority import policy_binding
from maverick.world_model_backends import postgres

_OPERATION = "POST /api/v1/goals/{goal_id}/signoff"


class _AuthorityCursor:
    def __init__(self):
        self.goals = {7: ("done", 123.5, "finance_cash13w")}
        self.outbox = {}
        self.signoffs = {}
        self._rows = []
        self.rowcount = 0
        self.sql = []

    def execute(self, sql, params=()):  # noqa: C901 - SQL fake models one cursor.
        normalized = " ".join(sql.split())
        lowered = normalized.lower()
        values = tuple(params or ())
        self.sql.append((normalized, values))
        self._rows = []
        self.rowcount = 0

        if lowered.startswith("select status, updated_at, domain from goals"):
            goal = self.goals.get(int(values[0]))
            self._rows = [goal] if goal is not None else []
        elif lowered.startswith("select domain, updated_at from goals"):
            goal = self.goals.get(int(values[0]))
            self._rows = [(goal[2], goal[1])] if goal is not None else []
        elif lowered.startswith("update goals set domain=%s, updated_at=%s"):
            goal = self.goals.get(int(values[2]))
            if goal is not None:
                self.goals[int(values[2])] = (goal[0], values[1], values[0])
                self.rowcount = 1
        elif lowered.startswith("insert into signoff_decision_outbox"):
            scope = (str(values[1] or ""), values[2], values[3], values[4])
            existing = next(
                (row for row in self.outbox.values()
                 if (str(row[1] or ""), row[2], row[3], row[4]) == scope),
                None,
            )
            if existing is None and values[0] not in self.outbox:
                self.outbox[values[0]] = (
                    *values[:14],
                    "PENDING_AUDIT",
                    values[14],
                    values[15],
                    None,
                )
                self.rowcount = 1
        elif (
            lowered.startswith("select event_id, tenant_id, operation")
            and "principal_id = %s and idempotency_key = %s" in lowered
        ):
            scope = (str(values[0]), values[1], values[2], values[3])
            row = next(
                (candidate for candidate in self.outbox.values()
                 if (
                     str(candidate[1] or ""), candidate[2], candidate[3], candidate[4]
                 ) == scope),
                None,
            )
            self._rows = [row] if row is not None else []
        elif (
            lowered.startswith("select event_id, tenant_id, operation")
            and "where event_id=%s" in lowered
        ):
            row = self.outbox.get(values[0])
            self._rows = [row] if row is not None else []
        elif lowered.startswith(
            "select goal_id from signoff_decision_outbox where event_id=%s"
        ):
            row = self.outbox.get(values[0])
            self._rows = [(row[7],)] if row is not None else []
        elif lowered.startswith(
            "select goal_domain, governing_gate, policy_sha256 from signoffs"
        ):
            signed = self.signoffs.get(int(values[0]))
            self._rows = [(signed[6], signed[7], signed[8])] if signed else []
        elif (
            lowered.startswith("select event_id from signoff_decision_outbox")
            and "deliverable_updated_at=%s" in lowered
        ):
            tenant, goal_id, version, excluded = values[:4]
            rows = [
                (row[0],)
                for row in self.outbox.values()
                if str(row[1] or "") == tenant
                and int(row[7]) == int(goal_id)
                and float(row[8]) == float(version)
                and row[0] != excluded
                and row[14] in {"PENDING_AUDIT", "EFFECTIVE"}
            ]
            self._rows = rows[:1]
        elif lowered.startswith(
            "select decision, decided_by, deliverable_updated_at, audit_event_id"
        ):
            signed = self.signoffs.get(int(values[0]))
            self._rows = [
                (signed[0], signed[1], signed[4], signed[5], signed[6], signed[7], signed[8])
            ] if signed is not None else []
        elif lowered.startswith(
            "select audit_event_id, goal_domain, governing_gate, policy_sha256"
        ):
            signed = self.signoffs.get(int(values[0]))
            self._rows = [
                (signed[5], signed[6], signed[7], signed[8])
            ] if signed is not None else []
        elif lowered.startswith("select audit_event_id from signoffs"):
            signed = self.signoffs.get(int(values[0]))
            self._rows = [(signed[5],)] if signed is not None else []
        elif lowered.startswith("insert into signoffs"):
            self.signoffs[int(values[0])] = (
                values[1], values[2], values[3], values[4], values[5], values[6],
                values[7], values[8], values[9],
            )
            self.rowcount = 1
        elif lowered.startswith("delete from signoffs"):
            signed = self.signoffs.get(int(values[0]))
            if signed is not None and (len(values) == 1 or signed[5] == values[1]):
                del self.signoffs[int(values[0])]
                self.rowcount = 1
        elif lowered.startswith(
            "update signoff_decision_outbox set state='effective'"
        ):
            event_id = values[1]
            row = self.outbox.get(event_id)
            if row is not None and row[14] == "PENDING_AUDIT":
                updated = list(row)
                updated[14] = "EFFECTIVE"
                updated[17] = values[0]
                self.outbox[event_id] = tuple(updated)
                self.rowcount = 1
        elif lowered.startswith("update signoff_decision_outbox set state='stale'"):
            if "where goal_id=%s" in lowered:
                goal_id = int(values[1])
                for event_id, row in list(self.outbox.items()):
                    excluded = values[2] if "event_id<>%s" in lowered else None
                    wrong_policy = (
                        "goal_domain<>%s" not in lowered
                        or row[9:12] != values[3:6]
                    )
                    if (
                        int(row[7]) == goal_id
                        and event_id != excluded
                        and wrong_policy
                        and row[14] in {"PENDING_AUDIT", "EFFECTIVE"}
                    ):
                        updated = list(row)
                        updated[14] = "STALE"
                        updated[17] = (
                            updated[17] if updated[17] is not None else values[0]
                        )
                        self.outbox[event_id] = tuple(updated)
                        self.rowcount += 1
            else:
                event_id = values[1]
                row = self.outbox.get(event_id)
                if row is not None and row[14] in {"PENDING_AUDIT", "EFFECTIVE"}:
                    updated = list(row)
                    updated[14] = "STALE"
                    updated[17] = (
                        updated[17] if updated[17] is not None else values[0]
                    )
                    self.outbox[event_id] = tuple(updated)
                    self.rowcount = 1
        elif lowered.startswith("select event_id from signoff_decision_outbox where state"):
            self._rows = [
                (row[0],)
                for row in sorted(self.outbox.values(), key=lambda item: (item[15], item[0]))
                if row[14] == "PENDING_AUDIT" and row[17] is None
            ][: int(values[-1])]
        else:  # pragma: no cover - new SQL must be modeled explicitly
            raise AssertionError(f"unexpected Postgres authority SQL: {normalized}")
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows


def _world(monkeypatch):
    cursor = _AuthorityCursor()
    world = object.__new__(postgres.PostgresWorldModel)

    @contextmanager
    def _tx():
        yield cursor

    world._tx = _tx
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    monkeypatch.setattr(postgres, "_tenant_scope", lambda *_a, **_k: ("tenant_id = %s", ["acme"]))
    monkeypatch.setattr(postgres, "_seal", lambda value: value)
    monkeypatch.setattr(postgres, "_unseal", lambda value: value)
    return world, cursor


def _claim(
    world,
    *,
    gate="review",
    key="production-signoff-key-0001",
):
    binding = policy_binding("finance_cash13w", gate)
    normalized = {
        "decision": "approved",
        "deliverable_updated_at": 123.5,
        "goal_domain": binding.goal_domain,
        "goal_id": 7,
        "governing_gate": binding.governing_gate,
        "note": "reviewed",
        "policy_sha256": binding.policy_sha256,
    }
    return world.decide_signoff_idempotent(
        7,
        "approved",
        decided_by="user:alice",
        note="reviewed",
        expected_updated_at=123.5,
        goal_domain=binding.goal_domain,
        gate=gate,
        policy_sha256=binding.policy_sha256,
        operation=_OPERATION,
        principal_id="user:alice",
        idempotency_key=key,
        request_sha256=request_sha256(normalized),
        note_sha256=request_sha256({"note": "reviewed"}),
    )


def test_postgres_claim_replay_and_effectiveness_match_sqlite(monkeypatch):
    world, cursor = _world(monkeypatch)

    first = _claim(world)
    pending_replay = _claim(world)
    assert first.replayed is False
    assert pending_replay.replayed is True
    assert pending_replay.response == first.response
    assert cursor.signoffs == {}

    mark_start = len(cursor.sql)
    assert world.mark_signoff_audit_delivered(first.event.event_id) is True
    mark_sql = [sql for sql, _params in cursor.sql[mark_start:]]
    goal_lock = next(
        i
        for i, sql in enumerate(mark_sql)
        if sql.startswith("SELECT status, updated_at, domain FROM goals")
        and sql.endswith("FOR UPDATE")
    )
    outbox_lock = next(
        i
        for i, sql in enumerate(mark_sql)
        if sql.startswith("SELECT event_id, tenant_id, operation")
        and sql.endswith("FOR UPDATE")
    )
    assert goal_lock < outbox_lock
    effective_replay = _claim(world)
    assert effective_replay.response == first.response
    assert effective_replay.event.effective is True
    assert cursor.signoffs[7][0] == "approved"
    assert cursor.signoffs[7][5] == first.event.event_id
    assert cursor.signoffs[7][6:9] == (
        "finance_cash13w",
        "review",
        policy_binding("finance_cash13w", "review").policy_sha256,
    )
    assert any("FOR UPDATE" in sql for sql, _params in cursor.sql)
    assert all("tenant_id = %s" in sql for sql, _params in cursor.sql if "FROM goals" in sql)


def test_postgres_stale_generation_never_becomes_effective(monkeypatch):
    world, cursor = _world(monkeypatch)
    claim = _claim(world)
    cursor.goals[7] = ("done", 124.0, "finance_cash13w")

    assert world.mark_signoff_audit_delivered(claim.event.event_id) is False
    assert cursor.signoffs == {}
    assert cursor.outbox[claim.event.event_id][14] == "STALE"


def test_postgres_policy_drift_after_claim_is_stale_not_effective(
    monkeypatch,
):
    import maverick.signoff_authority as authority

    world, cursor = _world(monkeypatch)
    claim = _claim(world)
    monkeypatch.setattr(
        authority,
        "current_signoff_policy_binding",
        lambda _domain: policy_binding("finance_cash13w", "approval"),
    )

    assert world.mark_signoff_audit_delivered(claim.event.event_id) is False
    assert cursor.signoffs == {}
    assert cursor.outbox[claim.event.event_id][14] == "STALE"


def test_postgres_fresh_policy_review_retires_stranded_old_claim(monkeypatch):
    import maverick.signoff_authority as authority

    world, cursor = _world(monkeypatch)
    old_claim = _claim(world)
    monkeypatch.setattr(
        authority,
        "current_signoff_policy_binding",
        lambda _domain: policy_binding("finance_cash13w", "approval"),
    )

    fresh = _claim(
        world,
        gate="approval",
        key="production-signoff-key-0002",
    )

    assert fresh.event.state == "PENDING_AUDIT"
    assert cursor.outbox[old_claim.event.event_id][14] == "STALE"
    assert world.mark_signoff_audit_delivered(fresh.event.event_id) is True
    assert cursor.signoffs[7][7] == "approval"


def test_postgres_domain_transition_advances_revision_and_invalidates_signoff(
    monkeypatch,
):
    world, cursor = _world(monkeypatch)
    claim = _claim(world)
    assert world.mark_signoff_audit_delivered(claim.event.event_id) is True

    world.set_goal_domain(7, "bank_aml_alerts")

    assert cursor.goals[7][2] == "bank_aml_alerts"
    assert cursor.goals[7][1] > 123.5
    assert cursor.signoffs == {}
    assert cursor.outbox[claim.event.event_id][14] == "STALE"
