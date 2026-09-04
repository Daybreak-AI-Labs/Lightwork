# Auth0 browser login (non-secret values; the client secret lives in Secrets
# Manager at lightwork-prod/oidc-client-secret, written out-of-band).
oidc_issuer    = "https://dev-mrz8m4qsbh7hp3gd.us.auth0.com/"
oidc_client_id = "L9M8pKZRBty3pbiT02t1FsHLUCwPBAUg"

# Break-glass master admin: chris@daybreakailabs.com. Both Auth0 identities
# (Google and email/password) are listed so admin holds regardless of how he
# signs in. daychristopher91@gmail.com is intentionally NOT here (demo user).
dashboard_admins = "user:google-oauth2|115836816027351327475,user:auth0|6a9ad1a287e571a6c9d4d080"
