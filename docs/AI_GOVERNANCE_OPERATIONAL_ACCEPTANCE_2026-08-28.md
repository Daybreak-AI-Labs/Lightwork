# AI Governance Operational Acceptance Record

Date: 2026-08-28

Branch: `claude/aigov-gap-buildout`

Remote baseline: `ffa309a0990ecd216ee995a24bd53ece6bee16ac`

Publication state: the work in this record is local, uncommitted and unpushed.
Main was not touched.

## Result

All seven requested workstreams now have repository implementations and bounded
local acceptance paths. They are not all live-accepted. Work requiring provider
credentials, consenting external tenants/vendors/contributors, an external
gateway, production signing custody or independent privacy review remains
blocked rather than being represented as passed.

Status meanings:

- **Passed local**: code ran against a bounded local or synthetic estate.
- **Ready, blocked externally**: the fail-closed live harness exists, but an
  external prerequisite is absent.
- **Not accepted live**: no claim of provider, customer, vendor, contributor or
  production acceptance is made.

## Seven-workstream matrix

| # | Requested workstream | Built and locally exercised | Result and remaining boundary |
|---:|---|---|---|
| 1 | Network-egress and browser/OAuth sensors | Consent-gated deployable network-egress, browser-extension and OAuth JSONL agents; fixed-origin signed client; normalized private SQLite spool; local consenting estate; tenant/sensor-scoped idempotency; rotation, old-key rejection, revocation/restore, outage/retry, recovery, privacy and bounded concurrency | **Passed local.** Coverage remains explicitly incomplete. No external estate, enterprise completeness, DPIA approval or production-scale result. SaaS, directory and catalog deployable agents remain future work. |
| 2 | External-gateway runtime policy pilot | Restart-safe pilot client plus real local FastAPI gateway routes, auth, approvals and execution ledger; retry, response-loss recovery, outage ambiguity, current-policy re-evaluation, no-change, rollback and compensation | **Passed local; external gateway not accepted.** Controlled connector/policy doubles were used. No customer/external gateway or external credential was used. |
| 3 | Provider contracts and C2PA | Closed consent-gated harness for Hugging Face, Entra, one exact-origin-allowlisted ServiceNow sandbox write/replay and OpenAI Costs; environment/hostname labels cannot authorize mutation; `c2patool` 0.27.16 installed; real development-fixture sign/verify; digest-pinned external-signer boundary; production custody design | **Provider harness blocked externally:** all four credentials unavailable, no I/O or mutation attempted. **C2PA development interop passed.** The development credential is not production trusted; HSM/KMS/certificate/timestamp/relying-party acceptance remains. |
| 4 | Identity scheduler and control enforcement | Leased scheduler; digest-only normalized change fanout to dynamic risk/alerts; pseudonymous federation through an independently injected enrolled subject-binding resolver; identity-to-capability mapping; fresh conditional-access decisions; CAS/idempotent Purview writeback/compensation; default-off configuration | **Passed local; live identity operation not accepted.** No Entra/Okta/Purview test tenant or provider-specific mutation acceptance. |
| 5 | Benchmark coordinator and trust-root ceremony | Restartable aggregate-only coordinator; exact consent/inclusion/privacy-review evidence gates; bounded 64 MiB default durable state with copy-on-write rollback; k>=5 signed report; replay recovery; rotation/revocation ceremony | **Passed local with five synthetic contributors.** Anonymity and differential privacy are recorded as `reviewed_not_claimed`; privacy budget is not applicable. No live cross-tenant contributors or independent privacy acceptance. |
| 6 | Vendor portal pilot | Consent-digest gate bound to the named synthetic consent fixture; two synthetic vendors and representative documents; closed document contracts/drift rejection; exact submission retry and same-process import replay; invitation revocation; tenant isolation; pending human review | **Passed local synthetic.** Crash/restart recovery and consenting external vendor/customer or ecosystem workflow acceptance were not run. |
| 7 | Platform acceptance | Fixed five-suite runner for fresh-tenant, cross-tenant adversarial, recovery, concurrency/scale and audit export; runtime pilot included in recovery | **Passed local automated:** 140 passed, 0 skipped. The previously skipped at-rest field test now exercises tenant-bound encrypt/decrypt and rejects cross-tenant decryption. No moderated human usability, deployment-scale/on-call/DR or customer acceptance. |

## Evidence artifacts

| Artifact | File SHA-256 | What it proves | What it does not prove |
|---|---|---|---|
| `proof/discovery-sensor-test-estate-2026-08-28.json` | `f73dfadfbbf7e0e6e663abc65932a1dbe64473f252376c24a522b3d8ea4a3b61` | Local consent gate, three sensor kinds, tenant-scoped idempotency and key/recovery/privacy/scale exercise | External deployment or complete discovery |
| `proof/governance-platform-acceptance-2026-08-28.json` | `e9a3d84c8b20b4b2ec4600a3327cc9a5af83b6b9411c499dfdb36e1b1c55c0a6` | Five local automated suites passed; runtime recovery path included | Customer/external gateway or human usability acceptance |
| `proof/provider-contract-readiness-2026-08-28.json` | `5dd5bc4449de603ae90ed2fa336ede6303bf7f57ebfcef808b41b7d60cd216af` | All four requested contracts are configured; the ServiceNow origin is exactly allowlisted; missing credentials fail closed with no provider I/O | Any live provider contract acceptance or independent approval of the configured origin |
| `proof/c2patool-install-2026-08-28.json` | `35f31a3e5c0152dfaf8cfd8ae45a18f176f1338b3d770e040470671cff318290` | Installed binary/version/archive and executable digests | Software supply-chain attestation beyond the recorded checks |
| `proof/c2patool-interop-2026-08-28.json` | `00d1733ff879114cdc219c6cff30fc6141d86c40776d97fc41e4e5704f3700fb` | Real-binary development sign/verify and pinned development-root verification | Production key custody, trusted production certificate or relying-party acceptance |
| `proof/benchmark-coordinator-pilot-2026-08-28.json` | `43fd9f7080b4ea322e7615080457e90a8b3d03e3f1dd676a1d81b771e751fd9e` | Synthetic reviewed cohort, restart/replay and trust-root rotation | Anonymity, differential privacy, privacy-budget or live participation |
| `proof/vendor-portal-pilot-2026-08-28.json` | `1c8ff7b139ac07a8e5fd185a982f9d7e2533d395d50f72f07bbec0e8ffe2cc90` | Unsigned local checksum over the named synthetic consent fixture, document contract, retry/replay, revocation and isolation paths | Crash recovery, signed tamper-evidence or external vendor/customer acceptance |

Evidence files are bounded machine records. They contain digests and status
metadata, not provider response bodies, credentials, private keys, raw sensor
events or vendor document contents. `/proof/*.json` is pinned to LF in
`.gitattributes`, so the listed byte hashes are stable across Windows and POSIX
checkouts.

The vendor pilot's `consent_record_sha256` is the byte SHA-256 of
`scripts/acceptance/fixtures/vendor-portal-synthetic-consent.json`:
`248e65333b2a27a40c401ce294e9f783c3bbeb618290b81dbb2bb91300cc531d`.
That fixture explicitly records `live_vendor_consent_obtained: false`; it is a
local synthetic authorization record, not consent from an external vendor.

## Consolidated verification

- Changed-surface pytest run: **351 passed** in 79.79 seconds across installer,
  core, dashboard, sensor, identity, provider, runtime, C2PA, benchmark, vendor,
  platform-runner and cross-tenant tests.
- Fixed five-suite platform acceptance rerun: **140 passed, 0 skipped**. Embedded
  report digest:
  `98544f50471411e9e42cd5d114894037d432532633b8f4680d7caf8470e7e58e`.
- Ruff: all **40** changed or untracked Python files passed.
- `compileall`: core, dashboard, installer and acceptance-script trees passed.
- `git diff --check`: passed; Windows line-ending notices are informational.
- Import provenance resolved to this checkout:
  `packages/maverick-core/maverick/__init__.py`.

The entire repository test matrix was not run. These focused results must not be
expanded into a whole-repository or production-readiness claim.

## Adversarial-review remediations

A separate read-only review found and the working tree now addresses these
acceptance-integrity defects:

- runtime before/after state uses typed canonical finite JSON, so falsey values
  remain distinct and mapping order cannot manufacture a difference;
- runtime status and commit accept the full bounded URL-safe execution-ID
  alphabet generated by the gateway, including leading `_` and `-`, while the
  stricter idempotency-key contract remains unchanged;
- an execution whose approval was created immediately before a crash can bind
  the existing unique approval on replay instead of creating an orphan/duplicate;
- sensor duplicate admission is scoped to authenticated tenant, sensor and event
  digest rather than a global event digest;
- identity conditional-access and Purview controls cannot be selected/rendered
  by the installer without federation;
- benchmark durable mutations are copy-on-write and separately bounded by state
  bytes, including rollback on capacity or save failure;
- vendor evidence is labelled an unsigned checksum and same-process replay,
  rather than tamper-evident or crash-safe;
- platform evidence binds the source state under test without hashing its own
  generated proof; and
- the C2PA external signer requires and revalidates a pinned executable digest.

## Current provider blocker record

The working-tree readiness configuration is
`scripts/acceptance/fixtures/provider-contract-readiness.json`. It uses secret
references only. On this host, `HF_TOKEN`, `ENTRA_DISCOVERY_TOKEN`,
`SERVICENOW_TOKEN` and `OPENAI_ADMIN_KEY` are unavailable. The readiness command
therefore exits non-zero with four `credential_unavailable` results and records:

- `live_request_attempted: false` for every provider;
- `mutation_attempted: false` for ServiceNow;
- `servicenow_write_origin_allowlisted: true` for the exact configured-origin
  match, without claiming independent estate approval;
- `production_acceptance_claimed: false`; and
- `configured_contracts_live_accepted: false`.

This is the correct fail-closed result, not a test failure to bypass.

## C2PA custody boundary

The installed binary is
`C:\Users\Cstep\AppData\Local\Programs\c2patool\bin\c2patool.exe`, version
0.27.16. The development interop run used a disposable fixture credential and
a pinned development trust root. Lightwork's external-signer adapter does not
persist a private key or certificate.

Production requirements are specified in
`docs/security/c2pa-signing-custody.md`: non-exportable HSM/KMS keys, approved
certificate chain and timestamp service, four-eyes enrollment/rotation/
revocation, fail-closed egress, auditable signer identity, outage/recovery and
relying-party acceptance. The external signer requires an approved executable
SHA-256 and revalidates file identity/digest immediately before invocation;
owner/ACL/parent-directory/application-control checks remain deployment
preflight. Design and local enforcement are not production operation.

## Required next operations

1. Replace placeholder estate identifiers and provision the four provider
   secret references only for explicitly consenting test tenants. Run the live
   provider command without `--readiness-only`; review the bounded mutation plan
   and independently review the exact ServiceNow `write_origin_allowlist` before
   acknowledging the write.
2. Supply a consenting external gateway and repeat the runtime pilot there.
3. Deploy the sensor agents in an approved external estate and record measured
   coverage, DPIA/privacy approval and production-volume results.
4. Provide Entra or Okta and Purview test tenants and approve provider-specific
   federation, conditional-access and writeback semantics.
5. Provision production C2PA custody and obtain relying-party acceptance.
6. Enroll consenting external vendors and benchmark contributors only after the
   listed human and privacy reviews.
7. Repeat platform acceptance in deployment-like infrastructure and add
   moderated fresh-tenant usability, on-call and disaster-recovery exercises.

## Publication boundary

The prior remote branch tip remains
`ffa309a0990ecd216ee995a24bd53ece6bee16ac`. This operational batch is not on
GitHub until a new commit and push are explicitly authorized. No pull request,
merge or main-branch change was made.
