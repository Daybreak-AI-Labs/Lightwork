"""Flow definition versioning: every save snapshots the prior version, and a
rollback restores an earlier one non-destructively (the rollback is itself a new
version, so nothing is ever lost)."""
from __future__ import annotations

from maverick.flow import Flow, FlowNode, store


def _dd(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p))


def _flow(brief="do a"):
    return Flow(id="f", name="F", start="a",
                nodes={"a": FlowNode(id="a", kind="agent", brief=brief)})


def test_first_save_is_version_1(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    saved = store.save_flow(_flow())
    assert saved.version == 1
    assert store.load_flow("f").version == 1
    assert [v.version for v in store.list_versions("f")] == [1]


def test_each_save_bumps_and_snapshots_the_prior(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    store.save_flow(_flow("v1 brief"))
    store.save_flow(_flow("v2 brief"))
    store.save_flow(_flow("v3 brief"))
    cur = store.load_flow("f")
    assert cur.version == 3 and cur.nodes["a"].brief == "v3 brief"
    assert [v.version for v in store.list_versions("f")] == [1, 2, 3]
    assert store.load_version("f", 1).nodes["a"].brief == "v1 brief"
    assert store.load_version("f", 2).nodes["a"].brief == "v2 brief"


def test_rollback_restores_prior_as_a_new_version(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    store.save_flow(_flow("original"))
    store.save_flow(_flow("regrettable change"))
    restored = store.rollback_flow("f")            # default: the previous version
    assert restored.version == 3 and restored.nodes["a"].brief == "original"
    assert store.load_flow("f").nodes["a"].brief == "original"
    # the rolled-back-from version is still recoverable -- nothing was destroyed
    assert store.load_version("f", 2).nodes["a"].brief == "regrettable change"


def test_rollback_to_a_specific_version(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    store.save_flow(_flow("one"))
    store.save_flow(_flow("two"))
    store.save_flow(_flow("three"))
    restored = store.rollback_flow("f", version=1)
    assert restored.version == 4 and restored.nodes["a"].brief == "one"


def test_rollback_unknown_version_returns_none(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    store.save_flow(_flow())
    assert store.rollback_flow("f", version=99) is None


def test_delete_clears_history_so_a_reused_id_starts_clean(tmp_path, monkeypatch):
    _dd(tmp_path, monkeypatch)
    store.save_flow(_flow("a"))
    store.save_flow(_flow("b"))
    assert store.delete_flow("f") is True
    assert store.load_flow("f") is None
    assert store.list_versions("f") == []
    store.save_flow(_flow("new"))
    assert store.load_flow("f").version == 1
