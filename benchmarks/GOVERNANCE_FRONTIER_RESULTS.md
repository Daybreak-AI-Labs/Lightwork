# Governance frontier benchmark — measured results

> **MEASURED:** 72/72 unsafe instances prevented; 48/48 benign tasks completed; FPR 0.0%.
> This is an offline deterministic control-path benchmark, not an LLM
> task-intelligence or competitive model benchmark.
> “Prevented” means the called control returned a denial before the
> harness would mark its simulated terminal effect admitted; no live
> payment, deletion, or external side effect is executed.

## Run identity

- Suite: `governance-frontier-v1`
- Started: `2026-08-24T19:40:45.316870+00:00`
- Git commit: `b010f82664c315a4846c0886c88af8ade8b06b0f`
- Working tree dirty during run: `false`
- Definitions: 40 (24 unsafe, 16 benign) × seeds [17, 29, 43]
- Arms: 120 governed and 120 disabled-baseline instances
- Model in loop: `false`; network used: `false`
- Network claim basis: no model/network API is called; config/overlay, deployment profile, tenant/client, and injected or KMS-wrapped audit-key inputs are cleared; the egress adapter only evaluates URL and provider policy
- Inherited config/overlay, profile/trust, tenant/client binding, consent override, and audit-key injection inputs: cleared; related process caches reset
- Consent mode override: cleared; secure defaults explicitly enabled
- Input digest: `a3a7b2730d9130204222196cab16de9d91391247d749e739cfb82a1a5b4d9595`
- Manifest signature: `ed25519` (self-signed run integrity)
- Public evidence: [signed measured manifest](./results/governance-frontier-v1/measured-manifest.json), [trusted publisher key](./results/governance-frontier-v1/trusted-publisher.pub), and [standalone verifier](./results/governance-frontier-v1/verify_manifest.py)
- Signed manifest file SHA-256 (UTF-8/LF): `7522db2718efaba6cfed53d381a1f7f8e849e786437d581fffdc808420f3e4bd`
- Trusted-key file SHA-256 (UTF-8/LF): `d032290a6f135fbf1bdf32745e89c735dd9fd0c4a24f757ea2edda05909c4f19`
- Ed25519 public-key fingerprint (SHA-256 of the 32 raw key bytes): `04309209f2d8c73cca7358ad89814f893779db2b9e5812fca52dc76dcb815387`
- Publication verification requires the separately downloaded trusted key; the manifest-embedded key is not trusted by itself

## Scorecard

| Metric | Measured result |
|---|---:|
| Unsafe-action prevention | 72/72 (100.0%) |
| Harness decision-ledger coverage, unsafe | 72/72 (100.0%) |
| Native audit event observed, supported unsafe controls | 27/27 (100.0%) |
| Unsafe instances covered by a natively emitting called primitive | 27/72 (37.5%) |
| Benign task completion, governed | 48/48 (100.0%) |
| Benign task completion, baseline | 48/48 (100.0%) |
| False-positive rate | 0/48 (0.0%) |
| Harness evidence checks | 120/120 (100.0%) |
| Harness decision-ledger chain | verified |
| Verdict invariant across pinned seeds | true |

The disabled baseline admitted every scripted unsafe effect (100.0%); that arm is a per-control disabled/bypassed counterfactual, not a competing product or equivalent full-task runtime.

The harness appends a normalized row after every governed decision. That
proves the benchmark ledger, not native product audit wiring. Native-event
rates use only events observed during the called control primitive and only
controls that emit there (`action_gate`, `egress`). Pure decision APIs are
reported outside that denominator.

## Per-family results

| Family | Prevention | Completion | FPR | Native audit observation |
|---|---:|---:|---:|---:|
| `agent_trust` | 100.0% | 100.0% | 0.0% | n/a |
| `budget_control` | 100.0% | 100.0% | 0.0% | n/a |
| `capability_attenuation` | 100.0% | 100.0% | 0.0% | n/a |
| `destructive_action` | 100.0% | 100.0% | 0.0% | 100.0% |
| `egress_boundary` | 100.0% | 100.0% | 0.0% | 100.0% |
| `evidence_integrity` | 100.0% | 100.0% | 0.0% | n/a |
| `financial_actuation` | 100.0% | 100.0% | 0.0% | 100.0% |
| `secret_output` | 100.0% | 100.0% | 0.0% | n/a |

## Measured overhead

| Path | Samples | Median | p95 |
|---|---:|---:|---:|
| Governed control call | 120 | 164.566 µs | 3867.883 µs |
| Disabled baseline call | 120 | 17.279 µs | 109.430 µs |
| Paired governance overhead | 120 | 110.793 µs | 3866.132 µs |
| Harness signed decision append | 120 | 1008.446 µs | 1272.373 µs |

Times are local-machine observations, not an SLA. Each seed changes
execution order to expose state/order dependence; it is not model
sampling. Verdicts, not timings, are required to be seed-invariant.

## Evidence and custody

- Signed audit events: 190
- Audit-chain SHA-256: `2639462f0f979d3f053c7ee774dd2795b7067d6c4f77a1d21afb5d3ad3ad2aea`
- Audit chain verified in-run against the run public key: `true`
- Manifest schema: `maverick-bench-repro/1`
- Key custody: ephemeral co-located benchmark key; self-signed run integrity only, not off-host custody, publisher identity, or resistance to a same-user actor.
- Key source asserted by run: `ephemeral_co_located`; off-host active: `false`

The result manifest is self-signed with the ephemeral run key. The
verification command refuses the manifest's self-disclosed key and
requires a separate trusted-key file. Repository review/distribution
must establish that file's trust; the signature alone does not establish
publisher identity. It also does **not** prove production off-host key
custody. A same-user actor with access to a co-located private key could
re-sign altered evidence.

## What this does not show

- No LLM is in the loop, so task completion means the benign scripted
  terminal action remained admissible—not natural-language agent success.
- `effect_executed` is the harness's simulated terminal allow/block
  outcome. The suite does not execute live payments, deletions, or
  external side effects.
- Medium-risk benign click/type/fill mutations are the false-positive
  controls; observation-only actions do not make up those action families.
- The baseline is a per-control disabled/bypassed Lightwork
  counterfactual, not another framework or an end-to-end task runner.
- The 40 cases are fixed policy-boundary examples, not statistical
  coverage of every enterprise workflow.
- Native audit observation covers only the action and egress primitives
  that emit an event at the API called here. Harness-authored rows are
  reported separately and do not prove other product wiring.
- Timing is sensitive to this host, filesystem, and background load.
- Raw audit NDJSON is not a tracked artifact in v1. The signed
  manifest records its digest, event count, and in-run verification
  result, but a reviewer cannot replay that underlying chain without
  rerunning the benchmark.
- The separate public-key file still needs a trusted distribution and
  review channel to establish publisher identity.

## Verify the published evidence without Lightwork source

Download the signed manifest, trusted publisher key, and standalone
verifier linked under **Run identity**, keep them in one directory,
install `cryptography`, and run:

```bash
python verify_manifest.py measured-manifest.json trusted-publisher.pub
```

That check independently verifies the Ed25519 signature and prints the
downloaded-file SHA-256 values plus the raw-key fingerprint. It does
**not** recompute the manifest's signed control-source digests.
Source-binding verification requires a licensed Lightwork source
snapshot at the measured commit and the full tracked-artifact command
below.

## Defensible claim

> Across 40 deterministic control-path definitions, three pinned
> order seeds, and matched controls-disabled baselines, Lightwork
> prevented 100.0% of scripted unsafe
> effects while completing 100.0%
> of benign lookalikes with a 0.0%
> false-positive rate under Lightwork's risk-aware secure defaults.
> For called primitives that natively emit audit events, 100.0% of supported
> unsafe instances produced one. The separate benchmark decision ledger
> verified in-run. This evaluates fixed control paths, not native audit
> coverage for every primitive or LLM task intelligence.

## Reproduce

```bash
python benchmarks/eval_governance_frontier.py
python benchmarks/eval_governance_frontier.py --verify-manifest benchmarks/results/governance-frontier-v1/measured-manifest.json --trusted-pubkey-file benchmarks/results/governance-frontier-v1/trusted-publisher.pub
python benchmarks/eval_governance_frontier.py --verify-tracked-artifacts
python -m pytest -q benchmarks/test_eval_governance_frontier.py benchmarks/test_governance_metrics.py
```

Publication validation checks the external trusted-key file, current
signed harness, control-implementation, and config digests; a clean
measured commit; commit ancestry when history preserves it (or the
exact signed control-source snapshot after a shallow checkout, squash,
or rebase); and deterministic report rendering. Strict UTF-8 source
content is normalized to LF before hashing so identical Windows and
Linux Git checkouts verify the same signed snapshot.
