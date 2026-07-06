"""POST /api/v1/voice/transcribe — dashboard voice commands."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_VOICE_COMMANDS", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _post_audio(client, data: bytes = b"fake-webm-audio", **kw):
    return client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("voice-command.webm", io.BytesIO(data), "audio/webm")},
        **kw,
    )


def test_transcribe_returns_text(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.tools.voice as voice_mod

    seen: dict = {}

    def _fake_transcribe(args, sandbox=None):
        seen.update(args)
        assert sandbox is None
        return "  schedule a demo with acme on friday  "

    monkeypatch.setattr(voice_mod, "_run_transcribe", _fake_transcribe)
    r = _post_audio(_client())
    assert r.status_code == 200
    assert r.json() == {"text": "schedule a demo with acme on friday"}
    # The uploaded bytes were handed to the STT backend via a temp file...
    assert seen["source"].endswith(".webm")
    # ...and the temp file is gone afterwards.
    import os
    assert not os.path.exists(seen["source"])


def test_transcribe_passes_language(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.tools.voice as voice_mod
    seen: dict = {}

    def _fake_transcribe(args, sandbox=None):
        seen.update(args)
        return "hola"

    monkeypatch.setattr(voice_mod, "_run_transcribe", _fake_transcribe)
    r = _post_audio(_client(), params={"language": "es"})
    assert r.status_code == 200
    assert seen.get("language") == "es"


def test_transcribe_503_when_no_backend(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.tools.voice as voice_mod
    monkeypatch.setattr(
        voice_mod, "_run_transcribe",
        lambda args, sandbox=None: "ERROR: no voice backend available.",
    )
    r = _post_audio(_client())
    assert r.status_code == 503
    assert "speech-to-text" in r.json()["detail"]


def test_transcribe_rejects_empty_audio(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _post_audio(_client(), data=b"")
    assert r.status_code == 400
    assert "empty audio" in r.json()["detail"]


def test_transcribe_404_when_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_VOICE_COMMANDS", "0")
    r = _post_audio(_client())
    assert r.status_code == 404


def test_transcribe_rejects_oversized_audio(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_VOICE_MAX_BYTES", "10")
    r = _post_audio(_client(), data=b"x" * 11)
    assert r.status_code == 400
    assert "audio too large" in r.json()["detail"]
