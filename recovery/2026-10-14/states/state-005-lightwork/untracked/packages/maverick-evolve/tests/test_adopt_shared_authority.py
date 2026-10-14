"""Shared DGM-pack activation retry and lineage regressions."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json

import pytest
from maverick_evolve.archive import Archive, ArchiveIntegrityError, Candidate

adoption = importlib.import_module("maverick_evolve.adopt")

_PACK = """name = "finance_sox"
description = "SOX testing"
persona = "Original persona."

[models]
default = "mock"
"""


def _archive(path, persona: str) -> None:
    archive = Archive()
    candidate = archive.add(Candidate(
        config={"persona": persona},
        score=0.95,
    ))
    archive.mark_confirmed(candidate.id)
    archive.save(path)


def test_archive_snapshot_rejects_a_path_alias(tmp_path):
    target = tmp_path / "archive.json"
    alias = tmp_path / "archive-alias.json"
    _archive(target, "Governed persona.")
    try:
        alias.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(ArchiveIntegrityError, match="regular file"):
        Archive.load_snapshot(alias)


def test_dgm_pending_marker_recovers_lost_response_with_same_exact_intent(
    tmp_path, monkeypatch,
):
    archive_path = tmp_path / "archive.json"
    pack_path = tmp_path / "finance_sox.toml"
    out = tmp_path / "domains"
    pack_path.write_text(_PACK, encoding="utf-8")
    _archive(archive_path, "Governed evolved persona.")
    monkeypatch.setattr(
        "maverick.learning_authority.shared_learning_authority_selected",
        lambda: True,
    )
    publications = []

    def publish(intent):
        publications.append(copy.deepcopy(intent))
        if len(publications) == 1:
            raise OSError("database response was lost")

    monkeypatch.setattr(adoption, "_publish_release", publish)

    with pytest.raises(OSError, match="response"):
        adoption.adopt_best(archive_path, pack_path, out_dir=out)

    dest = out / pack_path.name
    marker_path = adoption._release_marker(dest)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert dest.exists()
    assert marker["previous_digest"] is None
    assert marker["digest"] == hashlib.sha256(dest.read_bytes()).hexdigest()
    assert marker["archive_sha256"] == hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()

    assert adoption.adopt_best(archive_path, pack_path, out_dir=out) == dest
    assert publications[1] == publications[0]
    assert json.loads(marker_path.read_text(encoding="utf-8")) == marker


def test_dgm_successor_marker_binds_exact_predecessor_and_tamper_blocks(
    tmp_path, monkeypatch,
):
    archive_path = tmp_path / "archive.json"
    pack_path = tmp_path / "finance_sox.toml"
    out = tmp_path / "domains"
    pack_path.write_text(_PACK, encoding="utf-8")
    _archive(archive_path, "First governed persona.")
    monkeypatch.setattr(
        "maverick.learning_authority.shared_learning_authority_selected",
        lambda: True,
    )
    publications = []
    monkeypatch.setattr(
        adoption,
        "_publish_release",
        lambda intent: publications.append(copy.deepcopy(intent)),
    )
    # The previous generation is proven before a successor is written.
    monkeypatch.setattr(
        "maverick.learning_authority.require_learning_release",
        lambda **_kwargs: True,
    )

    dest = adoption.adopt_best(archive_path, pack_path, out_dir=out)
    first_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    _archive(archive_path, "Second governed persona.")

    assert adoption.adopt_best(archive_path, pack_path, out_dir=out) == dest
    assert publications[-1]["previous_digest"] == first_digest
    assert publications[-1]["digest"] == hashlib.sha256(dest.read_bytes()).hexdigest()

    marker_path = adoption._release_marker(dest)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["digest"] = "00" * 32
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        adoption.adopt_best(archive_path, pack_path, out_dir=out)
