"""Native local Qdrant text compatibility; synthetic FastEmbed, no inference."""
from __future__ import annotations

import pytest


@pytest.fixture
def native_text(monkeypatch, tmp_path):
    qdrant = pytest.importorskip("qdrant_client")
    fastembed = pytest.importorskip("fastembed")
    numpy = pytest.importorskip("numpy")
    if callable(getattr(qdrant.QdrantClient, "add", None)):
        pytest.skip("modern Qdrant text fallback; older clients have separate coverage")

    from maverick.vector_store.qdrant_store import QdrantStore

    monkeypatch.delenv("MAVERICK_QDRANT_URL", raising=False)
    monkeypatch.delenv("MAVERICK_QDRANT_API_KEY", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")
    calls = []

    class SyntheticEncoder:
        models = [
            {"model": "synthetic/current", "dim": 2},
            {"model": "synthetic/legacy", "dim": 2},
            {"model": "synthetic/other", "dim": 2},
            {"model": "synthetic/wide", "dim": 3},
        ]
        fail_initialization = False

        def __init__(self, model_name="synthetic/current", **kwargs):
            calls.append(("init", model_name))
            if self.fail_initialization:
                raise RuntimeError("synthetic encoder initialization failure")
            dimensions = {row["model"]: row["dim"] for row in self.models}
            if model_name not in dimensions:
                raise ValueError("unsupported synthetic model")
            self.model_name = model_name
            self.embedding_size = dimensions[model_name]

        @classmethod
        def list_supported_models(cls):
            return [dict(row) for row in cls.models]

        @classmethod
        def get_embedding_size(cls, model_name):
            return next(row["dim"] for row in cls.models if row["model"] == model_name)

        def passage_embed(self, texts, **kwargs):
            texts = list(texts)
            calls.append(("passage", self.model_name, texts))
            for text in texts:
                vector = [0.0] * self.embedding_size
                vector[0 if "needle" in text else 1] = 1.0
                yield numpy.asarray(vector, dtype=numpy.float32)

        def query_embed(self, query, **kwargs):
            texts = [query] if isinstance(query, str) else list(query)
            calls.append(("query", self.model_name, texts))
            for _ in texts:
                yield numpy.asarray([1.0] + [0.0] * (self.embedding_size - 1), dtype=numpy.float32)

        def embed(self, *args, **kwargs):
            raise AssertionError("text compatibility must preserve passage/query embedding modes")

    monkeypatch.setattr(fastembed, "TextEmbedding", SyntheticEncoder)
    stores = []

    def make_store(*, embedding_model=None, path=None):
        store = QdrantStore(
            collection="text_compat", path=path or tmp_path / f"qdrant-{len(stores)}",
            embedding_model=embedding_model,
        )
        stores.append(store)
        return store

    yield make_store, calls, SyntheticEncoder
    for store in stores:
        store._client.close()


def _seed(store, *, vector_name="fast-legacy", dimension=2, distance=None, tenant=None):
    from maverick.vector_store.qdrant_store import _point_id, _stored_id
    from qdrant_client.models import Distance, PointStruct, VectorParams

    params = VectorParams(size=dimension, distance=distance or Distance.COSINE)
    config = params if vector_name is None else {vector_name: params}
    store._client.create_collection(store._collection, vectors_config=config)
    vector = [1.0] + [0.0] * (dimension - 1)
    store._client.upsert(
        store._collection,
        points=[PointStruct(
            id=_point_id("original", tenant),
            vector=vector if vector_name is None else {vector_name: vector},
            payload={"document": "needle legacy", "topic": "legacy",
                     "__nsid__": _stored_id("original", tenant)},
        )], wait=True,
    )


def _snapshot(store):
    points, _ = store._client.scroll(store._collection, limit=100, with_payload=True, with_vectors=True)
    return sorted((str(point.id), point.model_dump()) for point in points)


def test_existing_named_legacy_collection_infers_model_and_preserves_payload(native_text):
    make_store, calls, _ = native_text
    store = make_store()
    _seed(store)
    assert calls == []

    hits = store.query("needle", top_k=1)
    assert [hit["id"] for hit in hits] == ["original"]
    assert hits[0]["document"] == "needle legacy"
    assert hits[0]["metadata"]["topic"] == "legacy"
    assert hits[0]["score"] == pytest.approx(1.0)
    assert hits[0]["distance"] == pytest.approx(0.0)
    assert ("init", "synthetic/legacy") in calls
    assert ("query", "synthetic/legacy", ["needle"]) in calls

    store.add(["unrelated passage"], ids=["new"], metadatas=[{"topic": "new"}])
    assert ("passage", "synthetic/legacy", ["unrelated passage"]) in calls
    assert store.count() == 2
    assert {hit["id"] for hit in store.query("needle", top_k=2)} == {"original", "new"}
    assert set(store._client.get_collection(store._collection).config.params.vectors) == {"fast-legacy"}
    store.delete(["original"])
    assert [hit["id"] for hit in store.query("needle", top_k=2)] == ["new"]


@pytest.mark.parametrize("requested,expected", [(None, "synthetic/current"), ("synthetic/other", "synthetic/other")])
def test_new_collection_delegates_default_or_honors_explicit_model(native_text, requested, expected):
    make_store, calls, _ = native_text
    store = make_store(embedding_model=requested)
    assert calls == []
    store.add(["needle", "unrelated"], ids=["first", "second"])
    assert ("init", expected) in calls
    assert ("passage", expected, ["needle", "unrelated"]) in calls
    assert [hit["id"] for hit in store.query("needle", top_k=1)] == ["first"]
    assert ("query", expected, ["needle"]) in calls
    assert set(store._client.get_collection(store._collection).config.params.vectors) == {
        "fast-" + expected.rsplit("/", 1)[-1].lower(),
    }


def test_recorded_model_survives_reopen_sdk_default_change_and_reset(native_text, tmp_path, monkeypatch):
    make_store, calls, encoder = native_text
    path = tmp_path / "persisted"
    original = make_store(path=path)
    original.add(["needle before upgrade"], ids=["original"])
    recorded = original._client.get_collection(original._collection).config.metadata
    assert recorded["lightwork_text_embedding"] == {
        "model": "synthetic/current", "field": "fast-current", "dimension": 2, "distance": "Cosine",
    }
    original._client.close()
    original_init = encoder.__init__

    def changed_sdk_default(self, model_name="synthetic/other", **kwargs):
        original_init(self, model_name=model_name, **kwargs)

    monkeypatch.setattr(encoder, "__init__", changed_sdk_default)
    calls.clear()
    reopened = make_store(path=path)
    assert calls == []
    assert [hit["document"] for hit in reopened.query("needle")] == ["needle before upgrade"]
    assert ("init", "synthetic/current") in calls
    assert ("init", "synthetic/other") not in calls
    calls.clear()
    reopened.reset()
    assert calls == []
    assert reopened._client.get_collection(reopened._collection).config.metadata == recorded
    reopened.add(["needle after upgrade"], ids=["fresh"])
    assert ("init", "synthetic/other") not in calls
    assert [hit["id"] for hit in reopened.query("needle")] == ["fresh"]


def test_recorded_model_rejects_same_basename_same_dimension_different_model(native_text, tmp_path):
    make_store, calls, encoder = native_text
    path = tmp_path / "recorded-model"
    original = make_store(path=path)
    original.add(["needle prior"], ids=["original"])
    before = _snapshot(original)
    original._client.close()
    encoder.models = [*encoder.models, {"model": "another/current", "dim": 2}]
    calls.clear()
    changed = make_store(path=path, embedding_model="another/current")
    with pytest.raises(ValueError):
        changed.add(["needle replacement"], ids=["original"])
    with pytest.raises(ValueError):
        changed.query("needle")
    assert calls == []
    assert _snapshot(changed) == before


@pytest.mark.parametrize("mismatch", ["model", "dimension", "distance", "unnamed"])
def test_incompatible_existing_collection_is_never_overwritten(native_text, mismatch):
    from qdrant_client.models import Distance

    make_store, _, _ = native_text
    store = make_store(embedding_model="synthetic/other" if mismatch == "model" else "synthetic/legacy")
    _seed(store, vector_name=None if mismatch == "unnamed" else "fast-legacy",
          dimension=3 if mismatch == "dimension" else 2,
          distance=Distance.DOT if mismatch == "distance" else Distance.COSINE)
    original = _snapshot(store)
    original_config = store._client.get_collection(store._collection).config.params.vectors
    with pytest.raises(ValueError):
        store.add(["replacement needle"], ids=["original"])
    assert _snapshot(store) == original
    assert store._client.get_collection(store._collection).config.params.vectors == original_config
    with pytest.raises(ValueError):
        store.query("needle")
    assert _snapshot(store) == original


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unresolvable_legacy_model_refuses_before_encoder_or_write(native_text, ambiguous):
    make_store, calls, encoder = native_text
    store = make_store()
    if ambiguous:
        encoder.models = [*encoder.models, {"model": "another/legacy", "dim": 2}]
    _seed(store, vector_name="fast-legacy" if ambiguous else "fast-unknown")
    original = _snapshot(store)
    with pytest.raises(ValueError):
        store.add(["needle replacement"], ids=["original"])
    with pytest.raises(ValueError):
        store.query("needle")
    assert calls == []
    assert _snapshot(store) == original


@pytest.mark.parametrize("named", [False, True])
def test_reset_preserves_named_or_unnamed_schema_without_encoder_initialization(native_text, named):
    from maverick.paths import tenant_scope

    make_store, calls, _ = native_text
    store = make_store()
    _seed(store, vector_name="fast-legacy" if named else None)
    original_config = store._client.get_collection(store._collection).config.params.vectors
    store.reset()
    assert calls == []
    assert store.count() == 0
    assert store._client.get_collection(store._collection).config.params.vectors == original_config
    if named:
        store.add(["needle after reset"], ids=["fresh"])
        assert [hit["id"] for hit in store.query("needle")] == ["fresh"]
        assert ("init", "synthetic/legacy") in calls
    else:
        with tenant_scope(tenant="reset-scope"):
            store.upsert_one("after reset", id="fresh", metadata={}, embedding=[1.0, 0.0])
            assert [hit["id"] for hit in store.query(embedding=[1.0, 0.0])] == ["fresh"]
        assert calls == []


def test_legacy_rows_without_scope_field_are_filtered_by_exact_tenant_prefix(native_text):
    from maverick.paths import tenant_scope
    from maverick.vector_store.qdrant_store import _point_id, _stored_id
    from qdrant_client.models import PointStruct

    make_store, _, _ = native_text
    store = make_store()
    _seed(store, tenant="acme-1")
    store._client.upsert(store._collection, points=[PointStruct(
        id=_point_id("original", "acme_1"), vector={"fast-legacy": [1.0, 0.0]},
        payload={"document": "other tenant", "__nsid__": _stored_id("original", "acme_1")},
    )], wait=True)
    with tenant_scope(tenant="acme-1"):
        assert [hit["document"] for hit in store.query("needle", top_k=2)] == ["needle legacy"]
        store.delete(["original"])
        assert store.query("needle") == []
    with tenant_scope(tenant="acme_1"):
        assert [hit["document"] for hit in store.query("needle")] == ["other tenant"]


def test_new_rows_keep_tenant_ids_distinct_and_override_reserved_metadata(native_text):
    from maverick.paths import tenant_scope

    make_store, _, _ = native_text
    store = make_store()
    for tenant, text in [("acme-1", "needle first"), ("acme_1", "needle second")]:
        with tenant_scope(tenant=tenant):
            store.add([text], ids=["same"], metadatas=[{
                "__nsid__": "tenant:attacker:forged", "__recall_tenant__": "attacker", "topic": tenant,
            }])
    with tenant_scope(tenant="acme-1"):
        hits = store.query("needle", top_k=2)
        assert [(hit["id"], hit["document"]) for hit in hits] == [("same", "needle first")]
        assert hits[0]["metadata"]["topic"] == "acme-1"
        store.delete(["same"])
        assert store.query("needle") == []
    with tenant_scope(tenant="acme_1"):
        assert [(hit["id"], hit["document"]) for hit in store.query("needle")] == [("same", "needle second")]


def test_encoder_initialization_failure_never_creates_or_overwrites_collection(native_text):
    make_store, _, encoder = native_text
    store = make_store(embedding_model="synthetic/legacy")
    _seed(store)
    original = _snapshot(store)
    encoder.fail_initialization = True
    with pytest.raises(RuntimeError, match="initialization failure"):
        store.add(["replacement needle"], ids=["original"])
    assert _snapshot(store) == original
    empty = make_store(embedding_model="synthetic/legacy")
    with pytest.raises(RuntimeError, match="initialization failure"):
        empty.add(["needle"], ids=["new"])
    assert not empty._client.collection_exists(empty._collection)


@pytest.mark.parametrize("vectors", [[], [[float("nan"), 0.0]], [[1.0, 0.0, 0.0]]])
def test_invalid_embedding_batch_preserves_existing_points(native_text, monkeypatch, vectors):
    make_store, _, encoder = native_text
    store = make_store(embedding_model="synthetic/legacy")
    _seed(store)
    original = _snapshot(store)
    monkeypatch.setattr(encoder, "passage_embed", lambda self, documents: iter(vectors))
    with pytest.raises(ValueError):
        store.add(["replacement needle"], ids=["original"])
    assert _snapshot(store) == original


def test_uncertain_native_upsert_is_not_retried_or_followed_by_deletion(native_text, monkeypatch):
    make_store, _, _ = native_text
    store = make_store(embedding_model="synthetic/legacy")
    _seed(store)
    original = _snapshot(store)
    attempts = []

    def ambiguous_upsert(*args, **kwargs):
        attempts.append(kwargs)
        raise TimeoutError("native write outcome is unknown")

    monkeypatch.setattr(store._client, "upsert", ambiguous_upsert)
    with pytest.raises(TimeoutError, match="unknown"):
        store.add(["replacement needle"], ids=["original"])
    assert len(attempts) == 1
    assert _snapshot(store) == original
