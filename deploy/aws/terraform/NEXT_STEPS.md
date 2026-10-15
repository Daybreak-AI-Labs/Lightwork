# Lightwork AWS deploy — handoff

Everything needed to finish standing up Lightwork on AWS tonight. Written
because the prior session hit a permission wall on `terraform apply` (Auto
mode's classifier hard-blocks it, and self-editing settings to allow it is
also blocked — by design). This session needs a permission mode other than
Auto (Accept Edits / Bypass Permissions / Default-with-prompts all work).

## What already exists (nothing billed beyond these two)

- **AWS account:** `589997396906` (Daybreak Labs). Profile name: `lightwork`.
- **Region:** `us-east-2` for everything except the CodeBuild-triggering
  commands, which are also `us-east-2` (the stack does not use `us-east-1`
  anywhere).
- **Terraform state bucket:** `lightwork-tfstate-589997396906` — versioned,
  SSE-encrypted, public access blocked. Created directly via `aws s3api`
  (it can't bootstrap itself via Terraform), already wired as the backend in
  `versions.tf`.
- **Budget guardrail:** an AWS Budget named `lightwork-monthly`, $200/mo cost
  alert. Created directly via `aws budgets create-budget` (not Terraform
  state). If a re-run errors "already exists" on either of these two, that's
  fine — skip and move on.
- **Terraform stack:** fully written, `fmt`/`init`/`validate`/`plan` all
  passed, at `deploy/aws/terraform/` on this branch (`aws/terraform-deploy`,
  already pushed to origin). **Nothing else has been created yet** — no VPC,
  no RDS, no ECR, no ECS, no ALB. The next real step is `terraform apply`.

## What it builds

VPC (2 public subnets for the ALB + Fargate tasks, no NAT gateway — tasks get
a public IP directly; 2 isolated private subnets for RDS) → ECR repo → a
CodeBuild project that builds `deploy/docker/Dockerfile` from an S3 source
zip and pushes to ECR (no local Docker daemon in these containers, so the
image is built in AWS, not locally) → Secrets Manager (DB password, dashboard
token, Anthropic API key placeholder, assembled Postgres DSN) → RDS Postgres
16 (`db.t4g.micro`) → ECS Fargate service running `maverick dashboard --host
0.0.0.0 --port 8765` behind an ALB (health check `/livez`) → least-privilege
IAM roles → CloudWatch logs.

## Steps, in order

### 0. Confirm/redo AWS login

```bash
export PATH="$HOME/.local/bin:$PATH"
aws sts get-caller-identity --profile lightwork   # if this already works, skip to step 1
```

If it fails (fresh container, no cached login) — install the CLI if needed,
then log in:

```bash
curl -fsSL 'https://awscli.amazonaws.com/v2/install.sh' | bash
export PATH="$HOME/.local/bin:$PATH"
aws configure set region us-east-2 --profile lightwork
aws login --region us-east-2 --profile lightwork --remote
# Prints a URL. The human opens it, approves, gets a code back, pastes it here.
aws sts get-caller-identity --profile lightwork   # must show Account 589997396906
```

### 1. Install Terraform if needed

```bash
export PATH="$HOME/.local/bin:$PATH"
terraform version || {
  TFVER=$(curl -fsSL https://checkpoint-api.hashicorp.com/v1/check/terraform | python3 -c 'import sys,json;print(json.load(sys.stdin)["current_version"])')
  cd /tmp && curl -fsSL -o tf.zip "https://releases.hashicorp.com/terraform/${TFVER}/terraform_${TFVER}_linux_amd64.zip"
  unzip -o -q tf.zip && mkdir -p ~/.local/bin && mv -f terraform ~/.local/bin/terraform && rm -f tf.zip
}
```

### 1b. Create the untracked operator inputs

`oidc.auto.tfvars` is tracked and holds only non-personal configuration. The
two inputs that identify people are read from `local.auto.tfvars`, which git
ignores:

```bash
cp deploy/aws/terraform/local.auto.tfvars.example deploy/aws/terraform/local.auto.tfvars
# fill in dashboard_admins (the break-glass Auth0 principals) and email_from
```

An existing deployment must get this file before its next `terraform apply`,
or the plan removes the break-glass admins and switches platform email to the
`variables.tf` default from-address. The previous values are in the git
history of `oidc.auto.tfvars` (last tracked at commit `252767e6`).

Two further protections are part of the stack and need nothing from you:
every Secrets Manager entry keeps a 7-day recovery window (restore with
`aws secretsmanager restore-secret`; an intentional teardown that must reuse
a name at once deletes with `--force-delete-without-recovery` first), and the
state volume has AWS Backup enabled (daily, 35-day retention, default vault).
The deployer identity is bound to the deploy region and can never delete the
database, its snapshots or the state volume; retiring those is a separately
reviewed operation under a different identity.

### 2. Apply the stack

**Critical gotcha:** the `aws login` credential store (`~/.aws/login/`) is a
newer mechanism the Terraform AWS provider's SDK can't read directly. Bridge
it into plain env vars first — every terraform invocation needs this in the
same shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN   # this sandbox may have proxy-injected placeholder keys that silently override the profile — clear them first
eval "$(aws configure export-credentials --profile lightwork --format env)"

cd /home/user/Lightwork/deploy/aws/terraform   # adjust if the checkout path differs
terraform init -input=false
terraform plan -input=false          # sanity check — should show ~30 resources to add, 0 to change/destroy
terraform apply -input=false -auto-approve
```

RDS takes ~10 minutes to provision; everything else is fast. `terraform
apply` does **not** wait for the ECS service to go healthy (no
`wait_for_steady_state`), so it returns well before tasks are actually
running — that's expected, not a failure.

### Database protection on an existing deployment

Before applying a change to an existing stack, inspect the plan for
`aws_db_instance.main`. The safety update should enable AWS deletion
protection and configure a final snapshot **in place**. Stop and review any
plan that replaces or destroys the database; `prevent_destroy` is meant to
block that apply. A plain `terraform destroy` will also be blocked. An
intentional retirement needs a separately reviewed plan, a verified backup,
and explicit removal of both the Terraform and AWS deletion guards; the
final snapshot must then complete before the database is removed. The snapshot
name stays stable during normal plans and must be changed before another
retirement if a previous final snapshot already uses it.

The database still runs in one availability zone with seven days of automated
backup retention. Deletion protection does not add failover or prove restore
readiness; decide separately whether the higher ongoing cost of Multi-AZ is
warranted and periodically verify a restore.

### 3. Build and push the image via CodeBuild

Can run in parallel with step 2 (kick it off right after `terraform apply`
starts, or right after — the ECS tasks just won't have an image to pull until
this finishes). This step alone takes ~15-20 minutes (the release-cohort
wheelhouse build is the long pole).

```bash
cd /home/user/Lightwork
rm -f /tmp/lightwork-src.zip
zip -r -q /tmp/lightwork-src.zip \
  release-cohort.toml \
  requirements \
  scripts/install_release_cohort.py \
  packages/maverick-core packages/maverick-shield packages/maverick-channels \
  packages/maverick-evolve packages/maverick-dashboard packages/maverick-mcp packages/maverick-knowledge \
  apps/installer-cli \
  deploy/docker \
  -x '*/__pycache__/*' -x '*.pyc' -x '*.egg-info/*' -x '*/.pytest_cache/*' -x '*/node_modules/*'

BUCKET=$(cd deploy/aws/terraform && terraform output -raw build_source_bucket)
aws s3 cp /tmp/lightwork-src.zip "s3://${BUCKET}/source.zip" --profile lightwork --region us-east-2

aws codebuild start-build --project-name lightwork-prod --profile lightwork --region us-east-2

# Poll until it succeeds (SUCCEEDED / FAILED / FAULT / TIMED_OUT / STOPPED):
aws codebuild batch-get-builds --ids "$(aws codebuild list-builds-for-project --project-name lightwork-prod --profile lightwork --region us-east-2 --query 'ids[0]' --output text)" \
  --profile lightwork --region us-east-2 --query 'builds[0].buildStatus' --output text
```

### 4. Set the real Anthropic API key

The Secrets Manager entry is currently a placeholder (`REPLACE_ME`) —
Terraform is configured with `ignore_changes` on it so re-applying will never
stomp a real value once set:

```bash
aws secretsmanager put-secret-value \
  --secret-id lightwork-prod/anthropic-api-key \
  --secret-string 'sk-ant-...' \
  --profile lightwork --region us-east-2
```

### 4b. Pin the image you just built (recommended)

The repository is MUTABLE and `image_tag` defaults to `latest`, so a service
follows whatever is pushed under that tag on its next task start. To run exactly
the image CodeBuild produced, read its digest and set `image_digest`:

```bash
aws ecr describe-images --repository-name lightwork --region us-east-2 \
  --image-ids imageTag=latest --query 'imageDetails[0].imageDigest' --output text
# -> sha256:...; then in local.auto.tfvars:
#    image_digest = "sha256:..."
terraform apply
```

The task definition then names `repository@digest`; a later push to `latest`
changes nothing until you update the digest. Clear the variable to follow the
tag again.

### 5. Make sure ECS picks up the pushed image + new key

```bash
aws ecs update-service --cluster lightwork-prod --service lightwork-prod \
  --force-new-deployment --profile lightwork --region us-east-2
```

### 6. Get the URL and the login token

```bash
cd deploy/aws/terraform
terraform output app_url
aws secretsmanager get-secret-value \
  --secret-id "$(terraform output -raw dashboard_token_secret)" \
  --query SecretString --output text --profile lightwork --region us-east-2
```

Verify: `curl http://<alb-dns-name>/livez` should return healthy once the ECS
task is running (check `aws ecs describe-services --cluster lightwork-prod
--services lightwork-prod --profile lightwork --region us-east-2` if it
isn't). Then open the `app_url` in a browser and log in with the token
(bearer token / `Authorization: Bearer <token>`, or however the dashboard's
login screen wants it — see `packages/maverick-dashboard`).

### 7. Module configuration that lives on the state volume

A few governed modules have a closed configuration contract that the Settings
page cannot write (lists and nested tables): AI evidence analysis, assessment
evidence auto-population, and the native consent SDK. They read
`~/.maverick/config.toml` on the EFS state volume, which the image does not
ship. Place it with the maintenance task in `fstask.tf` (same access point the
app mounts; run it with the app's public subnets and security group):

```bash
cat > config.toml <<'EOF'
[evidence_gateway]
enable = true

[ai_evidence_analysis]
enable = true
# Closed allowlist; "xls" additionally needs [ai_evidence_analysis.legacy_xls]
# and OCR needs [ai_evidence_analysis.ocr], both with SHA-pinned binaries.
enabled_media_types = ["pdf", "png", "jpeg", "text", "xlsx"]

[assessment_evidence_autopopulation]
enable = true
apply_enabled = true

[consent_preferences]
enable = true

[consent_preferences.publishing]
enable = true

[consent_preferences.public_api]
enable = true

[consent_preferences.native_sdk]
enable = true
receipt_key_id = "lightwork-prod-2026-09"
receipt_key_revision = 1
# Names the task environment variable that secrets.tf injects.
receipt_key_secret_ref = "MAVERICK_CONSENT_RECEIPT_ROOT" # pragma: allowlist secret -- variable name, not a value
EOF

B64=$(base64 -w0 config.toml)
ARN=$(aws ecs run-task --cluster lightwork-prod --task-definition lightwork-prod-fstask \
  --launch-type FARGATE --platform-version 1.4.0 \
  --network-configuration "awsvpcConfiguration={subnets=[<public-subnet-a>,<public-subnet-b>],securityGroups=[<app-sg>],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"fs\",\"command\":[\"cd /home/maverick/.maverick && echo $B64 | base64 -d > config.toml && chmod 600 config.toml && cat config.toml\"]}]}" \
  --query 'tasks[0].taskArn' --output text --profile lightwork --region us-east-2)
aws ecs wait tasks-stopped --cluster lightwork-prod --tasks "$ARN" --profile lightwork --region us-east-2
aws logs get-log-events --log-group-name /ecs/lightwork-prod \
  --log-stream-name "fstask/fs/${ARN##*/}" --query 'events[].message' --output text \
  --profile lightwork --region us-east-2
```

The native consent SDK also needs the receipt signing root in the task
environment (`MAVERICK_CONSENT_RECEIPT_ROOT`, from `secrets.tf`); `terraform
apply` creates the secret and rolls a task revision that carries it. The
evidence parsers (pypdf, openpyxl, pillow, xlrd, olefile) come from the
`evidence-analysis` extra, which `deploy/docker/Dockerfile` now includes in the
image build, so rebuild and redeploy (step 3 and step 5) after this change.
Then `GET /api/v1/features/modules` should report the three modules as
`configured` instead of `incomplete`.

## Fast-follow (not tonight-blocking)

- HTTPS + a real domain: ACM cert + a Cloudflare CNAME for a
  `daybreakailabs.com` subdomain onto the ALB DNS name, plus an `aws_lb_listener`
  on 443 (currently only port 80 is wired).
- Cognito for real user login (token auth is fine for tonight).
- Once verified working, open a PR for this branch (`aws/terraform-deploy`)
  against `main` so the IaC isn't stranded on a feature branch.
- Root credentials were used for `aws login` — fine to get moving tonight,
  but consider a scoped deploy IAM role/user afterward instead of running
  Terraform as account root long-term.
