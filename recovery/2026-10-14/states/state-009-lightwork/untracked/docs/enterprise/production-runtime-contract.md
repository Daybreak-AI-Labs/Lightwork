# First production runtime contract

The first production surface is a single Lightwork control-plane pod. It is
not an arbitrary-code execution worker: `MAVERICK_SANDBOX_BACKEND=disabled` is
mandatory, command requests return exit 126 without starting a runtime, and
plugins, MCP servers, shell hooks, remote workers, code DGM, and consequential
tools are outside the admitted manifest.

The canonical machine-readable contract is
`release/production-runtime-contract.json`. Its bytes are generated from
`maverick.production_runtime_contract.contract_document`; startup rejects a
syntactically valid but hand-edited copy.

## Filesystem and mounts

The container image root is read-only. The only application data mounts are:

| Path | Mode | Capacity | Purpose |
| --- | --- | --- | --- |
| `/tmp` | read/write `emptyDir` | 1 GiB | bounded ephemeral scratch |
| `/home/maverick/.maverick` | read/write PVC | 10 GiB request | durable tenant-scoped control-plane state |
| `/run/secrets/lightwork` | read-only memory-backed snapshot | 2 MiB | exact regular-file secret custody |

`/workspace`, `/app`, `/etc/lightwork`, and installed Python packages stay on
the read-only image. Production read tools are descriptor-contained below
`/workspace`; startup and sandbox construction both refuse redirecting that
workspace into the durable state or secret roots. That prevents a model-facing
read tool from crossing into another tenant's on-disk namespace even though the
single control-plane process must own the shared durable volume.

The live mount check admits only those three application mounts, the image
root, standard `/dev`, `/proc`, and `/sys` kernel trees, and Kubernetes' exact
`/etc/hostname`, `/etc/hosts`, and `/etc/resolv.conf` files. Any other injected
mount is a startup refusal even when it is read-only.

The application container never mounts the Kubernetes Secret projection. The
init container projects only these keys before making its immutable snapshot:

- `MAVERICK_PG_DSN`
- `MAVERICK_KMS_KEY_ID`
- `VAULT_TOKEN`
- `VLLM_API_KEY`
- `MAVERICK_AUDIT_SIGNING_KEY_WRAPPED`
- the two OIDC secrets when OIDC is selected

The raw `MAVERICK_AUDIT_SIGNING_KEY`, cloud credentials, Docker configuration,
Kubernetes credentials, SSH material, static dashboard tokens, and unapproved
provider keys are not projected. Production also rejects arbitrary Helm `env`
entries, disables service-account-token projection and Kubernetes service-link
environment injection, and checks that host/runtime sockets and credential
directories are absent.

The approved model itself is served by the policy-pinned external vLLM endpoint;
model weights are not mounted into the control-plane pod. The model policy,
surface manifest, runtime contract, and installed package code are read-only
image inputs.

## Resource ceilings

The supported Helm profile fixes the pod at 2 CPU, 2 GiB memory, 1 GiB
ephemeral storage, a 1 GiB `/tmp`, and a 10 GiB durable-volume request. Before
the dashboard starts, the PID-1 launcher lowers both soft and hard POSIX limits
to 256 processes, 4,096 open files, 8 GiB per file, and zero-byte core dumps.

Every goal is additionally bounded to 1,800 wall seconds, 500 tool calls, a
32,000-byte single tool result before it enters model context, depth 3, and 32
total child agents. Mutable config may tighten those limits but cannot raise
them. The process accepts at most eight concurrent goals and two per principal.

The image uses exec-form PID-1 startup and `STOPSIGNAL SIGTERM`; the chart gives
60 seconds before the container runtime's forced cgroup termination. Because
the admitted profile starts no untrusted subprocess tree, there is no
model-generated descendant to escape that boundary.

## Evidence boundary

Repository tests prove manifest/code agreement, mount parsing and default-deny
paths, secret projection contents, POSIX-limit installation, goal clamps, Helm
render/refusal behavior, disabled execution, PID-1 image wiring, and the
read-only workspace separation.

They do not prove that a specific cluster installed the intended gVisor
RuntimeClass, enforced cgroups/ephemeral-storage eviction and PVC capacity,
applied NetworkPolicy at the CNI, or killed every cgroup process after grace.
Production acceptance still requires a live cluster qualification and shutdown
drill against the exact release image, RuntimeClass, CNI, CSI/storage class,
and rendered manifests.
