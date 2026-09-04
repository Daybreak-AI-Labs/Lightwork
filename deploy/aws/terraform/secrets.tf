# Generated credentials. Alphanumeric-only so they embed in a DSN URL and an
# HTTP header without escaping.
resource "random_password" "db" {
  length  = 28
  special = false
}

resource "random_password" "dashboard_token" {
  length  = 48
  special = false
}

# ---- Secrets Manager entries (injected into the task, never baked into the image) ----

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.name}/db-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db.result
}

resource "aws_secretsmanager_secret" "dashboard_token" {
  name                    = "${local.name}/dashboard-token"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "dashboard_token" {
  secret_id     = aws_secretsmanager_secret.dashboard_token.id
  secret_string = random_password.dashboard_token.result
}

# The provider API key: created as a placeholder so the task definition can
# reference a stable ARN. The real value is written out-of-band (Terraform must
# not hold it in state), e.g.:
#   aws secretsmanager put-secret-value --secret-id lightwork-prod/anthropic-api-key \
#     --secret-string 'sk-ant-...' --profile lightwork --region us-east-2
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${local.name}/anthropic-api-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = "REPLACE_ME" # pragma: allowlist secret -- placeholder, not a real key

  # The real key is rotated in out-of-band; do not let Terraform revert it.
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ---- OIDC browser login (Auth0) ----
# Signs the dashboard's own session cookie after a successful OIDC login.
# Terraform generates and owns this one.
resource "random_password" "oidc_session_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "oidc_session_secret" {
  name                    = "${local.name}/oidc-session-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "oidc_session_secret" {
  secret_id     = aws_secretsmanager_secret.oidc_session_secret.id
  secret_string = random_password.oidc_session_secret.result
}

# The Auth0 application client secret. Placeholder here so the task can
# reference a stable ARN; the real value is written out-of-band (never in
# Terraform state), e.g.:
#   aws secretsmanager put-secret-value --secret-id lightwork-prod/oidc-client-secret \
#     --secret-string '<auth0-client-secret>' --profile lightwork --region us-east-2
resource "aws_secretsmanager_secret" "oidc_client_secret" {
  name                    = "${local.name}/oidc-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "oidc_client_secret" {
  secret_id     = aws_secretsmanager_secret.oidc_client_secret.id
  secret_string = "REPLACE_ME"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ---- Platform mailer (SES SMTP) ----
# SES SMTP credentials. The environment guardrail blocks programmatic IAM key
# creation, so these are provisioned once via the SES console ("Create SMTP
# credentials") and written out-of-band, like the Anthropic key:
#   aws secretsmanager put-secret-value --secret-id lightwork-prod/email-smtp-user     --secret-string '<SMTP username>' ...
#   aws secretsmanager put-secret-value --secret-id lightwork-prod/email-smtp-password --secret-string '<SMTP password>' ...
resource "aws_secretsmanager_secret" "email_smtp_user" {
  name                    = "${local.name}/email-smtp-user"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "email_smtp_user" {
  secret_id     = aws_secretsmanager_secret.email_smtp_user.id
  secret_string = "REPLACE_ME"
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "email_smtp_password" {
  name                    = "${local.name}/email-smtp-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "email_smtp_password" {
  secret_id     = aws_secretsmanager_secret.email_smtp_password.id
  secret_string = "REPLACE_ME"
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Full Postgres DSN, assembled from the RDS endpoint. Alphanumeric password
# means no URL-encoding is required.
resource "aws_secretsmanager_secret" "pg_dsn" {
  name                    = "${local.name}/pg-dsn"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "pg_dsn" {
  secret_id     = aws_secretsmanager_secret.pg_dsn.id
  secret_string = "postgresql://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}" # pragma: allowlist secret -- Terraform interpolation, no literal secret
}
