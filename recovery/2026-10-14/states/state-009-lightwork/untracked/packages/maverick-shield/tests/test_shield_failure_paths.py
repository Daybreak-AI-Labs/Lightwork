"""Real Shield failure paths must be blocking, explicit verdicts."""
from __future__ import annotations

from types import SimpleNamespace

import maverick_shield.deobfuscate as deobfuscate
import maverick_shield.guard as guard
import maverick_shield.phishing as phishing
from maverick_shield import Shield


def _assert_scan_error(verdict, detector: str) -> None:
    assert verdict.allowed is False
    assert verdict.scan_error is True
    assert verdict.severity == "critical"
    assert verdict.reasons == [f"shield detector error: {detector}"]


def _sdk_shield(scanner: object) -> Shield:
    shield = Shield(backend="builtin", warn_if_missing=False)
    shield.backend = Shield.BACKEND_SDK
    shield._sdk = scanner
    return shield


def test_builtin_exception_returns_blocking_error_verdict(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("missing rule assets")

    monkeypatch.setattr(guard, "builtin_scan", _raise)
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_input("hello")
    _assert_scan_error(verdict, "builtin scan")


def test_builtin_malformed_response_returns_blocking_error_verdict(monkeypatch):
    monkeypatch.setattr(guard, "builtin_scan", lambda *_a, **_k: (False, "none"))
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_input("hello")
    _assert_scan_error(verdict, "builtin scan")


def test_sdk_timeout_returns_blocking_error_verdict():
    class _TimedOutSDK:
        @staticmethod
        def scanInput(_text):  # noqa: N802 - third-party SDK method
            raise TimeoutError("deadline exceeded")

    verdict = _sdk_shield(_TimedOutSDK()).scan_input("hello")
    _assert_scan_error(verdict, "agent-shield SDK scan")


def test_sdk_malformed_response_returns_blocking_error_verdict():
    class _MalformedSDK:
        @staticmethod
        def scanInput(_text):  # noqa: N802 - third-party SDK method
            return SimpleNamespace(severity="none")

    verdict = _sdk_shield(_MalformedSDK()).scan_input("hello")
    _assert_scan_error(verdict, "agent-shield SDK scan")


def test_sdk_initialization_failure_is_not_downgraded_to_clean_builtin(monkeypatch):
    def _fail_init(**_kwargs):
        raise FileNotFoundError("model asset missing")

    monkeypatch.setattr(guard, "_HAVE_SDK", True)
    monkeypatch.setattr(guard, "AgentShield", _fail_init)
    shield = Shield(backend="auto", warn_if_missing=False)

    assert shield.backend == Shield.BACKEND_BUILTIN
    _assert_scan_error(shield.scan_input("benign text"), "agent-shield initialization")


def test_explicit_missing_sdk_blocks_while_auto_mode_can_use_builtin(monkeypatch):
    monkeypatch.setattr(guard, "_HAVE_SDK", False)
    explicit = Shield(backend="agent-shield", warn_if_missing=False)
    automatic = Shield(backend="auto", warn_if_missing=False)

    _assert_scan_error(explicit.scan_input("benign text"), "agent-shield unavailable")
    assert automatic.scan_input("benign text").allowed is True


def test_partial_output_policy_failure_blocks_otherwise_clean_output(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(guard, "output_policy_scan", _raise)
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_output(
        "ordinary summary"
    )
    _assert_scan_error(verdict, "output-policy scan")


def test_malformed_output_policy_response_blocks(monkeypatch):
    monkeypatch.setattr(guard, "output_policy_scan", lambda *_a, **_k: None)
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_output(
        "ordinary summary"
    )
    _assert_scan_error(verdict, "output-policy scan")


def test_partial_phishing_detector_failure_blocks(monkeypatch):
    def _raise(_text):
        raise RuntimeError("phishing model unavailable")

    monkeypatch.setattr(phishing, "detect_phishing", _raise)
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_output(
        "ordinary summary"
    )
    _assert_scan_error(verdict, "phishing scan")


def test_partial_constitutional_detector_failure_blocks(monkeypatch):
    shield = Shield(
        backend="builtin",
        warn_if_missing=False,
        constitution=[{"name": "policy", "pattern": "blocked", "severity": "high"}],
    )

    def _raise(*_args, **_kwargs):
        raise RuntimeError("policy rules unavailable")

    monkeypatch.setattr(guard, "_constitutional_scan", _raise)
    _assert_scan_error(shield.scan_input("ordinary text"), "constitutional scan")


def test_decode_prepass_failure_blocks_otherwise_clean_input(monkeypatch):
    def _raise(_text):
        raise RuntimeError("decoder unavailable")

    monkeypatch.setattr(deobfuscate, "decoded_variants", _raise)
    verdict = Shield(backend="builtin", warn_if_missing=False).scan_input(
        "ordinary text"
    )
    _assert_scan_error(verdict, "decode pre-pass")


def test_tool_argument_normalization_failure_blocks_tool_call():
    class _Unrenderable:
        def __str__(self):
            raise RuntimeError("cannot render")

    verdict = Shield(backend="builtin", warn_if_missing=False).scan_tool_call(
        "custom_tool", {"payload": _Unrenderable()}
    )
    _assert_scan_error(verdict, "tool argument normalization")
