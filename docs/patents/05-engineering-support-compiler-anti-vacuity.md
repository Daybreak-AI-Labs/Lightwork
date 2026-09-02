# Engineering Support Memo 05 — Executable Anti-Vacuity Checks for Compiled Governance Obligations

> **Not legal advice.** This is an engineering-side technical support memo for
> patent counsel. It does not assess patentability, novelty, prior art, claim
> scope, inventorship, freedom to operate, or filing dates or deadlines.

## Purpose and boundary

This memo describes the current executable check that detects whether a selected
set of declarative regulatory obligations compiles to a live runtime gate. It
does not establish that a regulation is complete, current, correctly
interpreted, or satisfied in a particular deployment.

## Technical problem

A policy library can look complete on paper while doing nothing at runtime.
Typical failure modes include a rule that references a removed tool, a policy
overlay that is no longer evaluated at the action chokepoint, a risk floor that
matches no classified action, or a capability attenuation that does not remove
the stated authority. A release check should distinguish a clean result from a
check that inspected nothing.

## Current technical architecture

### 1. Compile declarative obligations into existing enforcement shapes

Each obligation carries a regulation/article reference, an effect, and a scope
expressed as action names, a risk floor, or denied capability tools.
`compile_obligations(keys)` combines selected obligations using the existing
governance `Policy` and `Capability` structures:

```text
selected obligation libraries
        |
        +--> DENY / REQUIRE_HUMAN --> governance Policy --> governance.evaluate
        |
        +--> ATTENUATE -----------> deny-tool set -----> Capability.attenuate
        |
        +--> obligation identifiers -------------------> audit decision tags
```

The compiler retains the selected obligations alongside the compiled policy so
that a verdict can carry the obligation identifiers that were enforced or
checked. Policy composition is strictest-wins: a deny is not weakened by a
less-restrictive rule, and capability attenuation uses the existing
least-privilege operation.

### 2. Verify that every selected obligation has an observable effect

`verify(keys)` performs an executable anti-vacuity check against the current
classified-tool universe and the actual compiled output. For action-scoped
DENY and REQUIRE_HUMAN obligations it checks that each action is classified and
that `governance.evaluate` returns a result at least as restrictive as the
obligation. For risk floors it requires at least one classified tool at that
floor. For ATTENUATE obligations it creates a capability probe and confirms
that each in-scope tool no longer permits.

The command-line gate exposes this result to automation:

- a selected, clean set returns success;
- a stale tool name, dead policy overlay, dead attenuation, unknown effect, or
  empty scope produces problems; and
- no selected/active profiles returns status `2`, rather than a pass.

This is deliberately an executable property of the checked library, the
current tool classifier, and the live chokepoint contract. It is not a general
proof of all agent behavior or a statement of regulatory coverage.

### 3. Preserve decision provenance separately from the CI result

`audit_tags` and `checked_tags` derive bounded obligation identifiers from the
specific compiled profile keys that governed a decision. Passing the CI check
does not prove an individual production action ran, and an audit receipt for an
executed/failed action still depends on the correlated terminal result evidence.

## Engineering variations to preserve for review

- A deployment may attach an equivalent compiler to another policy engine,
  provided the verification probe exercises the same enforcement point that
  processes real actions.
- The classified-tool universe can be versioned or supplied by a service
  registry; the anti-vacuity check should bind its report to that exact
  universe/version so a renamed tool cannot silently pass.
- The check may use generated action/risk cases, model checking, or a separate
  sandbox runtime in addition to the current deterministic probe. Those
  additions should continue to fail when the check inspects no applicable
  obligations.
- The emitted obligation provenance can be represented in audit rows, signed
  receipts, or a policy-verdict table, but a storage representation must not be
  mistaken for an enforcement result.

## Focused test and implementation evidence

| Evidence | What it exercises |
| --- | --- |
| `packages/maverick-core/maverick/regulatory_compiler.py:1-30` | Design boundary: compile into the existing chokepoint and capability attenuation; describe anti-vacuity conditions. |
| `packages/maverick-core/maverick/regulatory_compiler.py:187-224, 377-489` | Compiled-obligation representation, policy/attenuation construction, classified-tool lookup, and executable verification. |
| `packages/maverick-core/maverick/regulatory_compiler.py:329-374` | Decision-specific obligation tags and checked-obligation evidence. |
| `packages/maverick-core/maverick/compliance_profiles.py` | Profile selection/activation path used by the compiler. |
| `packages/maverick-core/tests/test_regulatory_compiler.py:31-40` | Each shipped profile compiles to an individually checked gate. |
| `packages/maverick-core/tests/test_regulatory_compiler.py:210-260` | Fault-injected stale tool, dead overlay, dead attenuation, no-scope, and no-active-profile cases. |
| `packages/maverick-core/tests/test_compliance_profiles.py` | Profile compilation and rejection behavior around profile selection. |

## Known limitations and non-claims

- The check covers the selected library, its current classified-tool universe,
  and the tested governance/capability paths. It does not prove enforcement by
  an external gateway, a third-party agent, or a provider API.
- It does not prove that every legal requirement is represented, that a cited
  source remains current, or that a tool-level rule captures conduct that is
  not structurally visible at a tool call.
- Unknown keys passed directly to the low-level compiler are normalized out;
  deployment configuration should use the stricter profile-validation path and
  retain the exact selected profile set in release evidence.
- An audit tag is provenance metadata, not an independent proof that a policy
  decision or downstream effect occurred.
- The focused tests are deterministic in-process checks. They are not a
  customer-tenant, regulator, or production-gateway acceptance exercise.
