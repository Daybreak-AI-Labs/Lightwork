# Production support matrix

The first reviewed Lightwork profile is deliberately narrow. Its machine-readable
contract is [`release/production-support-matrix.json`](../../release/production-support-matrix.json),
and its authority boundary is
[`release/production-surface-manifest.json`](../../release/production-surface-manifest.json).
Source-tree presence, a passing unit test, or a packaged binary does not make a
combination production-supported.

## Status meanings

| Status | Meaning |
|---|---|
| `admitted` | Inside the reviewed source profile. The exact release still needs its revision-bound evidence packet. |
| `qualification_required` | Required interface or dependency, but the exact product/version is not supported until release evidence records it. |
| `evaluation_only` | Useful repository or CI coverage exists, but the combination is outside the first production profile. |
| `excluded` | The runtime or release gate refuses the combination. |

## First-profile boundary

| Dimension | Selection | Status | Important limit |
|---|---|---|---|
| OS and architecture | Linux container, `amd64` | admitted | Host-native execution and other architectures are not admitted. |
| Python | CPython 3.12 in an image pinned by OCI digest | admitted | A mutable Python tag is not a release identity. |
| Deployment | Kubernetes through Helm chart 0.1.2 | qualification required | Record exact Kubernetes and Helm versions plus the rendered-manifest digest. |
| Control plane | One replica | admitted | HPA and multiple control-plane replicas are refused. |
| Data plane | PostgreSQL 16 with forced RLS | qualification required | CI targets major 16; a release needs exact live TLS, migration, RLS, backup, restore, and failover evidence. |
| Isolation | gVisor RuntimeClass `gvisor` | qualification required | A RuntimeClass name does not prove the installed `runsc` build. |
| Model | vLLM-compatible HTTPS endpoint | qualification required | Exact build, model digest, endpoint, region, retention, credential, spend, and failover policy must be approved. |
| KMS | Vault Transit over HTTPS | qualification required | Exact offering/version, key identity, auth, HA, recovery, and rotation must be retained. |
| Identity | OIDC or explicitly trusted proxy SSO | qualification required | Provider/proxy, claims, revocation, logout, and break-glass behavior need live evidence. |
| Operator surfaces | Manifest-filtered CLI and dashboard REST API | admitted | New/unclassified commands and endpoints are denied until reviewed. |
| Browser | Dashboard browser client | qualification required | Exact browser journeys are release evidence, not an inference from source. |
| Arbitrary execution | Disabled | admitted | Container/VM/SSH/local execution backends are outside this profile. |

The profile excludes self-learning, code and weights DGM, plugins, MCP,
connectors, messaging channels, RAG, attachments, hosted model providers,
static operator tokens, local invites, SAML, SCIM, and multi-replica control
planes. Standalone Windows, macOS, and Linux binaries remain evaluation-only.

## Release qualification

Before calling a revision supported, bind the exact image digest and source SHA
to evidence that names every `qualification_required` product and version. The
packet must include the rendered Kubernetes manifest, server-side admission
result, live Postgres migration/RLS/recovery results, gVisor attestation and
containment result, model-policy approval, Vault recovery/rotation exercise,
identity revocation exercise, and browser journeys. Missing evidence leaves the
row unqualified; it is not silently covered by the broad component name.

Run the repository consistency check with:

```bash
python -I scripts/verify_production_support_matrix.py --root .
```

This verifies that the published matrix agrees with the production manifest,
chart, package metadata, and pinned repository test target. It does not turn
repository-local checks into deployed evidence.
