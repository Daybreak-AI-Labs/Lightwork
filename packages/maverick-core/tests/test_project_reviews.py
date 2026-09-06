"""Local governed project review authority; no provider or task execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from maverick import audit, config
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict
from maverick.project_reviews import ProjectReviewDenied, ProjectReviews
from maverick.world_model import WorldModel


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: True)
    with tenant_scope(tenant="tenant-a"), WorldModel(tmp_path / "world.db") as world:
        project = world.create_project("Customer review", owner="user:owner")
        goal = world.create_goal("Selected result", "", owner="user:owner", project_id=project)
        world.set_goal_status(
            goal, "done", result="Captured result containing evidence and uncertainty."
        )
        grants = {"user:owner": True, "user:reviewer": True, "user:third": True}

        def service(actor):
            return ProjectReviews(
                world,
                principal=actor,
                eligible=lambda principal, *_args: grants.get(principal, False),
            )

        owner = service("user:owner")
        team = owner.set_member(
            project,
            member="user:reviewer",
            allowed=True,
            expected_revision=0,
            request_id="grant-reviewer",
        )
        yield world, project, goal, grants, service, team
    config.reset_config_cache()


def send(setup, **changes):
    _, project, goal, _, service, team = setup
    owner = service("user:owner")
    preview = owner.prepare(project, goal)
    payload = {
        "goal_id": goal,
        "reviewer": "user:reviewer",
        "note": "Please review this captured result.",
        "due_at": owner.packets.authoritative_time() + 3600,
        "source_version": preview["source_version"],
        "expected_team_revision": team["revision"],
        "request_id": "send-packet-one",
    }
    payload.update(changes)
    return owner.send(project, **payload), payload


def respond(service, packet, **changes):
    payload = {
        "action": "approve",
        "note": "Reviewed the stated evidence and limitations.",
        "expected_revision": packet["revision"],
        "source_version": packet["source_version"],
        "request_id": "response-one",
    }
    payload.update(changes)
    return service.respond(packet["id"], **payload)


def test_packet_is_exact_immutable_review_and_never_live_goal_signoff(setup):
    world, project, goal, _, service, _ = setup
    packet, payload = send(setup)
    before = world.get_goal(goal)
    approved = respond(service("user:reviewer"), packet)
    assert approved["status"] == "approved"
    assert approved["snapshot"] == packet["snapshot"]
    assert approved["live_goal_signoff"] is False
    assert world.get_goal(goal) == before
    assert world.signoff_for(goal) is None
    assert service("user:owner").send(project, **payload)["id"] == packet["id"]
    assert respond(service("user:reviewer"), packet)["revision"] == approved["revision"]
    with pytest.raises(RecordConflict):
        respond(service("user:reviewer"), packet, note="Different request content")


@pytest.mark.parametrize("change", ["result", "status", "title", "unfile"])
def test_changed_source_keeps_original_packet_but_refuses_new_decision(setup, change):
    world, _, goal, _, service, _ = setup
    packet, _ = send(setup)
    if change == "result":
        world.set_goal_status(goal, "done", result="A new result")
    elif change == "status":
        world.set_goal_status(goal, "active")
    elif change == "title":
        world.set_goal_title(goal, "New title")
    else:
        world.set_goal_project(goal, None)
    loaded = service("user:reviewer").get(packet["id"])
    assert loaded["stale"] is True
    assert loaded["snapshot"] == packet["snapshot"]
    with pytest.raises(RecordConflict, match="Source task changed"):
        respond(service("user:reviewer"), packet)
    commented = respond(
        service("user:reviewer"), packet, action="comment", note="Please send a current result."
    )
    assert commented["status"] == "pending" and commented["stale"]


def test_revocation_hides_packet_counts_and_content_immediately_but_preserves_owner_history(setup):
    _, project, _, _, service, team = setup
    packet, _ = send(setup)
    owner, reviewer = service("user:owner"), service("user:reviewer")
    assert len(reviewer.inbox()) == 1
    owner.set_member(
        project,
        member="user:reviewer",
        allowed=False,
        expected_revision=team["revision"],
        request_id="revoke-reviewer",
    )
    assert reviewer.inbox() == []
    with pytest.raises(ProjectReviewDenied):
        reviewer.get(packet["id"])
    with pytest.raises(ProjectReviewDenied):
        respond(reviewer, packet)
    assert owner.get(packet["id"])["history"][0]["action"] == "sent"
    assert respond(owner, packet, action="cancel")["status"] == "cancelled"


@pytest.mark.parametrize("change", ["unassigned", "role", "tenant"])
def test_current_access_is_checked_before_packet_counts_or_content(setup, change):
    _, _, _, grants, service, _ = setup
    packet, _ = send(setup)
    reviewer = service("user:third" if change == "unassigned" else "user:reviewer")
    if change == "role":
        grants["user:reviewer"] = False
    if change == "tenant":
        with tenant_scope(tenant="tenant-b"), pytest.raises(ProjectReviewDenied):
            reviewer.inbox()
    else:
        assert reviewer.inbox() == []
        with pytest.raises(ProjectReviewDenied):
            reviewer.get(packet["id"])


def test_owner_cannot_self_approve_or_share_unfiled_foreign_or_changed_result(setup):
    world, project, goal, _, service, _ = setup
    packet, _ = send(setup)
    with pytest.raises(ProjectReviewDenied):
        respond(service("user:owner"), packet)
    with pytest.raises(ProjectReviewDenied):
        send(setup, reviewer="user:owner", request_id="self-packet")
    foreign = world.create_goal(
        "Private foreign result", "", owner="user:third", project_id=project
    )
    world.set_goal_status(foreign, "done", result="Private")
    with pytest.raises(ProjectReviewDenied):
        service("user:owner").prepare(project, foreign)
    with pytest.raises(RecordConflict):
        send(setup, source_version="0" * 64, request_id="changed-packet")
    assert world.get_goal(goal).result == packet["snapshot"]["result"]


def test_revocation_and_response_use_one_local_project_critical_section(setup, monkeypatch):
    _, project, _, _, service, team = setup
    packet, _ = send(setup)
    entered, release = Event(), Event()
    owner, reviewer = service("user:owner"), service("user:reviewer")
    original = owner.teams.update

    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(owner.teams, "update", hold)

    def revoke():
        with tenant_scope(tenant="tenant-a"):
            return owner.set_member(
                project,
                member="user:reviewer",
                allowed=False,
                expected_revision=team["revision"],
                request_id="concurrent-revoke",
            )

    def approve():
        with tenant_scope(tenant="tenant-a"):
            with pytest.raises(ProjectReviewDenied):
                respond(reviewer, packet)

    with ThreadPoolExecutor(max_workers=2) as pool:
        revoke_future = pool.submit(revoke)
        assert entered.wait(5)
        approve_future = pool.submit(approve)
        release.set()
        revoke_future.result(timeout=5)
        approve_future.result(timeout=5)
    assert owner.get(packet["id"])["status"] == "pending"


def test_postgres_fence_rejects_changed_membership_version(setup):
    _, _, _, _, service, team = setup
    owner = service("user:owner")
    authority = owner._team(team["id"])
    fence = owner._fence(authority, "postgres")
    fence.validate(authority, 0)
    changed = {**authority, "revision": authority["revision"] + 1}
    with pytest.raises(RecordConflict):
        fence.validate(changed, 0)
