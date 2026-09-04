# Auth0 browser login (non-secret values; the client secret lives in Secrets
# Manager at lightwork-prod/oidc-client-secret, written out-of-band).
# Auth0 custom domain (verified, Let's Encrypt cert). Using it as the issuer
# means the login page + tokens are served from login.daybreakailabs.com rather
# than the dev-*.us.auth0.com tenant domain. JWKS is derived from this in ecs.tf.
oidc_issuer    = "https://login.daybreakailabs.com/"
oidc_client_id = "L9M8pKZRBty3pbiT02t1FsHLUCwPBAUg"

# Break-glass master admin: chris@daybreakailabs.com. Both Auth0 identities
# (Google and email/password) are listed so admin holds regardless of how he
# signs in. daychristopher91@gmail.com is intentionally NOT here (demo user).
dashboard_admins = "user:google-oauth2|115836816027351327475,user:auth0|6a9ad1a287e571a6c9d4d080"

# Invite-by-email flow on the /users page (accept -> Auth0 sign-in -> role bound).
dashboard_invites = true

# Platform email via the Amazon SES API using the task role (no SMTP credential).
# SES domain identity daybreakailabs.com is verified; from noreply@daybreakailabs.com.
email_enabled = true
email_from    = "noreply@daybreakailabs.com"
