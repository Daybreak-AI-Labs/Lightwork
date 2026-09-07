# RFC 0005: staged local restore reference validation

Status: implemented locally; installed and external recovery qualification is separate.

## Problem and behavior

A correctly signed archive with matching payload hashes can still contain a
vendor-onboarding case whose vendor, assessment, or renewal parent is absent.
Previously, restoring those bytes could produce a successful recovery receipt.
The restore path now checks supported application references in its private
staging directory before recovering an earlier transaction or publishing any
application file. `force=True` does not bypass this check.

The recovery center performs the same preflight before creating a review/safety
snapshot and again before consuming a reviewed live restore. A failed drill
does not issue a successful drill report. Hash-only inspection remains useful
and remains explicitly distinct from application-reference validation.

## Supported scope

The versioned validator is `lightwork.restore-reference-validation.v1`:

- Local JSON records in `vendor_onboarding`, `vendor_management`, and
  `ai_vendor_risk`, with their existing v1 schemas and store/file identities.
- A case's `vendor_id` must exist in its workspace and match its owner and
  `vendor_name` (using the vendor register's trimmed lowercase identity).
- A non-null case `assessment.id` must exist in that workspace and carry the
  matching tenant digest and `vendor` name (using the assessment authority's
  whitespace-normalized casefold identity).
- A non-null `renewal_of` must resolve to another case with the same tenant,
  owner, and vendor reference.
- Whole-installation recovery resolves each `tenants/<encoded-tenant>` subtree
  independently. Another tenant's matching record ID cannot satisfy a reference.

The validator reads at most 16 MiB per record, 64 MiB across supported records,
and 25,000 records. Exceeding these bounds requires a reviewed recovery plan.
Duplicate-key, malformed, non-finite, unknown-schema and mismatched-identity
JSON is rejected with an affected file and field/reference diagnostic. Record
payload values are not copied into diagnostics.

## Compatibility boundaries

Full replacement is required whenever an additive restore would involve any
supported governed-store directory in the archive or existing live tree. An
overlay could otherwise leave retained live records pointing at replaced
targets. This version fails closed instead of claiming to validate that merged
state. Existing isolated in-app and installation restores use full replacement.

Encrypted envelopes in supported stores are rejected before publication with a
key-custody/decryption diagnostic. The validator never imports keys from a
snapshot or tries ambient decryption, which could select the wrong tenant key
or mutate shared key state. A custody test for other encrypted formats is not a
waiver of this boundary.

This intentionally narrows restore availability for profiles that persist
sealed payloads in these covered stores. A follow-up tenant/key-scoped validator
is required to regain supported restore for that profile; refusal is not proof
of successful encrypted recovery. Other encrypted drafts and database columns
remain outside this check. The current local governed-record backend writes
plain JSON; shared encrypted Postgres records are already excluded from local
workspace recovery.

These checks do not validate every field in a domain schema, audit outbox or
signature authority, historical assessment versions, complete renewal lineage,
general evidence-graph links, all cross-record application invariants, decryption
of unrelated stores, or provider state. Retained evidence is not reclassified
as fresh. Local restoration does not reverse any external effect. Recovery
reports retain these scope limits and do not claim complete business acceptance.

The application must still be quiescent under the existing isolated recovery
procedure. The low-level restore lock serializes restores; it does not stop
every ordinary application writer. This change adds no concurrency guarantee.

## Validation

`test_restore_application_validation.py` builds genuinely signed, hash-verified
archives and checks rejection before `_recover_transactions` or
`_apply_transaction` can run. It covers dangling references, binding failures,
valid replacement, additive refusal, sealed-format refusal and cross-tenant
whole-home recovery. `test_workspace_recovery.py` covers the in-app drill and
review boundaries. Encrypted fixtures test refusal of recognized envelopes;
they are not a decryption or real-key custody acceptance result.

An additional test creates a complete accepted case and its renewal through
`VendorOnboardingService`, archives the actual governed-store records, restores
into a fresh home, and reopens both cases through a fresh service instance.
Provider adapters and authorization/audit sinks are controlled test doubles;
the test proves local schema compatibility and retained references, not live
provider approval. Restore and readback do not repeat the synthetic ticket create.
