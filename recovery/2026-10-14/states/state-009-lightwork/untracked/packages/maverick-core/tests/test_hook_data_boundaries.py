"""Least-privilege, bounded, auditable lifecycle-hook projections."""
from __future__ import annotations

import json
import shlex
import sys
import time

import pytest
from maverick import hooks
from maverick.audit import AuditRefused, EventKind
from maverick.hooks import HookContext, HookEvent


@pytest.fixture(autouse=True)
def _clean_hooks():
    hooks.clear()
    yield
    hooks.clear()


@pytest.mark.asyncio
async def test_hook_content_is_unavailable_without_explicit_privileges():
    seen = []
    hooks.register(HookEvent.POST_TOOL_USE, lambda ctx: seen.append(ctx))

    assert await hooks.dispatch(
        HookContext(
            event=HookEvent.POST_TOOL_USE,
            tool_name="read_file",
            tool_args={"path": "/secret"},
            tool_result="private result",
            extra={"trace": "raw reasoning"},
            data_classification="internal",
        )
    )

    assert len(seen) == 1
    projected = seen[0]
    assert projected.tool_name == "read_file"
    assert projected.tool_args == {}
    assert projected.tool_result is None
    assert projected.extra == {}
    assert projected.content_withheld is True


@pytest.mark.asyncio
async def test_privileged_hook_gets_only_bounded_redacted_content():
    secret = "INTERNAL_API_TOKEN=abcdefghijklmnopqrstuvwxyz123456"
    seen = []
    hooks.register(
        HookEvent.POST_TOOL_USE,
        lambda ctx: seen.append(ctx),
        privileges={"tool_args", "tool_result", "lifecycle_data"},
        data_classifications={"confidential"},
    )

    assert await hooks.dispatch(
        HookContext(
            event=HookEvent.POST_TOOL_USE,
            tool_name="read_file",
            tool_args={"credential": secret, "large": "x" * 50_000},
            tool_result=secret,
            extra={"final": secret},
            data_classification="confidential",
        )
    )

    projected = seen[0]
    rendered = json.dumps(
        {
            "args": projected.tool_args,
            "result": projected.tool_result,
            "extra": projected.extra,
        }
    )
    assert secret not in rendered
    assert "[REDACTED:env_secret]" in rendered
    assert len(projected.tool_args["large"].encode("utf-8")) <= hooks.HOOK_MAX_STRING_BYTES
    assert projected.content_redacted is True
    assert len(rendered.encode("utf-8")) < hooks.HOOK_MAX_PAYLOAD_BYTES


@pytest.mark.asyncio
async def test_classification_filter_and_sealed_state_withhold_content():
    seen = []
    hooks.register(
        HookEvent.STOP,
        lambda ctx: seen.append(ctx),
        privileges={"lifecycle_data"},
        data_classifications={"internal"},
    )

    await hooks.emit(
        HookEvent.STOP,
        extra={"final": "confidential answer"},
        data_classification="confidential",
    )
    await hooks.emit(
        HookEvent.STOP,
        extra={"final": "sealed answer"},
        data_classification="internal",
        sealed=True,
    )

    assert [ctx.extra for ctx in seen] == [{}, {}]
    assert all(ctx.content_withheld for ctx in seen)
    assert seen[1].data_classification == "sealed"


@pytest.mark.asyncio
async def test_redaction_failure_withholds_privileged_content(monkeypatch):
    seen = []
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda ctx: seen.append(ctx) or True,
        privileges={"tool_args"},
        data_classifications={"confidential"},
    )

    def _fail(_text):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr("maverick.safety.secret_detector.redact", _fail)

    # The hook still receives only a withheld projection. The same injected
    # detector failure also prevents the metadata-only hook audit from proving
    # an allow result, so the blocking hook fails closed.
    assert not await hooks.dispatch(
        HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tool_name="shell",
            tool_args={"command": "sensitive"},
            data_classification="confidential",
        )
    )
    assert seen[0].tool_args == {}
    assert seen[0].content_withheld is True


@pytest.mark.asyncio
async def test_hook_outcome_audit_is_metadata_only(monkeypatch):
    captured = []

    def _audit(kind, **payload):
        captured.append((kind, payload))
        return True

    monkeypatch.setattr("maverick.audit.audit_event", _audit)
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda _ctx: False,
        privileges={"tool_args"},
        data_classifications={"confidential"},
    )

    allowed = await hooks.dispatch(
        HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tool_name="shell",
            tool_args={"command": "INTERNAL_API_TOKEN=do-not-persist-1234567890"},
            data_classification="confidential",
            goal_id=7,
        )
    )

    assert allowed is False
    assert captured[0][0] == EventKind.HOOK_OUTCOME
    assert captured[0][1]["status"] == "blocked"
    assert captured[0][1]["goal_id"] == 7
    assert "do-not-persist" not in repr(captured)


@pytest.mark.asyncio
async def test_blocking_hook_refuses_allow_when_audit_is_refused(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise AuditRefused("unavailable")

    monkeypatch.setattr("maverick.audit.audit_event", _refuse)
    hooks.register(HookEvent.PRE_TOOL_USE, lambda _ctx: True)

    assert await hooks.dispatch(
        HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="shell")
    ) is False


def test_unknown_hook_privileges_and_classifications_are_rejected():
    with pytest.raises(ValueError, match="unknown hook privileges"):
        hooks.register(HookEvent.STOP, lambda _ctx: True, privileges={"secrets"})
    with pytest.raises(ValueError, match="unknown hook data classifications"):
        hooks.register(
            HookEvent.STOP,
            lambda _ctx: True,
            data_classifications={"sealed"},
        )


def test_hook_registry_has_a_hard_capacity(monkeypatch):
    monkeypatch.setattr(hooks, "HOOK_MAX_REGISTERED", 1)
    hooks.register(HookEvent.STOP, lambda _ctx: True)

    with pytest.raises(RuntimeError, match="registry is full"):
        hooks.register(HookEvent.STOP, lambda _ctx: True)


@pytest.mark.asyncio
async def test_synchronous_callable_timeout_is_truthful_and_blocks(monkeypatch):
    captured = []

    def _audit(kind, **payload):
        captured.append((kind, payload))
        return True

    def _slow(_ctx):
        time.sleep(0.1)
        return True

    monkeypatch.setattr("maverick.audit.audit_event", _audit)
    hooks.register(HookEvent.PRE_TOOL_USE, _slow, timeout_ms=10)

    assert not await hooks.dispatch(
        HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="shell")
    )
    assert captured[0][1]["status"] == "timeout_still_running"


@pytest.mark.asyncio
async def test_shell_hook_stdin_is_bounded_and_redacted(tmp_path):
    destination = tmp_path / "payload.json"
    script = tmp_path / "capture.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    command = shlex.join([sys.executable, str(script), str(destination)])
    hooks.register(
        HookEvent.POST_TOOL_USE,
        command,
        privileges={"tool_args", "tool_result", "lifecycle_data"},
        data_classifications={"confidential"},
    )

    assert await hooks.dispatch(
        HookContext(
            event=HookEvent.POST_TOOL_USE,
            tool_name="read_file",
            tool_args={"large": "x" * 100_000},
            tool_result="INTERNAL_API_TOKEN=abcdefghijklmnopqrstuvwxyz123456",
            extra={"large": "y" * 100_000},
            data_classification="confidential",
        )
    )

    payload = destination.read_bytes()
    assert len(payload) <= hooks.HOOK_MAX_PAYLOAD_BYTES
    assert b"abcdefghijklmnopqrstuvwxyz123456" not in payload
    parsed = json.loads(payload)
    assert parsed["content_redacted"] is True
