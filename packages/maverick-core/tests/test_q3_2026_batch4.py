"""Q3 2026 batch 4: Kubernetes sandbox, HuggingFace tool,
context compactor, OTEL span wiring."""
from __future__ import annotations

import sys
import tarfile
import types
from unittest.mock import MagicMock

import pytest

from .test_kubernetes_workspace_transfer import _capture_using_fake_run


@pytest.fixture(autouse=True)
def _synthetic_archive_capture(monkeypatch):
    monkeypatch.setattr("maverick.sandbox.workspace_transfer.capture_archive", _capture_using_fake_run)

# ---------- Kubernetes sandbox ----------
#
# exec() now runs a multi-step pod lifecycle instead of one `kubectl run --rm
# -i -- sh -c cmd`: start a detached pod (`sleep`), wait for Ready, `kubectl
# cp` the host workdir in, `kubectl exec` the real command, `kubectl cp` any
# changes back out, then delete the pod. The old one-shot approach never gave
# `kubectl cp` a running pod to target, so the workspace was never
# transferred (platform audit W1-86) -- these tests exercise the new
# pipeline instead of asserting on a single captured `kubectl run` call.

def _subcommand(args):
    """First non-flag token after ``kubectl`` (skipping ``--context X`` /
    ``-n X``) -- e.g. "run"/"wait"/"cp"/"exec"/"delete"/"version"."""
    i = 1
    while i < len(args):
        tok = args[i]
        if tok in ("--context", "-n"):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok
    return None


def _calls_with(calls, subcommand):
    return [c for c in calls if _subcommand(c) == subcommand]


def _fake_kubectl(monkeypatch, *, run_rc=0, wait_rc=0, cp_in_rc=0, cp_out_rc=0,
                   exec_rc=0, exec_stdout="ran\n", exec_stderr="",
                   run_stderr="", wait_stderr="", cp_in_stderr="", cp_out_stderr=""):
    """Patch subprocess.run with a per-subcommand outcome; return every
    invocation's argv (oldest first) for order/content assertions."""
    calls: list[list] = []

    def _fake_run(args, *a, **k):
        calls.append(list(args))
        sub = _subcommand(args)
        if sub == "version":
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if sub == "run":
            return MagicMock(returncode=run_rc, stdout="", stderr=run_stderr)
        if sub == "wait":
            return MagicMock(returncode=wait_rc, stdout="", stderr=wait_stderr)
        if sub == "cp":
            src = args[args.index("cp") + 1]
            if ":" in src:  # POD:PATH source -> copying OUT of the pod
                return MagicMock(returncode=cp_out_rc, stdout="", stderr=cp_out_stderr)
            return MagicMock(returncode=cp_in_rc, stdout="", stderr=cp_in_stderr)
        if sub == "exec":
            if "tar" in args:
                if cp_out_rc == 0:
                    with tarfile.open(fileobj=k["stdout"], mode="w"):
                        pass
                return MagicMock(returncode=cp_out_rc, stdout=b"", stderr=cp_out_stderr.encode())
            return MagicMock(returncode=exec_rc, stdout=exec_stdout, stderr=exec_stderr)
        if sub == "delete":
            return MagicMock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected kubectl invocation: {args}")

    monkeypatch.setattr("subprocess.run", _fake_run)
    return calls


def test_k8s_missing_kubectl(monkeypatch):
    from maverick.sandbox.kubernetes import KubernetesBackend

    def _no_kubectl(args, *a, **k):
        if args and args[0] == "kubectl":
            raise FileNotFoundError("kubectl")
        raise AssertionError(f"unexpected: {args}")

    monkeypatch.setattr("subprocess.run", _no_kubectl)
    try:
        KubernetesBackend()
    except RuntimeError as e:
        assert "kubectl" in str(e).lower()
        return
    raise AssertionError("expected RuntimeError")


def test_k8s_exec_pipeline_order_and_no_rm(monkeypatch, tmp_path):
    """The old single-shot `kubectl run --rm -i` never gave `kubectl cp` a
    running pod to stream into. The pod must now start detached (no --rm,
    no -i), wait for Ready, then cp-in / exec / cp-out / delete in order."""
    calls = _fake_kubectl(monkeypatch, exec_stdout="hi\n")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(image="alpine", namespace="ci", allow_network=True,
                                workdir=tmp_path / "workspace")
    result = backend.exec("echo hi")
    assert result.ok
    assert result.stdout == "hi\n"

    assert [_subcommand(c) for c in calls] == [
        "version", "run", "wait", "cp", "exec", "exec", "delete",
    ]

    run_args = _calls_with(calls, "run")[0]
    assert "--rm" not in run_args
    assert "-i" not in run_args
    assert "--restart=Never" in run_args
    assert "--image=alpine" in run_args
    assert "-n" in run_args and "ci" in run_args

    exec_args = _calls_with(calls, "exec")[0]
    assert "echo hi" in exec_args[-1]


def test_k8s_copies_workspace_into_pod_before_running(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(namespace="ci", allow_network=True, workdir=tmp_path / "workspace")
    backend.exec("true")

    cp_in = _calls_with(calls, "cp")[0]
    assert cp_in[cp_in.index("cp") + 1] == "./."
    dest = cp_in[cp_in.index("cp") + 2]
    assert dest.startswith("ci/") and dest.endswith(":/workspace")


def test_k8s_copies_pod_workspace_back_out_after_running(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(namespace="ci", allow_network=True, workdir=tmp_path / "workspace")
    backend.exec("true")

    export = _calls_with(calls, "exec")[1]
    assert export[-6:] == ["tar", "-C", "/workspace", "-cf", "-", "."]
    assert 'kill -s STOP -1' in export[export.index("-c") + 1]


def test_k8s_copy_in_failure_skips_exec_and_still_deletes_pod(monkeypatch, tmp_path):
    """A workspace that never made it into the pod must not silently run the
    command against an empty directory (the original bug) -- fail closed."""
    calls = _fake_kubectl(monkeypatch, cp_in_rc=1, cp_in_stderr="no such file or directory")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    result = backend.exec("echo hi")
    assert result.exit_code != 0
    assert "kubectl cp" in result.stderr
    assert not _calls_with(calls, "exec")
    assert len(_calls_with(calls, "delete")) == 1


def test_k8s_copy_out_failure_does_not_claim_workspace_delivery(monkeypatch, tmp_path):
    """Keep command output but report that returning the output files failed."""
    calls = _fake_kubectl(monkeypatch, exec_stdout="hi\n", cp_out_rc=1,
                          cp_out_stderr="connection refused")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    result = backend.exec("echo hi")
    assert result.exit_code == 74
    assert result.stdout == "hi\n"
    assert "workspace export failed" in result.stderr
    assert not _calls_with(calls, "delete"), "failed export must retain the pod"
    assert calls  # sanity: the fake actually ran


def test_k8s_pod_not_ready_fails_without_running_command(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch, wait_rc=1, wait_stderr="timed out waiting for condition")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    result = backend.exec("echo hi")
    assert result.exit_code != 0
    assert "Ready" in result.stderr
    assert not _calls_with(calls, "cp")
    assert not _calls_with(calls, "exec")
    assert len(_calls_with(calls, "delete")) == 1


def test_k8s_passes_context(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(context="minikube", allow_network=True, workdir=tmp_path / "workspace")
    backend.exec("true")
    run_args = _calls_with(calls, "run")[0]
    assert "--context" in run_args and "minikube" in run_args


def test_build_sandbox_constructs_k8s(monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, *a, **k: MagicMock(returncode=0, stdout=b"", stderr=b""),
    )
    import maverick.config as cfg

    def _fake_cfg():
        return {"sandbox": {"backend": "kubernetes", "namespace": "x",
                            "image": "alpine", "timeout": 30}}

    monkeypatch.setattr(cfg, "load_config", _fake_cfg)
    monkeypatch.setattr(cfg, "get_sandbox", lambda: _fake_cfg()["sandbox"])
    from maverick.sandbox import build_sandbox
    sb = build_sandbox()
    assert sb.__class__.__name__ == "KubernetesBackend"
    assert sb.namespace == "x"


def test_k8s_disallow_network_fails_closed(monkeypatch):
    calls = {"n": 0}

    def _fake_run(args, *a, **k):
        calls["n"] += 1
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", _fake_run)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=False)
    out = backend.exec("echo hi")
    assert out.exit_code == 2
    assert "allow_network=false" in out.stderr
    # Constructor verification plus the one NetworkPolicy lookup (W1-104):
    # with no deny-all policy in the namespace, no pod is ever started.
    assert calls["n"] == 2


def test_k8s_pod_overrides_non_root_and_limits(monkeypatch, tmp_path):
    """The pod spec must run non-root, drop caps, block privilege escalation,
    and carry resource limits (issue #461), plus the workspace emptyDir
    mount `kubectl cp` streams the host workdir into/out of (W1-86)."""
    import json

    calls = _fake_kubectl(monkeypatch, exec_stdout="ok\n")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(image="alpine", allow_network=True,
                                memory="2g", cpus="1", workdir=tmp_path / "workspace")
    backend.exec("echo hi")
    run_args = _calls_with(calls, "run")[0]
    assert "--overrides" in run_args
    overrides = json.loads(run_args[run_args.index("--overrides") + 1])
    pod_sc = overrides["spec"]["securityContext"]
    assert pod_sc["runAsNonRoot"] is True
    assert pod_sc["runAsUser"] == 1000
    assert pod_sc["fsGroup"] == 1000
    assert pod_sc["runAsGroup"] == 1000
    assert "--override-type=strategic" in run_args
    assert "--command" in run_args
    container = overrides["spec"]["containers"][0]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert container["resources"]["limits"] == {"memory": "2Gi", "cpu": "1"}
    assert overrides["spec"]["automountServiceAccountToken"] is False
    assert container["volumeMounts"] == [{"name": "workspace", "mountPath": "/workspace"}]
    assert overrides["spec"]["volumes"] == [{"name": "workspace", "emptyDir": {"sizeLimit": str(512 * 1024 * 1024)}}]
    assert overrides["spec"]["activeDeadlineSeconds"] >= 3600


def test_k8s_disallow_network_emits_networkpolicy_hint(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        "subprocess.run",
        lambda args, *a, **k: MagicMock(returncode=0, stdout=b"", stderr=b""),
    )
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(namespace="ci", allow_network=False)
    with caplog.at_level(logging.WARNING):
        out = backend.exec("echo hi")
    assert out.exit_code == 2
    assert "NetworkPolicy" in caplog.text
    assert "kind: NetworkPolicy" in caplog.text
    assert "namespace: ci" in caplog.text


def _assert_deleted_pod(deletes):
    assert len(deletes) == 1, f"expected exactly one delete, got {deletes}"
    args = deletes[0]
    assert "delete" in args and "pod" in args
    # The pod name is the argv right after "pod".
    pod = args[args.index("pod") + 1]
    assert pod.startswith("maverick-sb-")
    assert "--force" in args and "--ignore-not-found=true" in args


def test_k8s_deletes_pod_on_success(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch, exec_stdout="ok\n")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    out = backend.exec("echo hi")
    assert out.exit_code == 0
    _assert_deleted_pod(_calls_with(calls, "delete"))


def test_k8s_deletes_pod_on_nonzero_exit(monkeypatch, tmp_path):
    calls = _fake_kubectl(monkeypatch, exec_rc=7, exec_stdout="", exec_stderr="boom")
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    out = backend.exec("false")
    assert out.exit_code == 7
    _assert_deleted_pod(_calls_with(calls, "delete"))


def test_k8s_deletes_pod_on_timeout(monkeypatch, tmp_path):
    import subprocess

    calls: list[list] = []

    def _fake_run(args, *a, **k):
        calls.append(list(args))
        sub = _subcommand(args)
        if sub == "exec":
            if "tar" in args:
                with tarfile.open(fileobj=k["stdout"], mode="w"):
                    pass
                return MagicMock(returncode=0, stderr=b"")
            raise subprocess.TimeoutExpired(cmd=args, timeout=1)
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", _fake_run)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    out = backend.exec("sleep 999")
    assert out.exit_code == 124
    _assert_deleted_pod(_calls_with(calls, "delete"))


def test_k8s_deletes_pod_on_unexpected_error(monkeypatch, tmp_path):
    calls: list[list] = []

    def _fake_run(args, *a, **k):
        calls.append(list(args))
        sub = _subcommand(args)
        if sub == "version":
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if sub == "delete":
            return MagicMock(returncode=0, stdout="", stderr="")
        raise OSError("kubectl killed")

    monkeypatch.setattr("subprocess.run", _fake_run)
    from maverick.sandbox.kubernetes import KubernetesBackend
    backend = KubernetesBackend(allow_network=True, workdir=tmp_path / "workspace")
    assert backend.exec("echo hi").exit_code == 74
    # No command started, so the empty pod can be cleaned up.
    _assert_deleted_pod(_calls_with(calls, "delete"))


# ---------- HuggingFace tool ----------

def test_hf_requires_op():
    from maverick.tools.huggingface import huggingface
    assert "op is required" in huggingface().fn({})


def test_hf_requires_model():
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "infer"})
    assert "model is required" in out


def test_hf_missing_httpx(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "infer", "model": "x", "inputs": "y"})
    assert "httpx not installed" in out


def test_hf_infer_calls_api(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=[
        {"label": "POSITIVE", "score": 0.99},
    ])
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.post = MagicMock(return_value=resp)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_xxx")
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({
        "op": "infer", "model": "distilbert-base-uncased-finetuned-sst-2-english",
        "inputs": "I love this!",
    })
    assert "POSITIVE" in out
    call_kwargs = fake_httpx.post.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"].startswith("Bearer hf_xxx")
    assert call_kwargs["json"]["inputs"] == "I love this!"


def test_hf_infer_error_propagates(monkeypatch):
    resp = MagicMock()
    resp.status_code = 503
    resp.json = MagicMock(return_value={"error": "Model loading"})
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.post = MagicMock(return_value=resp)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "infer", "model": "x", "inputs": "y"})
    assert "ERROR" in out and "503" in out


def test_hf_summarize_requires_text():
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "summarize", "model": "x"})
    assert "non-empty text" in out


def test_hf_image_classify_requires_url():
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "image_classify", "model": "x"})
    assert "requires url" in out


def test_hf_image_classify_blocks_private_ip(monkeypatch):
    # Now routed through the IP-pinning _ssrf.safe_get (closes the TOCTOU
    # rebind window); a blocked host surfaces as BlockedHost.
    import maverick.tools._ssrf as ssrf
    from maverick.tools.huggingface import huggingface

    def _boom(url, **k):
        raise ssrf.BlockedHost("127.0.0.1 is loopback")

    monkeypatch.setattr(ssrf, "safe_get", _boom)
    out = huggingface().fn({"op": "image_classify", "model": "x", "url": "http://127.0.0.1/a.png"})
    assert "refusing" in out


def test_hf_image_classify_rejects_redirects(monkeypatch):
    import maverick.tools._ssrf as ssrf
    resp = MagicMock()
    resp.status_code = 302
    resp.content = b""
    resp.headers = {"content-type": "text/plain"}
    monkeypatch.setattr(ssrf, "safe_get", lambda url, **k: resp)
    from maverick.tools.huggingface import huggingface
    out = huggingface().fn({"op": "image_classify", "model": "x", "url": "https://example.com/i.png"})
    assert "image fetch 302" in out


# ---------- Context compactor ----------

def test_compactor_passes_through_under_budget():
    from maverick.context_compactor import compact
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    out = compact(msgs, target_tokens=1000)
    assert out.messages == msgs
    assert out.dropped == []
    assert out.kept_marker is None


def test_compactor_drops_least_relevant_when_over_budget():
    from maverick.context_compactor import compact
    msgs = []
    # 10 old turns about completely unrelated topics (low relevance).
    for i in range(10):
        msgs.append({"role": "user",
                     "content": f"old topic {i}: " + "padding " * 50})
        msgs.append({"role": "assistant",
                     "content": f"reply about topic {i}: " + "filler " * 50})
    # Current user message about a different topic.
    msgs.append({"role": "user", "content": "current question about feature X"})
    msgs.append({"role": "assistant", "content": "reply about feature X"})

    out = compact(msgs, target_tokens=200, preserve_tail=2)
    assert out.tokens_after <= out.tokens_before
    assert len(out.dropped) > 0
    assert out.kept_marker is not None
    assert "compacted" in out.kept_marker
    # The last two turns are always preserved verbatim.
    assert out.messages[-2:] == msgs[-2:]


def test_compactor_keeps_most_relevant_older_turn():
    from maverick.context_compactor import compact
    msgs = [
        # Highly relevant older turn (shares vocab with the query)
        {"role": "user", "content": "tell me about feature X and its history"},
        {"role": "assistant", "content": "feature X dates back to 2024 ..."},
        # Filler older turns — heavy enough to push over the budget.
        *[
            {"role": "user",
             "content": f"weather report number {i} " + ("padding " * 60)}
            for i in range(8)
        ],
        # The tail / current focus
        {"role": "user", "content": "now explain feature X again briefly"},
    ]
    out = compact(msgs, target_tokens=80, preserve_tail=1)
    # Drop happened.
    assert out.dropped
    # The relevant first turn survives (in either order).
    survived = [m for m in out.messages if "history" in str(m.get("content", ""))]
    assert survived, "the most-relevant old turn should be kept"


def test_compactor_empty_history():
    from maverick.context_compactor import compact
    r = compact([], target_tokens=100)
    assert r.messages == []
    assert r.dropped == []
    assert r.tokens_before == r.tokens_after == 0


def test_compactor_estimate_tokens_increases_with_text():
    from maverick.context_compactor import estimate_tokens
    short = estimate_tokens([{"role": "user", "content": "hi"}])
    long = estimate_tokens([{"role": "user", "content": "hi " * 500}])
    assert long > short


# ---------- OTEL span wiring ----------

def test_tool_dispatch_opens_span(monkeypatch):
    """ToolRegistry.run wraps the call in a trace_span context."""
    import asyncio

    from maverick.tools import Tool, ToolRegistry

    seen = {"name": None, "attrs": None}

    class _FakeCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_trace_span(name, attributes=None):
        seen["name"] = name
        seen["attrs"] = attributes
        return _FakeCtx()

    import maverick.observability as obs
    monkeypatch.setattr(obs, "trace_span", _fake_trace_span)

    reg = ToolRegistry()
    reg.register(Tool(
        name="echo", description="echo",
        input_schema={"type": "object", "properties": {}},
        fn=lambda args: "ok",
    ))
    out = asyncio.run(reg.run("echo", {}))
    assert out == "ok"
    assert seen["name"] == "tool.run"
    # The span carries the back-compat tool.name plus the GenAI-semconv tool
    # attributes (gen_ai.operation.name/tool.name/tool.type) merged in.
    assert seen["attrs"]["tool.name"] == "echo"
    assert seen["attrs"]["gen_ai.operation.name"] == "execute_tool"
    assert seen["attrs"]["gen_ai.tool.name"] == "echo"


def test_llm_complete_opens_span(monkeypatch):
    from maverick.llm import LLM

    seen = {"calls": []}

    class _FakeCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_trace_span(name, attributes=None):
        seen["calls"].append((name, attributes))
        return _FakeCtx()

    import maverick.observability as obs
    monkeypatch.setattr(obs, "trace_span", _fake_trace_span)

    class _FakeResp:
        text = "ok"
        thinking = None
        tool_calls = []
        stop_reason = "end_turn"
        cache_creation_tokens = 0
        cache_read_tokens = 0
        raw = None
        thinking_blocks = []
        thinking_signature = None

    class _FakeClient:
        def complete(self, **kwargs):
            return _FakeResp()

    llm = LLM(model="anthropic:claude-haiku-4-5-20251001", api_key="dummy")
    monkeypatch.setattr(llm, "_get_client", lambda provider: _FakeClient())

    llm.complete(system="s", messages=[{"role": "user", "content": "hi"}])
    assert seen["calls"]
    name, attrs = seen["calls"][0]
    # The LLM span now follows the OTel GenAI semantic conventions: span name
    # "<operation> <model>" plus gen_ai.* attributes. The legacy llm.provider
    # attribute is kept alongside for back-compat. (Assert on the convention
    # shape, not the exact model-id spelling, so the test doesn't couple to
    # how the id is normalized.)
    assert name.startswith("chat claude-haiku-4-5")
    assert attrs and attrs.get("gen_ai.system") == "anthropic"
    assert attrs.get("gen_ai.request.model") == name.split(" ", 1)[1]
    assert attrs.get("llm.provider") == "anthropic"


# ---------- registration smoke ----------

def test_huggingface_registers(tmp_path):
    from maverick.sandbox.local import LocalBackend
    from maverick.tools import base_registry

    class _W:
        def open_questions(self, gid):
            return []

    reg = base_registry(_W(), LocalBackend(workdir=tmp_path / "workspace"))
    names = {t.name for t in reg.all()}
    assert "huggingface" in names
