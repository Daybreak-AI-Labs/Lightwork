# Auth0 browser login (non-secret values; the client secret lives in Secrets
# Manager at lightwork-prod/oidc-client-secret, written out-of-band).
# Auth0 custom domain (verified, Let's Encrypt cert). Using it as the issuer
# means the login page + tokens are served from login.daybreakailabs.com rather
# than the dev-*.us.auth0.com tenant domain. JWKS is derived from this in ecs.tf.
oidc_issuer    = "https://login.daybreakailabs.com/"
oidc_client_id = "L9M8pKZRBty3pbiT02t1FsHLUCwPBAUg"

# Who holds break-glass admin (dashboard_admins) and which address platform
# email is sent from (email_from) identify people, so they are not tracked:
# they live in local.auto.tfvars, copied from local.auto.tfvars.example.
# Terraform loads every *.auto.tfvars in this directory.

# Invite-by-email flow on the /users page (accept -> Auth0 sign-in -> role bound).
dashboard_invites = true

# Platform email via the Amazon SES API using the task role (no SMTP credential).
# The SES domain identity daybreakailabs.com is verified.
email_enabled    = true
flows_enabled    = true
data_use_enabled = true
