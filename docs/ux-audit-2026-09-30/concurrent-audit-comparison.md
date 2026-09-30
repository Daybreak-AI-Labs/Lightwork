# Concurrent audit comparison

The five leading claims in documentation-only PR2530 were compared with this
draft at `d822db99ee48aab4fdded47fc799486e8e5ea44f`. The source report is pinned
to `ee5cad418d11aedd50d6cba46f0b653b539d0204`, based on the older `6daa3165`
product. Its other 735 entries have not been adopted as this PR's scope.
Neither PR2529 nor PR2530 was modified or combined into this branch.

These are current code findings, not verification of the other author's private
runtime artifacts or severity counts. No real credential was read, no external
provider was called, and no production instance was exercised. The findings
below are separate from the original 195-row register; the implementation owner
has reported them for coordination rather than silently declaring them covered.

| Claim | Current source evidence | Disposition |
| --- | --- | --- |
| UX-001: feedback crosses workspaces | `goal_feedback_store._path()` still uses the deployment overlay's parent; `latest_for_goal()` matches only goal number. The API's current-goal authorization does not scope that side-store read. | Credible current gap. Feedback counts and personal erasure need the same explicit workspace/owner lineage; ambiguous legacy records must not be assigned by guess. |
| UX-002: annotations cross workspaces | `ux_store.shared()` still caches one `UxStore` whose constructor resolves its path once; annotations are addressed by goal number inside that object. | Credible current gap. Resolve the active workspace on every access, preserving locks and private ownership checks. |
| UX-003: flow expressions resolve server secrets | `flow.runner._resolve_secret()` calls deployment `secret_provider.get_secret(str(name))`; `_funcs(allow_secrets=True)` exposes it and `_render_params()` enables it for action parameters. The provider falls back to arbitrary process environment names. Action/connector grants do not restrict that independent lookup. | Credible current gap; independently reviewed by the flow worker. Requires an explicit scoped-credential contract and migration behavior. Existing deployment model configuration must remain unchanged. |
| UX-004: vendor text renders HTML | `vendor_detail.html` passes vendor name/category to `hero`; `_hero.html` forces `safe` on title/lede/copy. Default dashboard CSP still admits inline handlers. | Credible current gap. Escape user-origin strings at the shared boundary; retain trusted template formatting only through explicitly trusted markup. |
| UX-005: removed tenant identifiers can be reused | `tenant.registry.delete_tenant()` removes the roster entry without a removal tombstone. `api.create_tenant()` only excludes currently listed IDs from its generated slug. `paths.bind_tenant_namespace()` permits the same canonical owner to reuse its existing namespace claim. Existing roles/data therefore remain addressable after same-ID provisioning. | Credible current gap. The project-workspace erasure tombstones added in this PR are a different store and do not close tenant reprovisioning. Removal/reprovisioning needs a durable lifecycle boundary. |

These findings do not justify weakening authentication, changing deployment
credentials, bypassing record preservation, or assuming administrator content
access. Any coordinated follow-up must use synthetic regression coverage and
the approved audit-only administrative privacy policy.

The user subsequently approved fixing these five findings in the same draft PR.
[Current separate implementation register](additional-security-fixes.json) records
the repairs and independent review follow-ups. This does not adopt the other735
claims or imply completed full-suite qualification.
