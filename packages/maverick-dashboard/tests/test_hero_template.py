"""Hero text remains untrusted unless its template explicitly owns the markup."""
from __future__ import annotations

import pytest
from jinja2 import ChoiceLoader, DictLoader
from markupsafe import Markup, escape
from maverick_dashboard.app import templates
from maverick_dashboard.identity_format import role_label

_CALLS = {
    "title": "{{ hero('Workspace', value) }}",
    "lede": "{{ hero('Workspace', 'Title', lede=value) }}",
    "copy": "{{ state('Current state', 'Ready', copy=value) }}",
}


def _render_value(field, value):
    return templates.env.from_string(
        '{% from "_hero.html" import hero, state %}' + _CALLS[field],
    ).render(value=value)


@pytest.mark.parametrize("field", _CALLS)
@pytest.mark.parametrize("value", [
    '<xprobe title="quotes">Café & 東京</xprobe>',
    '</h1><svg data-probe="alternate">Résumé</svg>',
    '&lt;xprobe&gt;Already encoded & "quoted"&lt;/xprobe&gt;',
])
def test_hero_plain_text_cannot_create_markup(field, value):
    body = _render_value(field, value)
    assert str(escape(value)) in body
    assert "<xprobe" not in body
    assert "<svg" not in body


@pytest.mark.parametrize("field", _CALLS)
def test_hero_preserves_explicit_trusted_markup(field):
    value = Markup('<strong>Café &amp; 東京</strong> <a href="/privacy">Review</a>')
    assert str(value) in _render_value(field, value)


def _render_content(template_name, **context):
    # Render the real page content without unrelated navigation/context providers.
    env = templates.env.overlay(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        templates.env.loader,
    ]))
    return env.get_template(template_name).render(**context)


@pytest.mark.parametrize("default_role", ["viewer", "<xprobe>reviewer</xprobe>"])
def test_users_hero_keeps_emphasis_and_escapes_dynamic_role(default_role):
    body = _render_content(
        "users.html", default_role=default_role, users=[], bootstrap_admins=[],
        people_labels={}, role_labels={}, role_permissions={}, global_admin=False,
    )
    assert "Users &amp; roles" in body
    assert "Users &amp;amp; roles" not in body
    assert f"<strong>{escape(role_label(default_role))}</strong>" in body
    assert "<xprobe>" not in body.lower()


def test_roles_hero_keeps_template_owned_emphasis():
    body = _render_content("roles.html", roles=[])
    assert "<strong>model</strong>" in body
    assert "<strong>extra instructions</strong>" in body


def test_tenants_hero_keeps_template_owned_link():
    body = _render_content("tenants.html", tenants=[])
    assert '<a href="/tenants/overview">Workspace overview</a>' in body
