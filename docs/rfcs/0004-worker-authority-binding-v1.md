# RFC 0004: Worker authority binding v1

- Status: implemented for the supplier evidence workflow
- Contract: `lightwork.worker-authority.v1`
- Scope: request composition, durable action intent, execution, provider readback, customer outcome and tenant-local denial audit

## Decision

Every provider effect carries one immutable `ServiceContext` from authenticated request composition. The context binds the tenant, principal, resource owner, dashboard role and its current permission-set digest, workspace selection generation, named ticket and document credential references, credential authority, and tenant budget-authority digest. It contains references and hashes, never credential values.

The supplier workflow persists that context in the exact action intent before approval. The approval digest also covers the action name, unredacted proposed payload, source and assessment revisions, vendor revision, document and ticket credential versions, target scope, and expiry. Execution compares the current context and every precondition with the approved values before it makes a durable execution claim, then repeats policy, precondition, approval and approver checks immediately before the adapter call.

The workspace selection store assigns a monotonic generation to each principal. Switching away and back therefore produces a different workspace authority even when the final tenant string is unchanged. Legacy selection rows are admitted at generation zero and move to the versioned format on their next real change. Repeating the same selection is a no-op and does not invalidate work.

Approved operations created before role, budget and workspace binding existed do not inherit those fields. They fail the current-context comparison and require a newly prepared and approved intent before an effect.

## Authority trace

| Boundary | Bound authority | Enforcement |
|---|---|---|
| HTTP request | Authenticated principal, active tenant, current dashboard role, workspace generation | Authentication middleware pins the request tenant; composition requires `operate` and a named principal |
| Service context | Tenant, actor, resource owner, credential authority and references, role, workspace and budget digests | Callers must provide `ServiceContext`; request/body fields cannot replace it |
| Durable intent | Exact context, Jira payload, source/assessment/vendor/credential versions, scope and expiry | A human approval signs the canonical operation digest |
| Worker execution | Current context and current preconditions | Revalidated before and after durable claim and immediately before the effect |
| Provider adapter | Tenant, named credential reference and expected credential version | Adapter resolves the sealed enrollment again and rejects owner, tenant or version drift |
| Observation/outcome | Exact intent, authoritative readback and separate human acceptance | Technical verification cannot stand in for business acceptance |
| Export/audit | Tenant-scoped work record; redacted denial record | Cross-scope reads return no content; denial audit stores only a resource digest |

## Roles

- A customer reviewer operates the in-app workflow under the current dashboard role. Changing that role invalidates the queued intent even if the new role also has `operate` permission.
- The workflow worker holds only the service context and named connector references required for the one approved Jira action. It cannot use ambient request-body authority or raw credentials.
- A research worker is governed by the separate attenuating `Capability` and self-modification reference monitor. Candidate evaluation requires a non-host-visible sandbox with egress denied, root disabled, bounded processes, memory and output, and authenticated evaluator evidence. Evaluator, authority, tenant, secret, audit, sandbox and promotion paths remain outside the editable surface. Production credentials are absent from the sandbox environment and the candidate cannot grant itself capability.

Dashboard roles and research capabilities are separate control planes. A researcher does not become a customer operator merely by producing a candidate, and an operator approval does not authorize candidate promotion.

## Optional Shield behavior

Shield adds input/output inspection when installed. This contract does not import Shield or delegate tenant, role, approval, credential, budget or action authority to it. A supported kernel workflow therefore remains usable when Shield is absent, while the ordinary authentication, authorization, exact-approval and adapter gates continue to run.

## Failure behavior

- A tenant or owner mismatch returns the same not-found result as a missing record and writes a tenant-local `capability_denied` event containing the attempted tenant, principal, action, reason and SHA-256 of the resource identifier.
- Role, workspace, budget, credential, source, assessment, vendor or expiry drift stops before the provider effect. The user must reload and review a newly prepared exact action.
- Any failure after the durable execution claim is ambiguous. The workflow moves to read-only reconciliation and never treats the failure as permission to create again.
- Reusing another intent's approval digest or an expired approval is rejected before the effect.

## Supported deployment boundary

This implementation preserves the supplier journey's current single persistent application replica contract. It does not claim distributed queue ordering or exactly-once delivery across arbitrary providers. Provider idempotency remains false for Jira creation, so durable intent plus authoritative readback and reconciliation are required.
