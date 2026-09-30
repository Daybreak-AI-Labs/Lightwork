"""Principal isolation and inheritance contracts; authored, intentionally unrun."""
import pytest

from maverick import experience_preferences as prefs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('MAVERICK_HOME', str(tmp_path))
    monkeypatch.setattr(prefs.config, 'config_path', lambda: tmp_path / 'config.toml')
    monkeypatch.setattr(prefs.config, 'dashboard_overrides_path', lambda: tmp_path / 'overlay.toml')
    monkeypatch.setattr(prefs.config, 'load_config', lambda: {})
    monkeypatch.setattr(prefs.config, 'load_global_config', lambda: {})
    monkeypatch.setattr(prefs.config, 'config_source_errors', lambda: [])
    monkeypatch.setattr(prefs.config, 'get_features', lambda: {'personal_preferences': True})
    monkeypatch.setattr(prefs, 'current_tenant_id_strict', lambda: None)
    monkeypatch.setattr(prefs, 'data_dir', lambda *parts: tmp_path.joinpath(*parts))
    # The storage codec is tested separately; this fixture checks scope and CAS.
    from maverick import styles
    monkeypatch.setattr(styles, 'active_style_name', lambda: "")
    monkeypatch.setattr(prefs, 'seal', lambda value: value)
    monkeypatch.setattr(prefs, 'unseal', lambda value: value)


def write(principal, changes, scope='personal', thread=None):
    return prefs.save(principal, scope=scope, changes=changes, thread=thread,
                      expected_revision=prefs.state(principal, thread=thread)['revision'])


def test_two_people_and_conversation_override_do_not_leak(isolated):
    write('user:alice', {'preferred_name': 'Alice', 'tone': 'friendly'})
    write('user:alice', {'tone': 'formal'}, thread=12)
    assert prefs.state('user:bob')['values']['preferred_name'] == ''
    assert prefs.state('user:alice')['values']['tone'] == 'friendly'
    assert prefs.state('user:alice', thread=12)['values']['tone'] == 'formal'
    assert prefs.state('user:bob', thread=12)['values']['tone'] == ''
    assert prefs.prompt('service:scheduled') == ''


def test_inheritance_and_stale_save(isolated):
    write('user:admin', {'tone': 'formal'}, scope='organization')
    old = prefs.state('user:alice')['revision']
    write('user:alice', {'tone': 'friendly'})
    with pytest.raises(prefs.PreferenceConflict):
        prefs.save('user:alice', scope='personal', changes={'tone': 'concise'}, expected_revision=old)
    assert write('user:alice', {'tone': None})['values']['tone'] == 'formal'


def test_policy_lock_cannot_be_removed_by_inherit(isolated, monkeypatch):
    monkeypatch.setattr(prefs.config, 'load_global_config', lambda: {'personalization': {
        'locked_fields': ['tone', 'preferred_name'], 'defaults': {'tone': 'formal', 'preferred_name': 'Wrong person'}}})
    assert prefs.state('user:bob')['values']['preferred_name'] == ''
    with pytest.raises(ValueError, match='organization policy'):
        write('user:bob', {'tone': None})
    assert prefs.state('user:bob')['values']['tone'] == 'formal'


def test_personal_name_and_authority_cannot_be_shared_preferences(isolated):
    with pytest.raises(ValueError):
        write('user:alice', {'preferred_name': 'Alice'}, scope='organization')
    with pytest.raises(ValueError):
        write('user:alice', {'tools': ['browser']})
    write('user:alice', {'preferred_name': 'Alice'})
    write('user:bob', {'preferred_name': 'Bob'})
    assert prefs.erase_personal('user:alice') == 1
    assert prefs.state('user:bob')['values']['preferred_name'] == 'Bob'


def test_workspace_overlay_cannot_remove_organization_locks(isolated, monkeypatch):
    monkeypatch.setattr(prefs.config, 'load_global_config', lambda: {'personalization': {
        'locked_fields': ['tone'], 'defaults': {'tone': 'formal'}}})
    monkeypatch.setattr(prefs.config, 'load_config', lambda: {'personalization': {'locked_fields': []}})
    assert prefs.state('user:alice')['values']['tone'] == 'formal'
    with pytest.raises(ValueError, match='organization policy'):
        write('user:alice', {'tone': 'friendly'}, scope='workspace')


def test_workspace_cannot_enable_globally_disabled_preferences(isolated, monkeypatch):
    monkeypatch.setattr(prefs.config, 'load_global_config', lambda: {'features': {'personal_preferences': False}})
    assert not prefs.enabled()
    with pytest.raises(ValueError, match='turned off'):
        write('user:alice', {'tone': 'friendly'})
