# Internal research container primitive

`ResearchContainerRunner` in `research.py` is a trusted-controller primitive for
bounded Python candidates in a local Linux Docker engine. It is intentionally
not registered in the ordinary sandbox factory, dashboard, installer or public
research submission route. It adds no user-operable capability yet.

The caller supplies a bounded mapping of relative file names to immutable bytes,
an entrypoint within that mapping, a reviewed image already present locally, and
an explicit `Budget`. The runner resolves and pins the local image ID; it never
pulls an image. Docker Desktop's Linux engine on Windows and local Linux Docker
are potential targets requiring separate real qualification. No host-process
fallback or remote engine is accepted.

The primitive uses:

- Explicit Linux UID/GID 65532, independent of the controller's Windows/Linux UID.
- A generated read-only candidate snapshot and read-only container root.
- A bounded private tmpfs for scratch; no writable host mount.
- Separate private `/dev/shm`, explicitly capped to the same byte limit as
  `/scratch` and checked in pre-start metadata; both consume container memory.
- Network none, dropped capabilities and no-new-privileges.
- Finite memory/swap, CPU, process, output and wall-time limits.
- A cleared candidate environment with a small explicit runtime allowlist.
- Container inspection before startup, input hashes before and after execution,
  and removal/absence verification for the entire container afterward.

Only Python's standard environment is assumed: the reviewed Linux image must
provide `/usr/bin/env` and `/usr/local/bin/python3`. Python runs with `-I -B`;
there is no dependency download/bootstrap phase. Candidate code may arrange its
own imports inside the sandbox, but cannot supply extra Docker flags, mounts,
engine endpoints or environment settings. The controller host and Docker daemon
remain trusted. Candidate stdout is untrusted diagnostic output.

The unit tests are deliberately separate from real containment acceptance:

```powershell
.venv/Scripts/python.exe -m pytest -q packages/maverick-core/tests/test_research_container.py -k 'not real_'
```

For real tests, first inspect the local image inventory and select a reviewed
existing Linux image. Start the local runtime as necessary for authorized
reversible testing. Set `LIGHTWORK_RESEARCH_TEST_IMAGE` to that observed image
reference and run:

```powershell
$env:LIGHTWORK_RUN_RESEARCH_CONTAINER_TESTS = '1'
.venv/Scripts/python.exe -m pytest -q packages/maverick-core/tests/test_research_container.py -k 'real_'
```

An opted-in run fails if its runtime or selected image is unavailable. These
tests start disposable local containers and use only synthetic credential and
evaluator canaries. They do not call model providers or public network services.
The test network probe uses a documentation-only address and verifies Linux
`ENETUNREACH`, loopback-only interfaces and the absence of any non-loopback route.
It does not claim qualification of a permitted egress proxy.

If cleanup cannot be verified, the exception identifies the exact container name
and retained temporary snapshot. The caller must resolve that uncertainty before
claiming completion. Do not blindly resubmit the same candidate or delete the
snapshot while a container may still be using it.

## Integration still required

This primitive is not completion of F03-A3 or the research platform. It does not
authenticate a researcher, materialize a repository under that principal's
authority, authorize a submission, derive attenuated capabilities, integrate an
app job queue, persist an observation, or decide scientific/evaluator success.

It deliberately does not implement `exec_authenticated_tests` and does not mark
itself ready for authenticated scoring. A separate trusted oracle/controller
must evaluate candidate behavior outside the candidate process, bind its results
to nonce/subject/arm/policy, and integrate with the existing evidence contract.
Parsing candidate stdout/JUnit or signing candidate-provided counts is not that
implementation. Any later user-facing integration needs the corresponding
configuration and installer steps and independent acceptance through that path.

`/scratch` is not claimed to be the only writable filesystem location: Docker
provides private IPC shared memory and special runtime mounts. The positive test
checks nonroot execution, configured bounds, a scratch write and a correct
synthetic answer. Separate negative tests check candidate/evaluator immutability.
Actual filesystem and resource enforcement remain unqualified until the real
tests run on the target engine. Docker's documented IPC and shared-memory flags
are described in the [container run reference](https://docs.docker.com/reference/cli/docker/container/run/).
