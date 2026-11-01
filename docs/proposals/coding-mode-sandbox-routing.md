# Proposal: route coding-mode git through `sandbox.exec()`

**Status:** Partly implemented. `Agent._reset_workdir` and `Agent._git_apply`
now run git through `sandbox.exec()` (option 1 below: the patch is written to a
temporary file inside the workdir) and fall back to host `subprocess` only when
no sandbox is configured. The git calls in `coding_mode.py` still run on the
host. Routing those is architecturally significant (it changes behavior across
every sandbox backend), so it awaits a maintainer decision.

**Interim guard.** Until this is decided, every host-side git call in
that path refuses a workspace whose git settings define a filter (so a planted
`smudge`/`clean` command cannot run on the host; Git LFS repositories are
refused too) and runs with repository hooks off, using
`git_safety.git_metadata_may_execute_filters` / `git_env_without_user_config`.
That is a static scan of what git reads, not isolation: routing the calls
through `sandbox.exec()` as proposed below is still the real fix.

## The problem

Parts of coding mode's apply/validate/verify path run `git` on the **host** via
raw `subprocess.run`, while the rest of the agent's shell goes through
`sandbox.exec()`. This works against
[kernel rule 4](../../CONTRIBUTING.md#house-rules) ("all shell goes through
`sandbox.exec()`") and, more importantly, **breaks coding mode for non-local
sandboxes**:

- `run_failing_tests` correctly routes through the sandbox: `coding_mode.py`
  `r = sandbox.exec(c)`.
- `agent.py` `Agent._reset_workdir` (`git reset --hard HEAD` / `git clean -fd`)
  and `Agent._git_apply` (`git apply <tmpfile>`) route through `sandbox.exec()`
  when a sandbox is configured.
- These still run on the host:
  - `coding_mode.py` `validate_patch` — `git apply --check -`.
  - `coding_mode.py` `evaluate_candidate` — `git worktree add`, `git apply -`
    and `git worktree remove` for each candidate.
- Both take the workdir from the sandbox (`getattr(sandbox, "workdir", ".")`).

For `SSHBackend` the workdir is a **remote** path (`~/maverick-workspace`,
`sandbox/ssh.py`), so `git -C <that-path> apply` on the host operates on a path
that **doesn't exist locally**, a different tree than where the agent's edits
and tests actually ran. Docker/podman work only by the bind-mount accident
(`docker.py` mounts the host workdir to `/workspace`).

(An earlier fix addressed the same class of bug for `KubernetesBackend`:
its `workdir` used to be an in-pod path with nothing ever copied into it —
`exec()` ran commands against an empty directory, so this proposal's host-side
`git -C <workdir>` calls were doubly wrong there. `workdir` is now the HOST
directory `kubectl cp` streams into/out of the pod on every `exec()`, the same
contract as every other backend, so it no longer needs special-casing here —
though routing git through `sandbox.exec()` remains the more correct fix in
general, per this proposal.)

Net: while these calls stay on the host, coding mode can silently validate or
evaluate the wrong tree under SSH/firecracker
(the Firecracker scaffold has its own, still-open "exec path does not yet
mount the workspace" gap — see `docs/FEATURES.md`).

## Proposed fix

Route every git operation in the coding-mode apply/validate/verify path through
`self.ctx.sandbox.exec(...)` instead of `subprocess.run`, so they execute in the
same filesystem context as the edits and tests.

- `git reset --hard HEAD`, `git diff`, `git apply --check`, `git apply` →
  `sandbox.exec("git -C <workdir> …")` (the sandbox already cd's/wraps per
  backend).
- `git apply -` reads the patch from **stdin**, which `sandbox.exec()` may not
  support uniformly across backends. Two options:
  1. Write the patch to a temp file **inside the sandbox** (`sandbox.exec` a
     heredoc / `write_file` tool) and `git apply <tmpfile>`; or
  2. Add an optional `stdin=` parameter to the `sandbox.Backend.exec`
     contract and implement it per backend (local: `subprocess` stdin; ssh:
     pipe over the ssh channel; docker: `docker exec -i`).
  Option 1 is lower-risk (no contract change); Option 2 is cleaner long-term.

## Risks

- **Touches every sandbox backend's contract/behavior.** A regression here breaks
  coding mode (the SWE-bench path) for *all* users, not just remote ones.
- The `git apply -` stdin handling is the crux; getting it wrong silently drops
  patches (exactly the failure class we're fixing).
- The default (`local`) sandbox is unaffected — local coding mode works
  today. The bug affects SSH/firecracker users.

## Verification plan (before merge)

1. Unit: a fake sandbox recording `exec()` calls — assert the FINAL handler
   issues git via `sandbox.exec`, never `subprocess`.
2. Integration (local backend): the existing
   `test_integration_swe_smoke::test_full_pipeline_produces_valid_patch` must
   still pass.
3. SSH: exercise against a configured remote host — confirm the patch is
   rendered from the remote tree.

## Recommendation

Implement it as a dedicated change: it is a real correctness bug for remote
sandboxes and brings the remaining calls under kernel rule 4, but the default
local sandbox is unaffected and the change is wide.
