# Production assurance on Kubernetes (single control plane)

This reference pins one Lightwork dashboard/control-plane pod to an immutable
OCI digest, mounts explicit non-secret assurance policy, uses Postgres for the
encrypted world model, governed records, and effect ledger, and keeps residual
file-backed authorities on one ReadWriteOnce PVC. It is a production
*assurance* starting point: it does not assert that the Lightwork control plane
is highly available, enterprise-ready, or compliant. Those conclusions require
deployment-specific evidence and human review.

## Files and boundary

- `values.yaml` is a Helm overlay with no secret values.
- `config.toml` enables strict authority ingress, capability and tenant policy,
  signed audit, the evidence graph/factory, Agent Trust Auditor, CRA operations,
  the Model Risk & AI Assurance Officer with its exact-payload DGM promotion
  gate, and certified-only environment-hunt connectors.
- `RUNBOOK.md` covers deploy gates, backup/restore drills, and a
  signature-required evidence campaign workflow.

The supported topology is exactly one dashboard/serve replica with one RWO PVC.
Postgres is required here because strict authority also makes governed writes
use a shared, encrypted effect ledger. It additionally centralizes the world
model, canonical audit stream and signer trust, and integrated assurance
records, but the runtime authority report still identifies local audit
private-key custody, channel-federation delivery/replay, in-process agent-bus
delivery/acknowledgement, and work-discovery enrollment/lease/revocation
authority. This specific overlay also leaves network job dispatch local because
it declares no Redis queue, leaves harness learning local because it does not
select `self_harness.store = "world"`, and cannot promote holdout query budgets
without a deployment-stable `holdout_authority_id`. The Helm chart and runtime
authority admission therefore still refuse a multi-replica or enterprise
claim. Do not scale this Deployment until that report is clean and live
qualification passes.

Application-level Postgres sealing does not make the topology highly available.
A future multi-replica reference must finish the residual shared-authority
migrations, inject one shared KMS/key authority, pass live service probes, and
complete witnessed failover and restore drills before increasing replicas.

## Preconditions

Before rendering the chart, an operator must provide:

1. A registry digest `sha256:<64 lowercase hex>` for the reviewed image, plus
   the source commit it was built from. A digest pins bytes but does not prove
   publisher identity. Verify the image signature/attestation and SBOM against
   your own trust anchor and registry policy before installation.
2. A Kubernetes Secret named `maverick-production-secrets`, delivered by an
   external secret manager/CSI integration, with at least:
   `MAVERICK_DASHBOARD_TOKEN`, `MAVERICK_ENCRYPTION_KEY`,
   `MAVERICK_ENCRYPTION_KEY_DIGEST` (the canonical SHA-256 digest of the
   decoded encryption-key bytes),
   `MAVERICK_PG_DSN`, `MAVERICK_AUDIT_SIGNING_KEY` (or the supported
   wrapped-key variable), `MAVERICK_AUDIT_TRUST_ROOT_SHA256` (the non-secret
   epoch-one pin may instead be supplied by an approved ConfigMap), and a
   configured model-provider credential or self-hosted provider setting. Never
   put credential or private-key values in `values.yaml` or `config.toml`; the
   public root pin is non-secret but still change-controlled.
3. A managed Postgres database provisioned with the owner/runtime role split,
   forced RLS, schema grants, TLS, backups, and restore testing documented in
   [`deploy/postgres/README.md`](../../postgres/README.md). The long-lived
   runtime role must be non-superuser and must not hold `BYPASSRLS`.
4. A StorageClass whose snapshot/restore behavior has been tested, an external
   backup location with retention controls, and a TLS ingress/gateway. Ingress
   and NetworkPolicy are intentionally left for the cluster owner because a
   generic egress policy can accidentally block Postgres, KMS, IdP,
   secret-manager, or model endpoints. Cluster/storage encryption is still
   required for files the application does not seal, including attachments and
   the current audit day.
5. Named operators for deployment, security review, backup restore, and
   evidence approval, plus accountable AI-system owners and human reviewers for
   model-risk acceptance, EU AI Act applicability, and DGM promotion. Lightwork
   records evidence and reviews; it does not replace those accountable humans
   or perform certification or legal classification.

Check the secret's key names without printing values:

```bash
kubectl -n lightwork get secret maverick-production-secrets \
  -o go-template='{{range $k,$v := .data}}{{printf "%s\\n" $k}}{{end}}'
```

## Render and deploy

Set environment-specific values without committing them:

```bash
export MAVERICK_IMAGE_DIGEST='sha256:<64-lowercase-hex>'
export MAVERICK_GIT_COMMIT='<reviewed-git-commit>'
export MAVERICK_DEPLOYMENT_ID='<environment-and-release-id>'

helm lint deploy/helm/maverick \
  -f deploy/reference-architectures/production-assurance/values.yaml \
  --set-string image.digest="$MAVERICK_IMAGE_DIGEST" \
  --set-string env.MAVERICK_GIT_COMMIT="$MAVERICK_GIT_COMMIT" \
  --set-string env.MAVERICK_DEPLOYMENT_ID="$MAVERICK_DEPLOYMENT_ID" \
  --set-file config.content=deploy/reference-architectures/production-assurance/config.toml

helm template maverick deploy/helm/maverick \
  --namespace lightwork \
  -f deploy/reference-architectures/production-assurance/values.yaml \
  --set-string image.digest="$MAVERICK_IMAGE_DIGEST" \
  --set-string env.MAVERICK_GIT_COMMIT="$MAVERICK_GIT_COMMIT" \
  --set-string env.MAVERICK_DEPLOYMENT_ID="$MAVERICK_DEPLOYMENT_ID" \
  --set-file config.content=deploy/reference-architectures/production-assurance/config.toml \
  > rendered-production-assurance.yaml

kubectl apply --dry-run=server -f rendered-production-assurance.yaml
helm upgrade --install maverick deploy/helm/maverick \
  --namespace lightwork --create-namespace \
  -f deploy/reference-architectures/production-assurance/values.yaml \
  --set-string image.digest="$MAVERICK_IMAGE_DIGEST" \
  --set-string env.MAVERICK_GIT_COMMIT="$MAVERICK_GIT_COMMIT" \
  --set-string env.MAVERICK_DEPLOYMENT_ID="$MAVERICK_DEPLOYMENT_ID" \
  --set-file config.content=deploy/reference-architectures/production-assurance/config.toml
```

Do not deploy the rendered manifest until the operator has inspected it for
the exact digest, one replica, the intended ConfigMap, external Secret name,
Postgres backend/RLS settings, PVC, security context, and environment-specific
network controls. Treat the rendered file as potentially sensitive metadata
and delete it according to your change-management policy.

After the v38 owner migration and reviewed runtime grants are applied, compute
and independently verify the epoch-one pin, then run `maverick audit
trust-bootstrap` once. A missing or mismatched ceremony keeps canonical audit
reads and writes fail-closed. See `docs/security-hardening.md` for rotation.
If a staged private half is lost, use the documented
`--replace-pending-key-id <exact-id>` ceremony while the predecessor remains
active; never rerun bootstrap or edit trust rows directly.

Continue with [`RUNBOOK.md`](./RUNBOOK.md); readiness alone is not evidence of
recoverability or compliance.
