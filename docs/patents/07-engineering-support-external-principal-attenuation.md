# Engineering Support Memo 07 — Capability Attenuation over External Principals

> **Not legal advice.** This is an engineering-side technical support memo for
> patent counsel. It does not assess patentability, novelty, prior art, claim
> scope, inventorship, freedom to operate, or filing dates or deadlines.

## Purpose and boundary

This memo describes the current least-privilege and lifecycle controls applied
to enrolled external-agent principals. It is an engineering description of
registry parsing, capability narrowing, identity checks, revocation, and
focused tests. It does not claim that every external runtime has integrated the
control plane or that a deployment has validated every identity provider.

## Technical problem

An agent running outside the local runtime can have its own tools, identity
mechanism, and budget, yet still need controlled access to a governed workload.
If a locally minted capability becomes wider than the external principal's
registry ceiling, or if revocation races with an administrative update, an
external agent can retain authority it should no longer have. The control needs
to bind an external identity to a principal, calculate a grant that only
narrows, and reject requests when trust state is absent, expired, malformed, or
revoked.

## Current technical architecture

### 1. Registry-backed external principal

A `TrustedAgent` entry represents a named external agent with a pinned Ed25519
key where configured, direction, tool allow/deny sets, maximum risk, spend and
time ceilings, memory domains, per-surface credentials, optional OIDC/JWKS
configuration, and lifecycle fields. The entry derives a `Capability` bound to
`agent:<id>`. When an allowlist is present, omitted coordination tools are
converted into explicit denials so an external caller does not inherit the
general in-process coordination floor.

The trust decision path checks registry enrollment, inbound/outbound direction,
lifecycle state, requested tool/risk, and capability ceiling. Identity
verification is separately bound to the registry entry and expected envelope
schema/origin. When the trust plane is engaged, an unregistered or revoked
external principal is denied rather than treated as a compatible legacy peer.

### 2. Monotonic attenuation operation

The general `Capability` type supports `intersect` and `attenuate`. The intended
least-authority invariant is:

```text
derived permission => parent permission AND requested-child permission
derived risk       <= each applicable risk ceiling
derived deny set   >= each applicable deny set
```

`attenuate_agent` applies a managed overlay under thread and cross-process
locking. It unions new denied tools and only accepts lower risk, spend, or wall
time ceilings. Empty, unchanged, malformed, or widening requests are rejected
before persistence; a post-write reload checks that the narrowing survived.
Revocation is a distinct managed state, so existing registry and federation
decision paths can fail closed on a revoked entry.

### 3. External-runtime boundary

`external_agents` enrolls external runtime metadata alongside a trust entry and
uses the trust plane for screening. It is designed so that platform provenance
(for example, the runtime type) does not itself grant authority: the same
registry, capability, budget, shield, and approval controls determine whether
an external action is admitted. Per-surface bearer material remains separate by
surface to limit credential reuse across REST, A2A, gRPC, and MCP entry points.

```text
external runtime request
        |
        v
per-surface authentication / identity binding
        |
        v
TrustedAgent registry + lifecycle + direction
        |
        v
Capability intersect/attenuate + budget/risk/tool checks
        |
        +--> allow under narrowed principal capability
        |
        +--> deny / revoke / expiry / approval path
```

## Engineering variations to preserve for review

- External identity may use a pinned asymmetric key, an OIDC/JWKS binding,
  a transport-authenticated federation identity, or another explicitly
  configured credential scheme. Identity source and capability authority should
  stay separate so a token alone is not mistaken for an unrestricted grant.
- Attenuation may add tool denials, lower risk, lower spend, lower wall time,
  narrow file/host scope, or combine several parent/child grants. Any form must
  retain the no-widening invariant.
- A managed overlay may be stored in a local file, a transactional policy store,
  or a distributed control plane, provided concurrent updates cannot discard a
  revoke or silently widen an existing ceiling.
- A deployment may record admit/deny events in its audit system or attach a
  receipt. Such records are evidence of the implementation's stated decision;
  they do not replace verification of the remote runtime's actual behavior.

## Focused test and implementation evidence

| Evidence | What it exercises |
| --- | --- |
| `docs/security/capability-attenuation.md` | Least-privilege invariant, glob-domain assumption, and documented sound-but-incomplete glob narrowing behavior. |
| `packages/maverick-core/maverick/capability.py:46-175` | Capability model plus intersection/attenuation operations. |
| `packages/maverick-core/maverick/agent_trust.py:188-312` | External-principal fields, lifecycle state, external coordination floor, and capability construction. |
| `packages/maverick-core/maverick/agent_trust.py:704-871, 876-914, 987-1179` | Atomic attenuation, revocation, admission decisions, and identity verification. |
| `packages/maverick-core/maverick/external_agents.py:1-45, 104-205` | External-agent enrollment/screening boundary, platform provenance, credential-surface separation, and tenant-scoped metadata sidecar. |
| `packages/maverick-core/tests/test_capability_monotonicity.py` | Randomized meet, no-widening, chain-transitivity, idempotence, and known escalation-attempt checks. |
| `packages/maverick-core/tests/test_capability_glob_smt.py` | Optional SMT-based check of glob/path/host narrowing in the NUL-free domain. |
| `packages/maverick-core/tests/test_agent_trust.py:181-229, 247-389, 496-616` | Tool/risk ceilings, pinned-key identity, federation admission, lifecycle revocation/expiry, and origin mismatch behavior. |
| `packages/maverick-core/tests/test_agent_trust_admin.py:55-58, 139-223` | Managed revocation and concurrent administrative update behavior. |
| `packages/maverick-core/tests/test_external_agents.py:314-335` | Budget cutoff and revoked-agent denial behavior on the external-agent path. |

## Known limitations and non-claims

- The capability invariant constrains the local grant calculation. It does not
  prove a remote runtime will faithfully enforce a decision after it leaves the
  governed boundary.
- The glob implementation narrows by intersecting pattern strings rather than
  full glob languages. This can over-restrict to deny-all; it is documented as
  sound but incomplete, not a precision guarantee.
- Defaults and enforcement mode matter: the trust-plane compatibility path can
  be disengaged in deployments that have not enabled it. A production rollout
  needs an explicit enforce-mode and provider acceptance review.
- OIDC/JWKS fields are configuration seams, not evidence that a particular
  Entra, Okta, or other identity-provider tenant has been integrated or tested.
- Focused local tests do not establish multi-tenant scale, incident response,
  credential custody, external-vendor cooperation, or production federation
  interoperability.
