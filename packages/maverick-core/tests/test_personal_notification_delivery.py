import sys
from types import SimpleNamespace

import pytest
from maverick import personal_notification_delivery as delivery
from maverick.personal_notifications import validate_preferences


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'seal', lambda value: value)
    monkeypatch.setattr(delivery, 'unseal', lambda value: value)
    return delivery.DeliveryStore('user:alice', {'id': 'a' * 32, 'verified_at': 1}, tmp_path / 'synthetic-ledger')


def test_interrupted_send_claim_never_replays_and_digest_releases_once_per_day(ledger):
    first = {'id': 'a' * 64}
    second = {'id': 'b' * 64}
    assert ledger.claim([first], day='2026-09-30') is not None
    assert ledger.claim([first], day='2026-09-30') is None
    assert ledger.claim([first, second], day='2026-09-30') is None
    next_day = ledger.claim([first, second], day='2026-10-01')
    assert next_day[1] == [second]
    assert ledger.pending([first, second]) == []


def test_private_recipient_rejects_public_channel_identifiers():
    with pytest.raises(ValueError, match='private recipient'):
        delivery._transport({'channel': 'slack', 'sender_id': 'C12345678'})
    with pytest.raises(ValueError, match='private recipient'):
        delivery._transport({'channel': 'telegram', 'sender_id': '-10012345678'})


def test_telegram_bot_change_refuses_before_any_request(monkeypatch):
    monkeypatch.setattr(delivery, '_transport', lambda _: ('telegram', '123:synthetic'))
    monkeypatch.setattr(delivery, '_post', lambda *args: pytest.fail('bot mismatch must not send'))
    with pytest.raises(delivery.Refused):
        delivery.send_private({'channel': 'telegram', 'sender_id': '456', 'account_sha256': delivery._digest('789')}, 'synthetic')


def test_slack_will_not_post_to_a_mismatched_direct_conversation(monkeypatch):
    monkeypatch.setattr(delivery, '_transport', lambda _: ('slack', 'synthetic-token'))
    calls = []
    def post(url, token, payload):
        calls.append(url)
        if url.endswith('/auth.test'):
            return {'ok': True, 'team_id': 'T12345678', 'user_id': 'U87654321'}
        return {'ok': True, 'channel': {'id': 'D12345678', 'is_im': True, 'user': 'U99999999'}}
    monkeypatch.setattr(delivery, '_post', post)
    with pytest.raises(delivery.Refused):
        delivery.send_private({'channel': 'slack', 'sender_id': 'U11111111',
                               'account_sha256': delivery._digest('T12345678:U87654321')}, 'synthetic')
    assert len(calls) == 2
    assert not any(url.endswith('/chat.postMessage') for url in calls)


def test_link_revocation_between_projection_and_effect_blocks_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'seal', lambda value: value)
    monkeypatch.setattr(delivery, 'unseal', lambda value: value)
    monkeypatch.setattr(delivery, 'data_dir', lambda *parts: tmp_path.joinpath(*parts))
    config = {'personal_alerts': True, 'personal_background': True, 'personal_external': True,
              'personal_dashboard_url': 'https://dashboard.example.test'}
    monkeypatch.setattr(delivery, '_settings', lambda: config)
    link = {'id': 'a' * 32, 'verified_at': 1, 'channel': 'telegram', 'sender_id': '123'}
    calls = []
    def links(_):
        calls.append(True)
        return [link] if len(calls) == 1 else []
    monkeypatch.setitem(sys.modules, 'maverick.personal_identity', SimpleNamespace(
        linked_channels=links, principal_current=lambda _: True))
    monkeypatch.setattr(delivery, 'send_private', lambda *args: pytest.fail('revoked link must not send'))
    choice = validate_preferences({'external_links': [link['id']]})
    delivery.dispatch('user:alice', {'items': [{'id': 'b' * 64, 'category': 'blocked', 'href': '/chat/goal/1'}]}, choice)
    receipt = delivery.DeliveryStore('user:alice', link).recent()[0]
    assert receipt['state'] == 'failed'


def test_private_links_never_accept_credentials_or_insecure_origins():
    for address in ('http://dashboard.example.test', 'https://user:synthetic@example.test',
                    'https://dashboard.example.test/other', 'https://dashboard.example.test?token=synthetic'):
        with pytest.raises(ValueError):
            delivery._base_url({'personal_dashboard_url': address})


def test_private_delivery_explains_missing_identity_link_policy(monkeypatch):
    from maverick import personal_identity

    monkeypatch.setattr(delivery, '_settings', lambda: {'personal_dashboard_url': 'https://dashboard.example.test'})
    monkeypatch.setattr(personal_identity, 'enabled', lambda: False)
    monkeypatch.setattr(personal_identity, 'linked_channels', lambda _: [])
    result = delivery.status('user:alice')
    assert result['enabled'] is False
    assert 'enable personal data controls' in result['note']
    assert result['channels'] == []


def test_personal_history_erasure_keeps_other_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'seal', lambda value: value)
    monkeypatch.setattr(delivery, 'unseal', lambda value: value)
    monkeypatch.setattr(delivery, 'data_dir', lambda *parts: tmp_path.joinpath(*parts))
    link = {'id': 'a' * 32, 'verified_at': 1}
    alice = delivery.DeliveryStore('user:alice', link)
    bob = delivery.DeliveryStore('user:bob', link)
    alice.claim([{'id': 'b' * 64}])
    bob.claim([{'id': 'c' * 64}])
    assert delivery.personal_history_inventory('user:alice')['consumed_items'] == 1
    delivery.remove_personal_history('user:alice')
    assert delivery.personal_history_inventory('user:alice')['consumed_items'] == 0
    assert bob.pending([{'id': 'c' * 64}]) == []


def test_misbound_delivery_file_cannot_publish_another_persons_history(ledger):
    ledger.claim([{'id': 'b' * 64}])
    other = delivery.DeliveryStore('user:bob', {'id': 'a' * 32, 'verified_at': 1}, path=ledger.path)
    with pytest.raises(ValueError, match='unavailable'):
        other.recent()
