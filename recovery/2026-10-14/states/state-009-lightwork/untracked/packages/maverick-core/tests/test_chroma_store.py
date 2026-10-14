"""Focused regressions for the Chroma vector-store adapter."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


class _StatefulCollection:
    """Small Chroma stand-in with request-atomic upsert semantics."""

    def __init__(self):
        self.rows = {}
        self.upsert_calls = 0
        self.add_calls = 0
        self.delete_calls = 0
        self.fail_next_upsert = False

    def upsert(self, *, documents, ids, metadatas=None, embeddings=None):
        self.upsert_calls += 1
        if self.fail_next_upsert:
            self.fail_next_upsert = False
            raise RuntimeError("write rejected")
        replacement = dict(self.rows)
        for index, doc_id in enumerate(ids):
            replacement[doc_id] = {
                "document": documents[index],
                "metadata": metadatas[index] if metadatas else None,
                "embedding": embeddings[index] if embeddings else None,
            }
        self.rows = replacement

    def add(self, **_kwargs):
        self.add_calls += 1
        raise AssertionError("ChromaStore must use stable-id upsert")

    def delete(self, *, ids):
        self.delete_calls += 1
        for doc_id in ids:
            self.rows.pop(doc_id, None)


def test_chroma_stable_id_upsert_failure_preserves_last_good_row(
    monkeypatch, tmp_path,
):
    collection = _StatefulCollection()

    class _FakeClient:
        def get_or_create_collection(self, **_kwargs):
            return collection

    fake_client = _FakeClient()
    fake_chromadb = SimpleNamespace(
        PersistentClient=lambda **_kwargs: fake_client,
    )
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)

    from maverick.vector_store.chroma_store import ChromaStore

    store = ChromaStore(collection="goals", path=tmp_path / "chroma")
    store.add(
        ["last good"],
        ids=["goal:1"],
        metadatas=[{"version": 1}],
        embeddings=[[0.1, 0.2]],
    )
    store.add(
        ["replacement"],
        ids=["goal:1"],
        metadatas=[{"version": 2}],
        embeddings=[[0.3, 0.4]],
    )
    assert collection.rows == {
        "goal:1": {
            "document": "replacement",
            "metadata": {"version": 2},
            "embedding": [0.3, 0.4],
        }
    }

    collection.fail_next_upsert = True
    with pytest.raises(RuntimeError, match="write rejected"):
        store.add(
            ["bad replacement"],
            ids=["goal:1"],
            metadatas=[{"version": 3}],
            embeddings=[[0.5, 0.6]],
        )

    assert collection.rows["goal:1"] == {
        "document": "replacement",
        "metadata": {"version": 2},
        "embedding": [0.3, 0.4],
    }
    assert collection.upsert_calls == 3
    assert collection.add_calls == 0
    assert collection.delete_calls == 0
