# Claim/evidence ledger

The canonical buyer-facing claim register is
`release/claim-evidence-ledger.json`. It separates facts that can be stated from
the checked-in repository from claims that still need an exact deployment,
external assessor, customer measurement, registry record, or approved release.
The ledger is **draft** and does not say that Lightwork is production-ready.

Every claim records an exact statement, status, buyer-use decision, evidence
files and SHA-256 digests, validation method and environment, limitations,
owner, and refresh date. Every digest row declares its mode. Reviewed source
text uses `utf8-lf-sha256`, so Git's Windows checkout line endings cannot
invalidate the same committed text on Linux. Retained proof output uses
`exact-byte-sha256` and remains byte-sensitive. Every buyer-facing document in
the reviewed set is bound by the applicable digest to the claims it discusses.
A content change, a new top-level enterprise document, a missing limitation, or
a changed evidence file makes verification fail until the ledger is regenerated
and reviewed.
Discovery is fail closed for all Markdown files in `docs/programs/`,
`docs/strategy/`, `docs/enterprise/legal/`, and the complete recursive
`docs/compliance/` tree, as well as the core buyer-facing set.

Strategy documents must classify checked-in observations as source-only, past
measurements and reviews as historical, and targets or future states as
proposals. They cannot carry aggregate test or generated-inventory totals,
because those values drift independently of long-lived pitch prose. An exact
candidate inventory must come from the release artifacts instead. No strategy
document can establish a release, deployment, customer result, ROI, valuation,
certification, compatibility, service level, or current market fact.

Compliance documents must retain one exact status class: source-controlled
draft, repository mapping/source only, unqualified deployment reference,
unexecuted template, non-authoritative sample register, non-independent
historical local snapshot, or the status boundary itself. The ledger records
separate claims for repository material, organization/deployment operation,
independent assessment, and the historical 2026 snapshots. Incident-response
source is likewise separated from staffed intake/on-call, exercised targets,
notification operation, complete retained forensics, penetration testing, and
independent assessment.

## Status rules

- `repository_verified` permits only the narrow repository fact written in the
  claim. It cannot be broadened into a live-deployment or outcome claim.
- `source_inventory_only` permits a deterministic source-count or surface fact
  only with its explicit non-production limitation.
- `deployment_evidence_required` and `external_evidence_required` are not
  buyer-claimable until revision-bound evidence exists.
- `historical_only` cannot qualify the current source or a release candidate.
- `draft` is incomplete or unapproved.
- `not_claimed` records an important claim that the project expressly does not
  make.

Only `repository_verified` and `source_inventory_only` rows may set
`buyer_claim_allowed` to true. The verifier refuses every contradictory pairing.

## Verification

Regenerate the deterministic draft after an intentional source or documentation
change, then review the complete diff:

```bash
python -I scripts/verify_claim_evidence_ledger.py --root . --write
python -I scripts/verify_claim_evidence_ledger.py --root .
```

Release-candidate verification additionally requires a current approved ledger,
two distinct approvers, an approval date, and an external approval reference:

```bash
python -I scripts/verify_claim_evidence_ledger.py --root . --release-candidate
```

The checked-in ledger intentionally fails that release-candidate command while
the repository remains unreleased and its live and external evidence gates are
open. Approval must be performed through production change control; do not edit
the generated hashes or approval fields as a substitute for review.
