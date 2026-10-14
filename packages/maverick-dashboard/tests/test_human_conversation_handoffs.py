"""Human conversation handoffs: the outside link and the review shown before a handoff is confirmed."""
from __future__ import annotations

from pathlib import Path


def test_the_conversation_link_path_is_the_chat_goal_page_that_shows_the_handoff_panel():
    from maverick_dashboard.app import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/chat/goal/{goal_id}" in paths
    templates = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    assert '{% include "_conversation_inbox_panel.html" %}' in (templates / "chat_goal.html").read_text(encoding="utf-8")
    assert '{% include "_human_conversations.html" %}' in (templates / "_conversation_inbox_panel.html").read_text(encoding="utf-8")


class _EmptyStore:
    """Stands in for the conversation inbox store: no stored records."""

    def get(self, identifier):
        return None

    def list(self, limit=None):
        return []


def test_the_conversation_inventory_still_opens_when_the_outbox_is_unreadable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from maverick import config, contact_centre
    from maverick.human_conversation_handoffs import HumanConversations
    from maverick_dashboard import human_conversation_handoffs as dashboard_handoffs

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_CONTACT_CENTRE_ZENDESK_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text(
        '[contact_centre]\nprovider = "zendesk"\nzendesk_subdomain = "acme-support"\n', encoding="utf-8")
    config.reset_config_cache()
    handoff = "handoff-test-0123456789abcdef"
    outbox = contact_centre._path()
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_bytes(b"not sealed")

    goal = SimpleNamespace(id=7, owner="ana@example.com", title="Card declined")
    root = SimpleNamespace(id=7, owner="ana@example.com")
    session = {"id": handoff, "state": "waiting", "recipient": "agent@acme.example", "goal_id": 7,
               "owner": "ana@example.com", "purpose": "Need a person", "expires_at": 0,
               "shared": {"title": "Card declined"}, "source": "v1", "tasks": [], "messages": [],
               "duty_label": "Agent"}
    human = {"current": handoff, "sessions": {handoff: session}, "operations": {}}
    source = HumanConversations.__new__(HumanConversations)
    source.world, source.actor, source.store, source.metadata_only = object(), "agent@acme.example", _EmptyStore(), False
    source.authorize = lambda *args: None
    source._load = lambda goal_id: (goal, root, None, {"revision": 1}, {}, human)
    source._authorized = lambda goal, root, human, write=False: session
    monkeypatch.setattr(dashboard_handoffs, "authority",
                        lambda request, context=None, write=False, enabled=False: ("agent@acme.example", "ctx"))
    monkeypatch.setattr(dashboard_handoffs, "service", lambda request, write=False, metadata_only=False: source)

    value = dashboard_handoffs.inventory(object(), goal_id=7)
    assert value["conversation"]["session"]["state"] == "waiting"
    assert value["conversation"]["contact_centre"]["state"] == "unavailable"
    assert value["people"] == []
