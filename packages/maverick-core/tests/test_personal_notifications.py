import time
from datetime import datetime, timezone

from maverick import personal_notifications as alerts


def _timestamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def _source(goal=1, version='blocked', category='blocked'):
    return {'ref':f'goal:{goal}:state', 'version':version, 'category':category,
            'goal_id':goal, 'project_id':None, 'title':'Synthetic task', 'href':f'/chat/goal/{goal}'}


def _plaintext_test_storage(monkeypatch):
    monkeypatch.setattr(alerts, 'seal', lambda value: value)
    monkeypatch.setattr(alerts, 'unseal', lambda value: value)


def test_digest_handles_dst_gap_and_repeated_hour_without_double_delivery():
    prefs = alerts.validate_preferences({'mode':'digest', 'timezone':'America/New_York', 'digest_time':'02:30'})
    assert not alerts.delivery_window(prefs, now=_timestamp('2026-03-08T06:59:00'))[0]
    assert alerts.delivery_window(prefs, now=_timestamp('2026-03-08T07:01:00'))[0]
    prefs['digest_time'] = '01:30'
    assert alerts.delivery_window(prefs, now=_timestamp('2026-11-01T05:30:00'))[0]
    assert not alerts.delivery_window(prefs, now=_timestamp('2026-11-01T06:30:00'), last_digest_day='2026-11-01')[0]


def test_critical_quiet_hour_exception_requires_both_policy_and_personal_choice():
    prefs = alerts.validate_preferences({'timezone':'America/New_York', 'quiet_enabled':True,
                                        'critical_exceptions':True})
    now = _timestamp('2026-03-08T06:59:00')
    assert not alerts.delivery_window(prefs, now=now, critical=True, allow_critical=False)[0]
    assert alerts.delivery_window(prefs, now=now, critical=True, allow_critical=True)[0]


def test_two_recipients_keep_independent_delivery_modes_and_semantic_dedupe(tmp_path, monkeypatch):
    _plaintext_test_storage(monkeypatch)
    alice = alerts.PersonalInbox('user:alice', tmp_path / 'alice.enc')
    bob = alerts.PersonalInbox('user:bob', tmp_path / 'bob.enc')
    morning = _timestamp('2026-09-30T08:00:00')
    digest = alerts.validate_preferences({'mode':'digest', 'digest_time':'09:00'})
    immediate = alerts.validate_preferences({})
    assert not alice.refresh([_source()], digest, now=morning)['items']
    first = bob.refresh([_source()], immediate, now=morning)['items']
    again = bob.refresh([_source()], immediate, now=morning+60)['items']
    assert len(first) == len(again) == 1
    assert first[0]['id'] == again[0]['id']
    assert len(alice.refresh([_source()], digest, now=morning+7200)['items']) == 1
    assert not bob.refresh([], immediate, now=morning+120)['items']


def test_task_mute_does_not_silence_unrelated_work_or_expose_revoked_source(tmp_path, monkeypatch):
    _plaintext_test_storage(monkeypatch)
    now = time.time()
    inbox = alerts.PersonalInbox('user:alice', tmp_path / 'alice.enc')
    prefs = alerts.validate_preferences({'muted_tasks':{'1':now+3600}})
    result = inbox.refresh([_source(1), _source(2)], prefs, now=now)
    assert [item['goal_id'] for item in result['items']] == [2]
    assert not inbox.refresh([], prefs, now=now+1)['items']


def test_changed_result_requires_an_observed_previous_result(tmp_path, monkeypatch):
    _plaintext_test_storage(monkeypatch)
    inbox = alerts.PersonalInbox('user:alice', tmp_path / 'alice.enc')
    prefs = alerts.validate_preferences({'events':['changed-result']})
    source = _source(version='digest-one', category='changed-result')
    assert not inbox.refresh([source], prefs)['items']
    source['version'] = 'digest-two'
    assert len(inbox.refresh([source], prefs)['items']) == 1
