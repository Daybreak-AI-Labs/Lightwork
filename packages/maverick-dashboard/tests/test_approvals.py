"""Approval queue page + approve/deny endpoints."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app)


def _isolate(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)


def _world(tmp_path):
    from maverick.world_model import WorldModel
    return WorldModel(tmp_path / "world.db")


def _structured_card(
    *,
    action: str = "browser.click",
    expires_at: float | None = None,
    target_label: str = "<script>target</script>",
) -> dict:
    from maverick.approval_cards import build_approval_card

    return build_approval_card(
        action=action,
        summary="Approve the reviewed customer change",
        target_label=target_label,
        target={"page": "https://customer.test/settings", "selector": "#save"},
        parameters={"secret": "never-render-this"},  # pragma: allowlist secret
        parameter_summary=["Activate the Save button once"],
        actor="user:alice",
        workspace="tenant-a",
        role="operator",
        authority_versions={"role_revision": "role-v1"},
        input_versions={"page_revision": "page-v1"},
        credential_versions={"browser_session_sha256": "a" * 64},
        budget={"currency": "USD", "maximum": 0.25},
        expires_at=expires_at if expires_at is not None else time.time() + 300,
        known_effects=["Changes the reviewed customer setting."],
        unresolved_assumptions=["The page may trigger a notification."],
        before="The setting is unchanged.",
        after="The reviewed setting is saved.",
        irreversible=True,
    )


def test_pending_approval_shows_on_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    aid = _world(tmp_path).create_approval(
        "rm-rf", risk="high", scope="/tmp/build", detail="wipe build dir",
    )
    client = _client()
    r = client.get("/approvals")
    assert r.status_code == 200
    text = r.text
    assert "approval queue" in text.lower()
    assert "rm-rf" in text
    assert "/tmp/build" in text
    assert f'data-id="{aid}"' in text
    assert 'href="/approvals"' in text  # nav link present
    # A durable vote can be accepted while signed audit delivery is pending,
    # and an N-of-M vote can be delivered while the action still lacks quorum.
    # The browser must not remove either row and imply the action is effective.
    assert "r.status === 202" in text
    assert "X-Lightwork-Approval-State" in text
    assert "decision recorded but not yet in effect" in text
    assert "This older request has no exact-operation review" in text
    assert 'data-action="approve" disabled' in text
    assert 'data-action="deny">Reject</button>' in text
    assert 'data-action="revise">Request revision</button>' in text
    assert "const originalDisabled" in text
    assert "restoreDisabledStates()" in text
    assert "b.disabled = originalDisabled[index]" in text


def test_legacy_approval_tokens_are_redacted_from_page_and_api(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    token = "lw-rest-" + "A" * 43
    _world(tmp_path).create_approval(
        f"click {token}",
        risk="high",
        scope=f'input[value="{token}"]',
        detail=f"legacy detail contains {token}",
    )
    client = _client()

    page = client.get("/approvals").text
    body = client.get("/api/v1/approvals").text

    assert token not in page
    assert token not in body
    assert "REDACTED:lightwork_agent_token" in page
    assert "REDACTED:lightwork_agent_token" in body


def test_legacy_approve_is_blocked_but_deny_remains_available(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    aid = w.create_approval("mass-dm", risk="high")
    client = _client()

    r = client.post(
        f"/api/v1/approvals/{aid}/approve",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 409
    assert "predates exact-operation review" in r.json()["detail"]
    assert w.get_approval(aid).status == "pending"

    deny = client.post(
        f"/api/v1/approvals/{aid}/deny",
        headers={"Origin": "http://testserver"},
    )
    assert deny.status_code == 204
    assert w.get_approval(aid).status == "denied"
    assert client.get("/api/v1/approvals").json()["approvals"] == []


def test_deny_transitions_state(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    aid = w.create_approval("force-push", risk="high", scope="main")
    client = _client()

    r = client.post(
        f"/api/v1/approvals/{aid}/deny",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 204
    assert w.get_approval(aid).status == "denied"


def test_revision_requires_rationale_supersedes_old_and_requires_fresh_card(
    monkeypatch, tmp_path,
):
    from maverick import audit
    from maverick.approval_cards import encode_approval_detail

    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    w = _world(tmp_path)
    old_card = _structured_card(action="browser.click")
    old_id = w.create_approval(
        "browser.click",
        risk="high",
        detail=encode_approval_detail(old_card),
        provenance="browser",
        requested_by="user:alice",
    )
    client = _client()

    missing = client.post(
        f"/api/v1/approvals/{old_id}/revise",
        json={"rationale": "   "},
        headers={"Origin": "http://testserver"},
    )
    assert missing.status_code == 422
    assert w.get_approval(old_id).status == "pending"

    revised = client.post(
        f"/api/v1/approvals/{old_id}/revise",
        json={"rationale": "Use the corrected customer and amount."},
        headers={"Origin": "http://testserver"},
    )
    assert revised.status_code == 200
    body = revised.json()
    assert body["status"] == "superseded"
    assert body["superseded_approval_id"] == old_id
    assert body["superseded_binding_sha256"] == old_card["binding_sha256"]
    assert body["producer"] == "browser"
    assert body["fresh_card_required"] is True
    assert w.get_approval(old_id).status == "denied"

    old_replay = client.post(
        f"/api/v1/approvals/{old_id}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": old_card["binding_sha256"],
        },
    )
    assert old_replay.status_code == 404

    fresh_card = _structured_card(
        action="browser.click", expires_at=old_card["expires_at"] + 60,
    )
    fresh_id = w.create_approval(
        "browser.click",
        risk="high",
        detail=encode_approval_detail(fresh_card),
        provenance="browser",
        requested_by="user:alice",
    )
    assert fresh_id != old_id
    assert fresh_card["binding_sha256"] != old_card["binding_sha256"]
    fresh = client.post(
        f"/api/v1/approvals/{fresh_id}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": fresh_card["binding_sha256"],
        },
    )
    assert fresh.status_code == 204


def test_approve_unknown_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    client = _client()
    r = client.post(
        "/api/v1/approvals/9999/approve",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 404


def test_approvals_api_lists_pending(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    w.create_approval("rm-rf", risk="high", scope="/x", detail="d")
    client = _client()
    body = client.get("/api/v1/approvals").json()
    assert len(body["approvals"]) == 1
    a = body["approvals"][0]
    assert a["action"] == "rm-rf"
    assert a["risk"] == "high"
    assert a["scope"] == "/x"


def test_decision_is_anchored_in_audit_chain(monkeypatch, tmp_path):
    # H28: a human approve/deny is recorded in the signed audit chain so its
    # integrity doesn't rest on the mutable approvals row alone.
    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    from maverick.approval_cards import encode_approval_detail

    card = _structured_card(action="wire-funds")
    aid = w.create_approval(
        "wire-funds",
        risk="high",
        detail=encode_approval_detail(card),
    )

    recorded = []
    import maverick.audit as audit
    monkeypatch.setattr(audit, "record",
                        lambda kind, **p: recorded.append((kind, p)) or True)

    client = _client()
    r = client.post(
        f"/api/v1/approvals/{aid}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": card["binding_sha256"],
        },
    )
    assert r.status_code == 204

    assert len(recorded) == 1
    kind, payload = recorded[0]
    assert kind == audit.EventKind.APPROVAL_DECISION
    assert payload["approval_id"] == aid
    assert payload["status"] == "approved"
    assert payload["decided_by"]  # the supervisor identity is attributed


def test_no_audit_event_when_decision_is_a_noop(monkeypatch, tmp_path):
    # A 404 (unknown/already-decided) must NOT emit a decision event.
    _isolate(monkeypatch, tmp_path)
    recorded = []
    import maverick.audit as audit
    monkeypatch.setattr(audit, "record",
                        lambda kind, **p: recorded.append((kind, p)) or True)
    client = _client()
    r = client.post("/api/v1/approvals/999999/deny",
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 404
    assert recorded == []


def test_structured_card_is_visible_escaped_and_requires_exact_binding(
    monkeypatch, tmp_path,
):
    from maverick.approval_cards import encode_approval_detail

    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    card = _structured_card()
    aid = w.create_approval(
        "browser.click",
        risk="high",
        scope="#save",
        detail=encode_approval_detail(card),
    )
    client = _client()

    page = client.get("/approvals")
    assert page.status_code == 200
    assert "Known effects" in page.text
    assert "Unresolved assumptions" in page.text
    assert "prediction does not grant authority" in page.text
    assert "&lt;script&gt;target&lt;/script&gt;" in page.text
    assert "never-render-this" not in page.text

    missing = client.post(
        f"/api/v1/approvals/{aid}/approve",
        headers={"Origin": "http://testserver"},
    )
    assert missing.status_code == 409
    assert w.get_approval(aid).status == "pending"
    stale = client.post(
        f"/api/v1/approvals/{aid}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": "b" * 64,
        },
    )
    assert stale.status_code == 409
    assert w.get_approval(aid).status == "pending"
    current = client.post(
        f"/api/v1/approvals/{aid}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": card["binding_sha256"],
        },
    )
    assert current.status_code == 204
    assert w.get_approval(aid).status == "approved"


def test_untrusted_assessment_text_cannot_replace_structured_approval_card(
    monkeypatch, tmp_path,
):
    from maverick.approval_cards import encode_approval_detail

    _isolate(monkeypatch, tmp_path)
    card = _structured_card(target_label="Save [assessment:attacker-chosen]")
    _world(tmp_path).create_approval(
        "browser.click",
        risk="high",
        scope="#save",
        detail=encode_approval_detail(card),
        provenance="browser",
    )
    page = _client().get("/approvals")

    assert page.status_code == 200
    assert 'class="approval-card"' in page.text
    assert "data-assessment-id=" not in page.text
    assert "detail.innerHTML = ''" not in page.text
    assert "tr.dataset.assessmentId" in page.text


def test_explicit_assessment_provenance_enables_review_without_removing_card_markup(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    assessment_id = "assessment-safe-01"
    _world(tmp_path).create_approval(
        "review assessment",
        risk="high",
        detail=f"Review the saved answers. [assessment:{assessment_id}]",
        provenance="assessment",
    )
    page = _client().get("/approvals")

    assert page.status_code == 200
    assert f'data-assessment-id="{assessment_id}"' in page.text
    assert "assessment · human review" in page.text


def test_expired_structured_card_never_mutates_approval(monkeypatch, tmp_path):
    from maverick.approval_cards import encode_approval_detail

    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    card = _structured_card(expires_at=time.time() - 1)
    aid = w.create_approval(
        "browser.click", risk="high", detail=encode_approval_detail(card),
    )
    response = _client().post(
        f"/api/v1/approvals/{aid}/approve",
        headers={
            "Origin": "http://testserver",
            "X-Lightwork-Approval-Binding": card["binding_sha256"],
        },
    )
    assert response.status_code == 409
    assert w.get_approval(aid).status == "pending"


def test_view_only_approval_submission_is_audited_without_sensitive_content(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    w = _world(tmp_path)
    aid = w.create_approval(
        "wire-private-acquisition", risk="high", detail="sensitive target",
    )
    from maverick import audit
    from maverick_dashboard import auth

    recorded = []
    monkeypatch.setattr(auth, "has_permission", lambda _request, _permission: False)
    monkeypatch.setattr(auth, "caller_principal", lambda _request: "user:viewer")
    monkeypatch.setattr(
        audit,
        "audit_event",
        lambda kind, **payload: recorded.append((kind, payload)) or True,
    )

    response = _client().post(
        f"/api/v1/approvals/{aid}/approve",
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 403
    assert w.get_approval(aid).status == "pending"
    assert len(recorded) == 1
    kind, payload = recorded[0]
    assert kind == audit.EventKind.CAPABILITY_DENIED
    assert payload["principal"] == "user:viewer"
    assert len(payload["resource_sha256"]) == 64
    assert "wire-private-acquisition" not in str(payload)
    assert "sensitive target" not in str(payload)


def test_view_only_submission_stays_pending_when_denial_audit_is_unavailable(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    world = _world(tmp_path)
    approval_id = world.create_approval("publish", risk="high")
    from maverick import audit
    from maverick_dashboard import auth

    monkeypatch.setattr(auth, "has_permission", lambda _request, _permission: False)
    monkeypatch.setattr(auth, "caller_principal", lambda _request: "user:viewer")
    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: False)

    response = _client().post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 503
    assert "audit receipt is unavailable" in response.json()["detail"]
    assert world.get_approval(approval_id).status == "pending"
