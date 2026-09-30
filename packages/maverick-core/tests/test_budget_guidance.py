"""Budget guidance regressions; intentionally not executed in the audit build."""
from copy import deepcopy

from maverick import budget_guidance as guidance


class Store:
    def __init__(self):
        self.rows = {}
        self.created = 0

    def get(self, identity):
        return deepcopy(self.rows.get(identity))

    def create(self, row, **_):
        assert row['id'] not in self.rows
        self.created += 1
        self.rows[row['id']] = deepcopy(row)
        return deepcopy(row)


def view(*, remaining=10, period='2026-09'):
    return {'observed_at': 1790812800, 'person': {'cap': None}, 'workspace': {'cap': None},
            'providers': [{'provider': 'configured', 'period': period, 'cap': 100, 'remaining': remaining}]}


def test_threshold_receipt_deduplicates_same_person_and_provider_period(monkeypatch):
    store = Store()
    monkeypatch.setattr(guidance, '_warnings_store', lambda: store)
    monkeypatch.setattr(guidance, 'policy', lambda: {'guidance': True, 'warnings': True, 'warning_thresholds': [.8]})
    guidance.observe_warnings('user:a', view())
    later = view(); later['observed_at'] += 86400
    guidance.observe_warnings('user:a', later)
    assert store.created == 1
    guidance.observe_warnings('user:b', later)
    assert store.created == 2
    guidance.observe_warnings('user:a', view(period='2026-10'))
    assert store.created == 3


def test_unknown_headroom_is_not_a_zero_charge_or_threshold(monkeypatch):
    store = Store()
    monkeypatch.setattr(guidance, '_warnings_store', lambda: store)
    monkeypatch.setattr(guidance, 'policy', lambda: {'guidance': True, 'warnings': True, 'warning_thresholds': [.8]})
    assert guidance.observe_warnings('user:a', view(remaining=None)) == []
    assert store.created == 0


def test_disabled_warnings_do_not_write_or_change_accounting(monkeypatch):
    monkeypatch.setattr(guidance, 'policy', lambda: {'guidance': True, 'warnings': False, 'warning_thresholds': [.8]})
    monkeypatch.setattr(guidance, '_warnings_store', lambda: (_ for _ in ()).throw(AssertionError('no store mutation')))
    assert guidance.observe_warnings('user:a', view(remaining=0)) == []
