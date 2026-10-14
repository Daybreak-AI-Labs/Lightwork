# Public form routing in shared deployments

Anonymous `POST /form/<token>` requests have no authenticated principal from
which tenant-by-user middleware can select a namespace. When tenant-by-user
isolation is enabled, Lightwork therefore requires governed Postgres and an
encrypted deployment-global routing registry. The registry stores the full
SHA-256 token digest, tenant id, trigger name, lifecycle state, and bounded
submission claims. It never stores the raw bearer token.

Form tokens provisioned in this mode must be 24-256 URL-safe characters. A
legacy shared form without a route remains inaccessible until an operator
re-saves the event trigger with a qualifying token through the governed API.
There is no scan-and-guess migration and no node-local fallback.

## Rotation and deletion contract

Token rotation is an ordered, fail-closed saga:

1. claim a deployment-global operation lease and change active old routes to
   `suspended` in the same CAS, which stops new admissions;
2. reserve the new digest as `pending` under that lease;
3. save the tenant-local event trigger with a version fence;
4. activate the new digest;
5. tombstone every other digest for that tenant and trigger, then release the
   operation lease.

The lease is keyed by a digest of the tenant and trigger, and contains only a
digest of the exact management intent, never its raw token. Prepare, tenant
save, activation, retirement, restoration, and deletion each re-check and
renew the lease in a backend-time CAS. A different operation cannot enter the
save-to-activation window. An identical retry after a lost lease response
resumes the same generation; expiry or release advances the generation, so a
paused prior holder cannot resume after takeover. No replica-local lock or
clock participates in this fence.

A definitively failed tenant-trigger save restores the old suspended route only
after re-reading and proving that the old trigger version and token are still
authoritative. If a save may have committed but its response was lost, the old
route stays suspended. This temporary outage is intentional: automatically
restoring it could route through a token whose trigger was already replaced.
Repeating the same management request reconciles the pending/suspended state and
converges. Revoked tokens remain tombstoned and cannot be assigned to a different
tenant later.

Registry schema version one remains readable. The next successful registry CAS
rewrites it as the bounded version-two document with operation-lease state; no
route, digest tombstone, epoch, or submission claim is discarded during that
cutover.

Deletion similarly suspends admissions before deleting the version-fenced
tenant trigger, then tombstones its routes. A retry after a lost delete response
uses the route history to finish the tombstone without recreating the trigger.

## Submission authorization point

After resolving the digest, the public endpoint enters that tenant context and
re-reads the exact active `form` event trigger. It then creates a durable claim
against the exact active route epoch. This claim is the authorization point:

- revocation that commits first makes admission fail, so nothing is appended;
- admission that commits first remains an explicitly authorized in-flight
  append, even if revocation commits immediately afterward.

The tenant form store records the claim id with the submission. Retrying the
same `Idempotency-Key` and payload returns the original sequence; reusing that
key with a different payload returns `409`. Version-one shared form history is
read unchanged and migrates to the claim-aware version-two schema inside the
next append CAS, preserving its sequence and rows. Admitted claims expire after
15 minutes and committed retry receipts after one hour, using the governed
backend clock; every later admission prunes expired claims.

Unknown, pending, suspended, revoked, malformed, ambiguous, inactive-tenant, or
unavailable authority state fails closed. Unknown and inactive links return the
same not-found response so the public endpoint does not provide a token-state
oracle.
