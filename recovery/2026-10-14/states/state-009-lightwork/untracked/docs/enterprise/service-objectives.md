# Service-objective approval

Lightwork does not derive customer commitments from unit tests, fixture proof
packs, repository size, or an aspirational Helm configuration. The exact
service objectives for a production release live in
`release/service-objectives.json` and are bound to one production profile and
source cohort.

The current contract is deliberately `draft`: no availability, latency,
recovery-time, recovery-point, durability, support-response, or audit-anchor
target has been approved. A release candidate must remain blocked while any
target or measurement source is unset.

## Required objective set

The contract requires all of the following; entries cannot be silently omitted
or renamed:

| Objective | Required measurement |
| --- | --- |
| Availability | Valid production API requests completed without a service-attributable 5xx or timeout, over all valid requests. |
| API p95 latency | Server duration for an explicitly enumerated synchronous operation set. Long-running model/flow work needs a separate task SLI and is not hidden in this number. |
| Recovery time | Time from a declared disaster to a working supported profile, measured in an isolated restore drill. |
| Recovery point | Age of committed customer state unavailable after that verified restore. |
| Data durability | Authenticated, durably acknowledged records recoverable after restore; a successful backup job is not a durability measurement. |
| Severity-one support response | Time from an accepted severity-one incident to qualified human acknowledgement. |
| Audit-anchor lag | Age of the newest independently retained authenticated audit checkpoint. A local chain tip is not independent evidence. |

Every entry needs a numeric target, a precise window, an SLI definition, a
deployment measurement source, an owner, and bounded exclusions. Keep customer
dependencies and planned-maintenance exclusions explicit; do not use an
unbounded exclusion to manufacture compliance.

## Approval and expiry

At least two distinct authenticated principals must approve the exact JSON:
one with role `service-owner` and one with role `site-reliability`. Approval
lasts no more than 366 days, must not be future-dated, and names the sole
authority that can review an error-budget exception. Exhausting an error budget
freezes risky releases and requires an incident review.

Validate the draft schema during ordinary development:

```bash
python -I scripts/verify_service_objectives.py --root .
```

The release path uses the stricter form, which refuses the current draft:

```bash
python -I scripts/verify_service_objectives.py --root . --release-candidate
```

Before changing `status` to `approved`, retain the exact dashboard queries or
immutable reports used as each `measurement_source`, a production-profile load
and soak result, restore evidence with measured RTO/RPO, the support rota and
escalation proof, and an external audit-checkpoint observation. Approval of a
number without its measurement evidence is not completion of the gate.
