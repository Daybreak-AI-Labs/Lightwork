from __future__ import annotations

import json
from copy import deepcopy

from maverick_dashboard import knowledge_sources_api as api


def test_stop_use_review_apply_and_original_are_one_retained_decision(monkeypatch):
    context = "a" * 64
    source = {
        "id": "KNS-" + "b" * 32,
        "revision": 7,
        "name": "Policies",
        "plan": {
            "name": "Policies",
            "binding": {
                "connection": "Shared drive",
                "origin": "https://drive.example.test",
                "selection": {"folder": "policies"},
            },
            "asset_id": "policy-library",
            "decision_id": "decision-policy-library",
            "purpose": "product_research",
            "interval_seconds": 3600,
            "max_dollars": 0,
        },
        "state": "completed",
        "paused": False,
        "item_count": 12,
        "registry_state": "approved",
        "reference": {"asset_id": "policy-library", "version": "v3"},
        "active_job": None,
    }

    class SourceService:
        def __init__(self):
            self.row = deepcopy(source)
            self.retire_calls = 0
            self.original = None

        def owned(self, identifier):
            assert identifier == source["id"]
            return deepcopy(self.row)

        @staticmethod
        def public(row):
            return deepcopy(row)

        def retire(self, identifier, *, expected_revision, operation):
            assert identifier == source["id"]
            if self.original is not None:
                assert operation == self.original["operation"]
                return deepcopy(self.original["result"])
            assert expected_revision == self.row["revision"]
            self.retire_calls += 1
            self.row.update(retired=True, state="retired", paused=True, projection_allowed=False)
            self.original = {
                "operation": operation,
                "result": {
                    "state": "confirmed",
                    "source": {**self.public(self.row), "retired": True},
                    "message": "This source is stopped and its saved search access is blocked.",
                },
            }
            return deepcopy(self.original["result"])

        def original_retire(self, operation):
            if self.original is None or self.original["operation"] != operation:
                return {"state": "not_established", "message": "No original stop-use decision was found."}
            return deepcopy(self.original["result"])

    service = SourceService()
    request = object()
    monkeypatch.setattr(api, "authority", lambda *_args, **_kwargs: ("workspace-a", "user:alice", context))
    monkeypatch.setattr(api, "service", lambda *_args, **_kwargs: (service, context))
    monkeypatch.setattr(api.crypto_at_rest, "seal_to_str", lambda value: "sealed-test:" + value)
    monkeypatch.setattr(api.crypto_at_rest, "is_sealed_str", lambda value: value.startswith("sealed-test:"))
    monkeypatch.setattr(api.crypto_at_rest, "unseal_from_str", lambda value: value.removeprefix("sealed-test:"))

    review = api.retire_review(request, source["id"], api.RetireIn(context=context))
    review_value = json.loads(review.body)
    assert review_value["changes"]["Immediate effect"].startswith("Block this source")
    assert "does not erase" in review_value["changes"]["What stays"]

    body = api.ApplyIn(token=review_value["token"], confirmed=True)
    applied = json.loads(api.apply(request, body).body)
    recovered = json.loads(api.read_original(request, body).body)
    retried = json.loads(api.apply(request, body).body)

    assert applied["kind"] == "retire" and applied["state"] == "confirmed"
    assert applied["source"]["retired"] is True
    assert recovered == applied
    assert retried == applied
    assert service.retire_calls == 1
