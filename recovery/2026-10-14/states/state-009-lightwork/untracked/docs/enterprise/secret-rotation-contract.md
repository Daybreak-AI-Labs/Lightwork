# Production secret rotation contract

This contract covers the exact credentials admitted by
`enterprise-single-control-plane-v1`. It defines repository-enforced rollout
semantics; it is not evidence that a customer's Kubernetes, Postgres, Vault,
identity provider, model gateway, or Prometheus deployment completed a live
rotation.

The canonical machine-readable contract is
`release/secret-rotation-contract.json`. Verify it with:

```bash
python -I scripts/verify_secret_rotation_contract.py --root .
```

## Deployment truth

- Production receives credential values only from pre-provisioned Kubernetes
  Secrets. Helm values contain names and a public rotation generation, never
  credential values or secret-derived hashes.
- The init container takes one bounded, private, immutable snapshot. A running
  pod deliberately does not hot-reload projected files.
- `secret.rotationGeneration` is required and is copied into pod-template
  metadata. Changing it guarantees a new pod template even if the Secret name
  is reused.
- The admitted control plane is one replica with `Recreate`, so a rotation has
  a planned availability interruption. Zero-downtime pod overlap is not
  claimed. Safety means no credential/custody downgrade, not uninterrupted
  service.
- Old provider authority and the old Secret generation remain usable for
  rollback until the new pod is ready and new plus historical operations have
  been verified. Revocation is last.

## Exact inventory

| Credential | Activation | Required overlap / consequence |
|---|---|---|
| Postgres runtime DSN | pod recreate | Separate runtime principal or provider-supported dual-password window; verify TLS, exact role, RLS, and schema. |
| Vault auth token | pod recreate | Keep two scoped tokens/auth roles valid through readiness and wrap/unwrap checks. |
| Vault wrapping key ID | provider change then pod recreate | Keep old decrypt authority through rewrap, backup retention, and restore validation. Prefer provider-managed key versions where the key ID remains stable. |
| vLLM gateway token | pod recreate | Gateway accepts old and new until an approved model call and spend reconciliation pass. |
| Wrapped audit signing key | pod recreate | Retain all historical public keys and the rollback generation; verify a new signed append and the historical chain. |
| OIDC client secret | pod recreate | IdP accepts old and new until a complete login callback proves exact identity attribution. |
| OIDC session secret | pod recreate | No dual-verification implementation is claimed; rotation deliberately forces reauthentication and old sessions must fail. |
| Metrics bearer pair | pod recreate | The application accepts current and previous during the bounded overlap; previous alone is never authoritative. |

## Required sequence

1. Preflight the exact credential scope and retain non-secret fingerprints and
   provider identities without printing values.
2. Create the new provider-side authority while the old authority remains
   valid. For the metrics bearer, populate the previous slot first.
3. Retain the old Kubernetes Secret generation and a tested rollback command.
4. Stop admission, drain in-flight work, and reconcile ambiguous effects.
5. Publish the new Secret data and change `secret.rotationGeneration`.
6. Apply the Helm release. Expect the single-replica `Recreate` interruption;
   wait for the new pod's startup gates and readiness.
7. Exercise the new credential and the required historical operation: database
   posture, historical decrypt, audit-chain verification, login attribution,
   model usage/cost, and metrics scrape as applicable.
8. Observe normal traffic and reconcile leases, outboxes, and provider spend.
9. Revoke old provider authority only after the evidence owner accepts the
   retained results.
10. Remove overlap material, change the generation again, recreate, and repeat
    readiness and negative-old-credential checks.

If any stage fails before revocation, restore the retained old Secret and old
generation. If revocation has begun, use the provider-specific recovery or
forward-fix path; never fall back to environment/config credentials, a local
KMS key, a raw audit private key, or a shared operator token.

## Evidence still required

Before launch, retain a target-cluster drill for every row: timestamps,
generation names, value-free fingerprints, drain results, readiness, new-use
and historical-use proof, rollback result, negative test after revocation,
operator/evidence-owner approval, and any measured interruption. Repository
tests establish the contract and fail-closed wiring only.
