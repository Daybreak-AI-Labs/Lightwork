"""Voice tools — speech-to-text (Whisper) and text-to-speech.

Speech-to-text backends (tried in order):
  1. OpenAI Whisper API (OPENAI_API_KEY)
  2. Groq Whisper API (GROQ_API_KEY, faster + cheap)
  3. local faster-whisper if installed
  4. local whisper.cpp binary + GGML model — the BUILT-IN path: no API key,
     no Python ML stack, works offline. ``maverick voice setup`` fetches the
     model; ``[voice] stt_backend = "local"`` keeps audio on the machine even
     when provider keys are present.

Text-to-speech backends:
  1. OpenAI TTS API (OPENAI_API_KEY)
  2. ElevenLabs (ELEVENLABS_API_KEY)
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import Tool

log = logging.getLogger(__name__)


_TRANSCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": "Audio file path (mp3, wav, m4a, flac, ogg).",
        },
        "language": {
            "type": "string",
            "description": "ISO 639-1 code (e.g. 'en'). Auto-detect if omitted.",
        },
        "backend": {
            "type": "string",
            "enum": ["openai", "groq", "local", "auto"],
            "description": "Force a specific backend (default 'auto').",
        },
    },
    "required": ["source"],
}


_TTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "Text to synthesize."},
        "voice": {
            "type": "string",
            "description": "Voice id. Backend-specific: 'alloy'/'echo'/... for OpenAI.",
        },
        "backend": {
            "type": "string",
            "enum": ["openai", "elevenlabs", "auto"],
            "description": "Backend (default 'auto').",
        },
        "persona": {
            "type": "string",
            "description": "Named voice persona from [voice.personas] config.",
        },
        "language": {
            "type": "string",
            "description": "Language code; picks the [voice.languages] voice.",
        },
        "output": {
            "type": "string",
            "description": "Output file path. Default: ./speech-<n>.mp3.",
        },
    },
    "required": ["text"],
}


# ---------- STT ----------

def _whisper_openai(audio_path: Path, language: str | None) -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        client = OpenAI(api_key=key)
        with open(audio_path, "rb") as f:
            kwargs: dict[str, Any] = {"model": "whisper-1", "file": f}
            if language:
                kwargs["language"] = language
            resp = client.audio.transcriptions.create(**kwargs)
        return getattr(resp, "text", None) or str(resp)
    except Exception as e:
        log.warning("whisper (openai): %s", e)
        return None


def _whisper_groq(audio_path: Path, language: str | None) -> str | None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        with open(audio_path, "rb") as f:
            kwargs: dict[str, Any] = {
                "model": "whisper-large-v3-turbo", "file": f,
            }
            if language:
                kwargs["language"] = language
            resp = client.audio.transcriptions.create(**kwargs)
        return getattr(resp, "text", None) or str(resp)
    except Exception as e:
        log.warning("whisper (groq): %s", e)
        return None


def _whisper_local(audio_path: Path, language: str | None) -> str | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    try:
        model_size = os.environ.get("MAVERICK_WHISPER_MODEL", "small")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language=language)
        return " ".join(s.text for s in segments).strip()
    except Exception as e:
        log.warning("whisper (local): %s", e)
        return None


_WHISPER_CPP_NAMES = ("whisper-cli", "whisper-cpp", "whisper.cpp")


def _find_whisper_cpp() -> str | None:
    """The whisper.cpp executable, or None.

    Order: ``MAVERICK_WHISPER_CPP`` env > ``[voice] whisper_cpp_bin`` config
    (either may be a PATH name or a file path) > well-known PATH names >
    ``<maverick_home>/bin`` (where desktop installers drop a bundled binary).
    """
    from ..voice_models import _voice_cfg
    for raw in (os.environ.get("MAVERICK_WHISPER_CPP"),
                _voice_cfg().get("whisper_cpp_bin")):
        if raw:
            found = shutil.which(str(raw))
            if found:
                return found
            log.warning("configured whisper.cpp binary %r not found", raw)
    for name in _WHISPER_CPP_NAMES:
        found = shutil.which(name)
        if found:
            return found
    from ..paths import maverick_home
    for name in _WHISPER_CPP_NAMES:
        cand = maverick_home() / "bin" / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _to_wav16k(audio_path: Path) -> Path | None:
    """A 16 kHz mono PCM WAV of ``audio_path`` (whisper.cpp's input format).

    Browsers record webm/ogg and phones m4a, so this converts via ffmpeg —
    always, even for .wav input, since arbitrary WAVs aren't 16 kHz. Returns
    a NEW temp file (caller deletes), or the input itself when it's already
    a .wav and ffmpeg is missing (best effort), or None when conversion is
    impossible.
    """
    from . import sandbox_run
    if not shutil.which("ffmpeg"):
        if audio_path.suffix.lower() == ".wav":
            return audio_path
        log.warning("whisper.cpp needs ffmpeg to decode %s input",
                    audio_path.suffix or "raw")
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    # Host-run on purpose (sandbox=None): the input is a confined/internal
    # temp path and the output is our own temp file, mirroring how the
    # faster-whisper backend already runs on the host.
    code, _out, err = sandbox_run(
        None,
        ["ffmpeg", "-y", "-i", str(audio_path),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp.name],
        timeout=300.0,
    )
    if code != 0:
        log.warning("ffmpeg convert for whisper.cpp failed: %s",
                    err.strip()[-300:])
        Path(tmp.name).unlink(missing_ok=True)
        return None
    return Path(tmp.name)


def _whisper_cpp(audio_path: Path, language: str | None) -> str | None:
    """Transcribe via a local whisper.cpp binary + GGML model, or None when
    either piece is missing / the run fails (the caller falls through)."""
    from . import sandbox_run
    binary = _find_whisper_cpp()
    if not binary:
        return None
    from ..voice_models import locate_model
    model = locate_model()
    if model is None:
        log.warning(
            "whisper.cpp found (%s) but no GGML model installed; "
            "run `maverick voice setup`", binary)
        return None
    wav = _to_wav16k(audio_path)
    if wav is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="whispercpp-") as td:
            prefix = os.path.join(td, "transcript")
            # -otxt/-of: read the transcript from a file instead of scraping
            # stdout, which whisper.cpp mixes with progress/system banners.
            code, _out, err = sandbox_run(
                None,
                [binary, "-m", str(model), "-f", str(wav),
                 "-l", (language or "auto"), "-otxt", "-of", prefix],
                timeout=600.0,
            )
            txt = Path(prefix + ".txt")
            if code != 0 or not txt.is_file():
                log.warning("whisper.cpp (%s) failed: %s",
                            binary, err.strip()[-300:])
                return None
            text = " ".join(txt.read_text(encoding="utf-8").split())
            return text.strip()
    except Exception as e:
        log.warning("whisper (whisper.cpp): %s", e)
        return None
    finally:
        if wav != audio_path:
            wav.unlink(missing_ok=True)


_STT_BACKENDS = ("auto", "openai", "groq", "local")


def _default_stt_backend() -> str:
    """Deployment-wide STT routing when a call doesn't force a backend:
    ``MAVERICK_VOICE_STT_BACKEND`` env > ``[voice] stt_backend`` > ``auto``.
    ``local`` pins transcription on-machine (privacy) even with keys set."""
    raw = (os.environ.get("MAVERICK_VOICE_STT_BACKEND") or "").strip().lower()
    if not raw:
        from ..voice_models import _voice_cfg
        raw = str(_voice_cfg().get("stt_backend") or "").strip().lower()
    if raw and raw not in _STT_BACKENDS:
        log.warning("unknown STT backend %r; using 'auto'", raw)
        raw = ""
    return raw or "auto"


def _run_transcribe(args: dict[str, Any], sandbox: Any = None) -> str:
    src = (args.get("source") or "").strip()
    if not src:
        return "ERROR: source is required"
    # Confine the model-supplied path to the workspace: transcription reads the
    # file and ships its bytes to a Whisper backend, so an unconfined source is
    # arbitrary host-file read + exfiltration (~/.ssh/id_rsa, .env). sandbox=None
    # -- the trusted internal view_video caller's temp path -- passes through.
    from .ffmpeg_tool import _safe_path
    try:
        path = Path(_safe_path(sandbox, src))
    except ValueError as e:
        return f"ERROR: {e}"
    if not path.exists() or not path.is_file():
        return f"ERROR: audio file not found: {src!r}"
    language = args.get("language")
    backend = (args.get("backend") or _default_stt_backend()).lower()

    if backend in ("openai", "auto"):
        out = _whisper_openai(path, language)
        if out is not None:
            return out
        if backend == "openai":
            return "ERROR: OpenAI Whisper failed (check OPENAI_API_KEY)"
    if backend in ("groq", "auto"):
        out = _whisper_groq(path, language)
        if out is not None:
            return out
        if backend == "groq":
            return "ERROR: Groq Whisper failed (check GROQ_API_KEY)"
    if backend in ("local", "auto"):
        out = _whisper_local(path, language)
        if out is None:
            out = _whisper_cpp(path, language)
        if out is not None:
            return out
        if backend == "local":
            return (
                "ERROR: no local STT engine available. Install whisper.cpp "
                "and run `maverick voice setup`, or install faster-whisper "
                "(python -m pip install -e './packages/maverick-core[voice]')."
            )
    return (
        "ERROR: no voice backend available. Set OPENAI_API_KEY / GROQ_API_KEY, "
        "or set up the built-in local engine: install whisper.cpp and run "
        "`maverick voice setup`."
    )


# ---------- TTS ----------

def _tts_openai(text: str, voice: str | None, output: Path) -> bool:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return False
    try:
        from openai import OpenAI
    except ImportError:
        return False
    try:
        client = OpenAI(api_key=key)
        resp = client.audio.speech.create(
            model="tts-1",
            voice=voice or "alloy",
            input=text,
        )
        # Stream to file (SDK exposes a stream_to_file helper).
        try:
            resp.stream_to_file(str(output))
        except AttributeError:
            # Older SDK: write resp.content.
            output.write_bytes(getattr(resp, "content", b""))
        return output.exists() and output.stat().st_size > 0
    except Exception as e:
        log.warning("tts (openai): %s", e)
        return False


def _tts_elevenlabs(text: str, voice: str | None, output: Path) -> bool:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return False
    try:
        import httpx
    except ImportError:
        return False
    voice_id = voice or os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    try:
        resp = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
            timeout=60.0,
        )
        resp.raise_for_status()
        output.write_bytes(resp.content)
        return True
    except Exception as e:
        log.warning("tts (elevenlabs): %s", e)
        return False


def _next_output_path(sandbox: Any = None) -> Path:
    base = Path(sandbox.workdir) if sandbox is not None else Path.cwd()
    i = 1
    while True:
        cand = base / f"speech-{i}.mp3"
        if not cand.exists():
            return cand
        i += 1


def _run_speak(args: dict[str, Any], sandbox: Any = None) -> str:
    # Voice personas / per-language voices ([voice.personas]/[voice.languages]):
    # explicit voice/backend args still win; unknown presets degrade silently.
    try:
        from ..voice_personas import resolve_speech_args
        args = resolve_speech_args(args)
    except Exception:
        pass
    text = (args.get("text") or "").strip()
    if not text:
        return "ERROR: text is required"
    if len(text) > 4096:
        return f"ERROR: text too long ({len(text)} > 4096 chars)"
    # Voice safety pass: never speak a secret/PII aloud -- spoken audio can't
    # be unspoken. Fail-open (a detector bug must not mute the channel).
    try:
        from ..safety.voice_safety import redact_for_speech
        text, _redactions = redact_for_speech(text)
    except Exception:
        pass
    # Confine the output path to the workspace (unconfined => arbitrary write:
    # ~/.ssh/authorized_keys, ~/.maverick/config.toml). The default also lands
    # in the workspace.
    output = args.get("output")
    if output:
        from .ffmpeg_tool import _safe_path
        try:
            output_path = Path(_safe_path(sandbox, output))
        except ValueError as e:
            return f"ERROR: {e}"
    else:
        output_path = _next_output_path(sandbox)
    voice = args.get("voice")
    backend = (args.get("backend") or "auto").lower()

    if backend in ("openai", "auto"):
        if _tts_openai(text, voice, output_path):
            return f"wrote {output_path} ({output_path.stat().st_size} bytes)"
        if backend == "openai":
            return "ERROR: OpenAI TTS failed (check OPENAI_API_KEY)"
    if backend in ("elevenlabs", "auto"):
        if _tts_elevenlabs(text, voice, output_path):
            return f"wrote {output_path} ({output_path.stat().st_size} bytes)"
        if backend == "elevenlabs":
            return "ERROR: ElevenLabs TTS failed (check ELEVENLABS_API_KEY)"
    return (
        "ERROR: no TTS backend available. Set OPENAI_API_KEY or "
        "ELEVENLABS_API_KEY."
    )


def transcribe_audio(sandbox: Any = None) -> Tool:
    return Tool(
        name="transcribe_audio",
        description=(
            "Transcribe an audio file via Whisper. Backends tried in order: "
            "OpenAI (OPENAI_API_KEY), Groq (GROQ_API_KEY, fast+cheap), local "
            "faster-whisper, local whisper.cpp (built-in offline path — "
            "`maverick voice setup`). Accepts mp3/wav/m4a/flac/ogg/webm. "
            "Set `language='en'` to skip auto-detect."
        ),
        input_schema=_TRANSCRIBE_SCHEMA,
        fn=lambda args: _run_transcribe(args, sandbox),
    )


def speak(sandbox: Any = None) -> Tool:
    return Tool(
        name="speak",
        description=(
            "Synthesize speech from text to an mp3 file. Backends: OpenAI "
            "TTS (OPENAI_API_KEY) or ElevenLabs (ELEVENLABS_API_KEY). "
            "Set `voice` for backend-specific id; default OpenAI voice is "
            "'alloy'. Output path defaults to ./speech-N.mp3."
        ),
        input_schema=_TTS_SCHEMA,
        fn=lambda args: _run_speak(args, sandbox),
    )
