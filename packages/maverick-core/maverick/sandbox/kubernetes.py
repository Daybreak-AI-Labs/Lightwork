"""Kubernetes sandbox backend.

Spawns a transient pod per ``exec()`` call: the pod is started detached
(running ``sleep``) with an emptyDir volume mounted at ``/workspace``, the
host ``workdir`` is streamed in via ``kubectl cp``, the command runs via
``kubectl exec``, any files it wrote are streamed back out via ``kubectl
cp``, and the pod is deleted on exit -- so exec() semantics (a workspace
that persists across calls, host-visible after each command) match the
other container backends for copied-in and exported files despite Kubernetes
having no host bind-mount primitive. This copy transport does not mirror
deletions back to the host; use a bind-mounted backend for that requirement.

Why kubectl-driven instead of the official Python client?
  - The Python client adds 50+ MB of generated code + grpcio + a
    handful of transitive deps. ``kubectl`` is one binary the user
    already has if they're running on K8s.
  - The exec semantics map 1:1 with our other sandboxes (Docker,
    Podman, Devcontainer); the abstraction is "spawn a fresh
    container per command", not "manage long-lived pods".

Config::

    [sandbox]
    backend = "kubernetes"
    image = "python:3.12-slim"
    namespace = "default"
    context = "minikube"        # optional kubeconfig context
    workdir = "/home/me/maverick-workspace"   # HOST directory (like every
                                               # other backend); streamed
                                               # into the pod's /workspace
    timeout = 120
    allow_network = false        # requires a deny-all NetworkPolicy (below)

``allow_network = false`` (the default, and what the wizard writes) means
"no egress". A transient ``kubectl run`` pod cannot enforce that on
itself, so before every command the backend checks the namespace for
a deny-all NetworkPolicy (empty ``podSelector``, ``Egress`` in
``policyTypes``, no egress rules). When one is present the commands run
under it; when none is, exec() fails closed with exit code 2 and prints
the manifest to apply (``_DENY_ALL_NETWORKPOLICY``). ``allow_network =
true`` skips the check and lets pods use the cluster network. Policies are
additive: use a dedicated namespace without egress allow rules and a CNI that
enforces NetworkPolicy. The API check does not test live packet filtering.

Loud-fallback: missing kubectl binary or ``kubectl version`` failure
raises ``RuntimeError`` at construction so the wizard's smoke test
catches it before the agent runs. The container image must have ``tar``
(``kubectl cp`` streams a tarball over ``kubectl exec``) -- true of every
common base image (python/node/ubuntu/alpine slim variants all ship it).
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .local import ExecResult, scrub_env

log = logging.getLogger(__name__)

# The in-pod absolute path the host workdir is streamed into. Hard-coded
# (not user-configurable) exactly like DockerBackend's container-side
# "/workspace" mount point -- only the HOST side is configurable, via
# ``[sandbox] workdir``.
_POD_WORKDIR = "/workspace"

# Deny-all NetworkPolicy the operator applies once per namespace so pods get no
# egress (and no ingress). Surfaced when allow_network=false, since a transient
# `kubectl run` pod can't enforce this on itself.
_DENY_ALL_NETWORKPOLICY = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: maverick-deny-all
  namespace: {ns}
spec:
  podSelector: {{}}
  policyTypes: [Ingress, Egress]\
"""


@dataclass
class KubernetesBackend:
    image: str = "python:3.12-slim"
    namespace: str = "default"
    context: str | None = None
    # The HOST directory commands run against -- same contract as every
    # other backend's ``workdir`` (Docker's bind-mount source, Podman's,
    # Devcontainer's ``project_dir``): file tools resolve model-supplied
    # paths against it, and it's what this backend streams into/out of the
    # pod via ``kubectl cp``. Defaults to cwd so direct construction (tests,
    # ad-hoc scripts) doesn't require passing one explicitly.
    workdir: Path = field(default_factory=Path.cwd)
    timeout: float = 120.0
    allow_network: bool = False
    extra_kubectl_args: list[str] = field(default_factory=list)
    # Containment for a possibly prompt-injected agent (mirrors docker/podman):
    # never run as uid 0, block privilege escalation, and bound RAM/CPU so a
    # runaway can't exhaust the node. Falsy memory/cpu disables that limit.
    run_as_user: int = 1000
    memory: str | None = "4g"
    cpus: str | None = None
    # Fixed budgets for the pod-lifecycle steps `exec()` now performs beyond
    # running the command itself: waiting for the pod to become Ready before
    # `kubectl cp`/`kubectl exec` can reach it, and each `kubectl cp` leg.
    # Not exposed as [sandbox] config -- these bound internal plumbing, not a
    # user-facing capability (contrast ``timeout``, which bounds the command).
    pod_ready_timeout: float = 60.0
    transfer_timeout: float = 120.0

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._verify_kubectl()

    def deny_all_networkpolicy_manifest(self) -> str:
        """The deny-all NetworkPolicy for this namespace, ready to apply."""
        return _DENY_ALL_NETWORKPOLICY.format(ns=self.namespace)

    def _deny_all_policy_present(self) -> bool:
        """True when the namespace carries a NetworkPolicy that denies all
        egress for every pod: an empty ``podSelector``, ``Egress`` in
        ``policyTypes`` and no ``egress`` rules. Any kubectl or parse failure
        counts as absent (fail closed)."""
        args = [*self._kubectl_prefix(), "get", "networkpolicy", "-o", "json"]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=15, env=scrub_env(),
            )
            if proc.returncode != 0:
                return False
            items = json.loads(proc.stdout or "{}").get("items") or []
        except (subprocess.TimeoutExpired, OSError, ValueError, AttributeError):
            return False
        if not isinstance(items, list):
            return False
        found = False
        for item in items:
            spec = item.get("spec") if isinstance(item, dict) else None
            if not isinstance(spec, dict):
                return False
            # NetworkPolicies are additive. A second policy with allow rules
            # can reopen egress despite a namespace-wide deny policy. Use a
            # dedicated namespace with no egress grants; reject ambiguous scopes.
            if spec.get("egress"):
                return False
            selector = spec.get("podSelector") or {}
            if not isinstance(selector, dict):
                return False
            if selector.get("matchLabels") or selector.get("matchExpressions"):
                continue  # scoped to some pods, not the whole namespace
            if "Egress" not in (spec.get("policyTypes") or []):
                continue
            if spec.get("egress"):
                continue  # has allow rules, so not deny-all
            found = True
        return found

    def _pod_overrides(self, container_name: str) -> dict:
        """A non-root, resource-bounded pod spec for ``kubectl run --overrides``.

        ``kubectl run`` otherwise emits a bare pod that runs as the image's
        default user (often root) with no resource limits and the namespace's
        default ServiceAccount token auto-mounted. We pin a non-root
        securityContext + drop all capabilities + block privilege escalation,
        disable the ServiceAccount token automount (a prompt-injected agent in
        the pod has no cluster API credential to steal), and set
        requests/limits so a runaway can't starve the node.

        ``container_name`` MUST be the pod name: ``kubectl run`` names the
        single container after the pod, and a per-container ``securityContext``
        only takes effect when the override's container name matches the
        generated one. A hard-coded mismatched name would silently leave the
        container hardening (drop-ALL caps, no-priv-escalation) unapplied."""
        limits: dict[str, str] = {}
        if self.memory:
            # The shared sandbox configuration uses Docker-style sizes (4g).
            # Kubernetes requires case-sensitive quantities such as 4Gi.
            memory = str(self.memory).strip()
            docker_size = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmgt])(?:b)?", memory)
            if docker_size:
                memory = docker_size[1] + docker_size[2].upper() + "i"
            limits["memory"] = memory
        if self.cpus:
            limits["cpu"] = str(self.cpus)
        container: dict = {
            "name": container_name,
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
            # The workspace volume (below) -- kubectl cp streams the host
            # workdir in here before the command runs, and back out after.
            "volumeMounts": [{"name": "workspace", "mountPath": _POD_WORKDIR}],
        }
        if limits:
            container["resources"] = {"limits": limits, "requests": limits}
        return {
            "spec": {
                "automountServiceAccountToken": False,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": int(self.run_as_user),
                    "runAsGroup": int(self.run_as_user),
                    "fsGroup": int(self.run_as_user),
                },
                "containers": [container],
                # emptyDir: node-local scratch space for the copied-in
                # workspace: it needs no PVC/StorageClass and is deleted
                # with the pod, matching the "transient per-exec pod"
                # design (nothing to provision or clean up cluster-side).
                "volumes": [{"name": "workspace", "emptyDir": {}}],
            }
        }

    def _kubectl_prefix(self) -> list[str]:
        args = ["kubectl"]
        if self.context:
            args.extend(["--context", self.context])
        args.extend(["-n", self.namespace])
        args.extend(self.extra_kubectl_args)
        return args

    def _verify_kubectl(self) -> None:
        try:
            subprocess.run(
                ["kubectl", "version", "--client", "--output=yaml"],
                capture_output=True, timeout=5, check=True, env=scrub_env(),
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                "kubectl not available — required for kubernetes backend. "
                "Install kubectl or change [sandbox] backend in "
                "~/.maverick/config.toml."
            ) from e

    def _delete_pod(self, pod_name: str) -> None:
        """Best-effort force-delete a pod; never raises.

        ``kubectl run --rm`` deletes the pod only on a clean exit. Any other
        outcome -- a timeout, a kubectl crash, the process being killed, or
        an unexpected exception mid-run -- can orphan the pod and leak
        cluster resources, so we always issue an explicit delete on the way
        out. ``--ignore-not-found`` makes the common (already-removed-by-rm)
        case a no-op.
        """
        try:
            subprocess.run(
                [*self._kubectl_prefix(), "delete", "pod", pod_name,
                 "--ignore-not-found=true", "--grace-period=0", "--force"],
                capture_output=True, timeout=10, env=scrub_env(),
            )
        except Exception:
            pass

    def _start_and_wait(self, pod_name: str) -> ExecResult | None:
        """Start the pod detached (long-lived ``sleep``, not ``--rm -i``) and
        block until it's Ready. Returns None on success, else an ExecResult
        describing the failure.

        A detached start is required so there's a running container for
        `kubectl cp` to stream into *before* the real command runs --
        `kubectl run --rm -i` (the old one-shot approach) runs the command
        immediately and tears the pod down, leaving no window to copy
        anything in.
        """
        # Long enough to outlive the command timeout plus both cp legs; the
        # pod is force-deleted explicitly once exec() is done with it, so
        # this is just a backstop against an orphaned sleep outliving us.
        sleep_budget = int(self.timeout) + int(2 * self.transfer_timeout) + 60
        run_args = [
            *self._kubectl_prefix(),
            "run", pod_name,
            "--restart=Never", "--quiet",
            f"--image={self.image}",
            # Merge lists by container name; JSON merge replaces the generated
            # container and would discard its image and command arguments.
            "--override-type=strategic", "--command",
            "--overrides", json.dumps(self._pod_overrides(pod_name)),
            "--",
            "sleep", str(sleep_budget),
        ]
        try:
            proc = subprocess.run(
                run_args, capture_output=True, text=True,
                timeout=self.pod_ready_timeout, env=scrub_env(),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                stdout="", exit_code=1,
                stderr=f"kubectl run timed out starting pod {pod_name!r}",
            )
        if proc.returncode != 0:
            return ExecResult(
                stdout="", exit_code=1,
                stderr=f"kubectl run failed: {proc.stderr.strip()[-2000:]}",
            )

        wait_args = [
            *self._kubectl_prefix(), "wait", f"pod/{pod_name}",
            "--for=condition=Ready", f"--timeout={int(self.pod_ready_timeout)}s",
        ]
        try:
            proc = subprocess.run(
                wait_args, capture_output=True, text=True,
                timeout=self.pod_ready_timeout + 5, env=scrub_env(),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                stdout="", exit_code=1,
                stderr=f"pod {pod_name!r} did not become Ready in time",
            )
        if proc.returncode != 0:
            return ExecResult(
                stdout="", exit_code=1,
                stderr=f"pod {pod_name!r} did not become Ready: {proc.stderr.strip()[-2000:]}",
            )
        return None

    def _copy_in(self, pod_name: str) -> ExecResult | None:
        """Stream the host workdir into the pod's emptyDir. Returns None on
        success, else an ExecResult describing the failure.

        The trailing ``/.`` on the source is required: ``kubectl cp`` tars
        the source directory and untars it at the destination, so a bare
        ``<dir>`` would land as ``<dest>/<basename(dir)>`` instead of
        populating ``<dest>`` itself.
        """
        # A Windows drive colon is parsed by kubectl as POD:PATH. A relative
        # local operand plus an explicit subprocess cwd works on every host.
        src = "./."
        dest = f"{self.namespace}/{pod_name}:{_POD_WORKDIR}"
        args = [*self._kubectl_prefix(), "cp", src, dest]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                timeout=self.transfer_timeout, env=scrub_env(), cwd=self.workdir,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                stdout="", exit_code=1,
                stderr="kubectl cp (workspace -> pod) timed out",
            )
        if proc.returncode != 0:
            return ExecResult(
                stdout="", exit_code=1,
                stderr=f"kubectl cp (workspace -> pod) failed: {proc.stderr.strip()[-2000:]}",
            )
        return None

    def _run_command(self, pod_name: str, cmd: str, effective: float) -> ExecResult:
        wrapped = f"cd {shlex.quote(_POD_WORKDIR)} && {cmd}"
        args = [*self._kubectl_prefix(), "exec", pod_name, "--", "sh", "-c", wrapped]
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=effective,
                env=scrub_env(),
            )
            return ExecResult(
                stdout=result.stdout[-8000:],
                stderr=result.stderr[-2000:],
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            return ExecResult(
                stdout=stdout[-8000:],
                stderr=f"TIMEOUT after {effective}s",
                exit_code=124,
            )

    def _copy_out(self, pod_name: str) -> str:
        """Best-effort: stream files the command wrote/modified back to the
        host workdir. Returns "" on success, else a short note to append to
        the command's stderr. ``exec()`` retains its output and original
        failure code, but promotes a successful command to an I/O failure if
        its files could not be returned.
        """
        src = f"{self.namespace}/{pod_name}:{_POD_WORKDIR}/."
        args = [*self._kubectl_prefix(), "cp", src, "."]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                timeout=self.transfer_timeout, env=scrub_env(), cwd=self.workdir,
            )
        except subprocess.TimeoutExpired:
            return "; kubectl cp (pod -> workspace) timed out"
        if proc.returncode != 0:
            return f"; kubectl cp (pod -> workspace) failed: {proc.stderr.strip()[-500:]}"
        return ""

    def exec(self, cmd: str, timeout: float | None = None) -> ExecResult:
        effective = self.timeout if timeout is None else timeout
        pod_name = f"maverick-sb-{uuid.uuid4().hex[:12]}"

        if not self.allow_network:
            # A single transient `kubectl run` pod cannot enforce no-egress
            # on its own -- that needs a namespace-level NetworkPolicy the
            # operator applies. Reverify an exclusive deny policy before
            # each command; otherwise fail closed (never
            # silently run with network) and print the manifest to apply.
            # Previously this branch refused EVERY command, so the wizard's
            # default config could not run anything (platform audit W1-104).
            if not self._deny_all_policy_present():
                manifest = self.deny_all_networkpolicy_manifest()
                log.warning(
                    "kubernetes backend: allow_network=false but namespace %r "
                    "has no deny-all NetworkPolicy, so a transient pod would "
                    "have network. Apply this once, then retry:\n%s",
                    self.namespace, manifest,
                )
                return ExecResult(
                    stdout="",
                    stderr=(
                        "networking is disabled for the kubernetes backend "
                        f"(allow_network=false), but namespace {self.namespace!r} "
                        "has no verified exclusive deny-all NetworkPolicy. A transient pod cannot "
                        "enforce no-network on its own. Use a dedicated namespace without egress "
                        "allow rules and apply this policy "
                        "(kubectl apply -f -), then retry -- or set "
                        "allow_network=true to let pods use the network:\n"
                        + manifest
                    ),
                    exit_code=2,
                )

        # Always force-delete the pod on the way out, whichever step failed:
        # the pod is never started with --rm now (it has to survive past a
        # single command for kubectl cp to reach it), so nothing else cleans
        # it up.
        try:
            failure = self._start_and_wait(pod_name)
            if failure is not None:
                return failure
            failure = self._copy_in(pod_name)
            if failure is not None:
                return failure
            result = self._run_command(pod_name, cmd, effective)
            copy_out_note = self._copy_out(pod_name)
            if copy_out_note:
                result = ExecResult(
                    stdout=result.stdout,
                    stderr=result.stderr + copy_out_note,
                    # The command may have run successfully, but its output
                    # files were not delivered. Do not advertise overall success.
                    exit_code=result.exit_code or 74,
                )
            return result
        finally:
            self._delete_pod(pod_name)


__all__ = ["KubernetesBackend"]
