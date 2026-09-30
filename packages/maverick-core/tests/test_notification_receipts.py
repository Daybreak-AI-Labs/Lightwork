import json
from uuid import uuid4

from maverick.notification_receipts import DeliveryReceipts


def test_accepted_delivery_cannot_be_replayed(tmp_path):
    receipts = DeliveryReceipts(tmp_path / 'receipts.json')
    event = str(uuid4())
    ident, acquired = receipts.claim(event, 'ntfy', kind='test')
    assert acquired
    receipts.update(ident, 'delivered', 'Accepted', safe_to_retry=False)
    assert receipts.claim(event, 'ntfy', kind='test') == (ident, False)
    assert receipts.claim(event, 'ntfy', kind='test', retry=True) == (ident, False)


def test_refused_test_has_three_attempt_ceiling_and_unknown_never_retries(tmp_path):
    receipts = DeliveryReceipts(tmp_path / 'receipts.json')
    event = str(uuid4())
    ident, _ = receipts.claim(event, 'slack', kind='test')
    for _ in range(2):
        receipts.update(ident, 'failed', 'Refused', safe_to_retry=True)
        assert receipts.claim(event, 'slack', kind='test', retry=True)[1]
    receipts.update(ident, 'failed', 'Refused', safe_to_retry=True)
    assert not receipts.claim(event, 'slack', kind='test', retry=True)[1]
    receipts.update(ident, 'unknown', 'Unconfirmed')
    assert not receipts.claim(event, 'slack', kind='test', retry=True)[1]


def test_receipt_has_no_event_content_or_destination(tmp_path):
    receipts = DeliveryReceipts(tmp_path / 'receipts.json')
    receipts.claim('private task title and destination secret', 'discord')
    saved = json.loads(receipts.path.read_text())
    assert 'private task title' not in json.dumps(saved)
    assert saved[0]['request_id'] is None


def test_async_queue_is_not_delivery_and_failed_backend_visible(tmp_path, monkeypatch):
    from maverick import notifications
    path = tmp_path / 'receipts.json'
    monkeypatch.setattr('maverick.paths.data_dir', lambda *parts: path)
    monkeypatch.setattr(notifications, '_load_config', lambda: {'backend':'ntfy', 'ntfy_topic':'synthetic'})
    jobs = []
    class Executor:
        def submit(self, fn, *args):
            jobs.append((fn, args))
    monkeypatch.setattr(notifications, '_get_executor', lambda: Executor())
    monkeypatch.setattr(notifications, '_send_ntfy', lambda *args: False)
    assert notifications.notify('PRIVATE', batch=False, delivery_key='event-once') == 1
    assert DeliveryReceipts(path).recent()[0]['state'] == 'queued'
    fn, args = jobs.pop()
    fn(*args)
    assert DeliveryReceipts(path).recent()[0]['state'] == 'failed'
    assert notifications.notify('PRIVATE', batch=False, delivery_key='event-once') == 0
    assert 'PRIVATE' not in path.read_text()
