"""Tail-cap defaults: the constants that bound worst-case token injection.

These pins are deliberate: each default was audited (July 2026 token-
efficiency pass) and lowered from a value that let a single tool result,
page fetch, or retry storm dominate the context window or output bill.
Raising one back is a cost decision — do it via the env knob, not here.
"""
from __future__ import annotations

import inspect


def test_single_tool_result_cap_is_32k():
    # Read at import with the env unset in CI; no reload (reloading
    # maverick.agent would re-mint class objects other tests hold).
    import maverick.agent as agent_mod
    assert agent_mod._MAX_TOOL_RESULT_BYTES == 32_000


def test_llm_retry_attempts_default_3(monkeypatch):
    monkeypatch.delenv("MAVERICK_LLM_RETRY_ATTEMPTS", raising=False)
    import importlib

    from maverick import retry
    importlib.reload(retry)
    try:
        assert retry.MAX_ATTEMPTS == 3
    finally:
        importlib.reload(retry)


def test_verifier_verdict_defaults_512():
    from maverick.verifier import verify_proposal, verify_proposal_structured
    assert inspect.signature(verify_proposal).parameters["max_tokens"].default == 512
    assert (inspect.signature(verify_proposal_structured)
            .parameters["max_tokens"].default == 512)


def test_reviewer_verdict_default_1024():
    from maverick.reviewer import review_diff
    assert inspect.signature(review_diff).parameters["max_tokens"].default == 1024
