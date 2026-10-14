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
