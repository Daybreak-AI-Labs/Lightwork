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
  secret_string = "REPLACE_ME"

  # The real key is rotated in out-of-band; do not let Terraform revert it.
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
  secret_string = "postgresql://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
}
