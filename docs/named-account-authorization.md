# Renewable named accounts

In **Connections**, a named administrator can explicitly enable saved account
authorization for the installation, save a provider app registration, and
authorize a named connection in the selected workspace. Both registration
secrets and access/refresh tokens are encrypted with that workspace's tenant
key. Password fields are excluded from draft recovery; secrets are not returned
by the listing APIs. The existing manually supplied token form remains available.

Supported account profiles are:

| Profile | Granted use | Pinned destination |
| --- | --- | --- |
| Google Drive evidence | Read Drive documents | `www.googleapis.com` |
| Google Cloud Storage discovery | Read storage objects and metadata | `storage.googleapis.com` |
| Jira Cloud reviewed delivery | Read Jira work and create the reviewed task | `api.atlassian.com`, selected site's returned cloud ID |

Create a provider application in the provider's administration console, using
the return address displayed by Lightwork. Enter its client ID and secret in
Connections. Application registration, provider approval requirements and the
account holder's consent screen remain external provider steps. Lightwork does
not create a provider application or claim that a locally saved token has passed
a live customer acceptance test. Google scopes are documented in Google's
[OAuth scope catalog](https://developers.google.com/identity/protocols/oauth2/scopes).
Jira's site-bound API and rotating refresh tokens follow Atlassian's
[OAuth 2.0 application contract](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/).

Review the named connection, requested access and Jira site before opening
consent. On return, finish the connection in the same browser and workspace.
The server holds the PKCE verifier and a ten-minute, single-use state bound to
the account, workspace, app registration, original connection version and exact
return address. A changed account, workspace, configuration or credential refuses
the old return. The browser does not retain a verifier or client secret.

Google Drive and Jira vendor adapters, the Google Cloud Storage discovery
adapter, and named-connection resolution consume these actual saved records.
Normal token renewal retains the connection's reviewed generation. Replacing
the credential, changing sharing, or reconnecting creates a new generation so
enrolled source references and pending approvals must be reviewed again. Changing
an app registration requires reconnecting its accounts, even if old access bytes
have not expired. Ordinary edits cannot move an OAuth credential to another
provider or origin. Jira uses the selected site's ID returned by the provider;
it does not send OAuth credentials directly to a user-entered site.

Renewal is serialized through the existing strict connection-store lock. The
application records a pending renewal before sending a rotating refresh token.
A lost response or interrupted process requires an explicit reconnect; it cannot
safely replay an old refresh token automatically. All calls retain the deployment
and workspace egress policy, public-address pinning, no-redirect token transport,
and bounded token responses. The app cannot waive a deployment-disabled
Connections or OAuth vault setting. The installer exposes these existing knobs
as **Named connections** and **OAuth token vault**.

Synthetic tests cover app registration sealing, consent return, role/workspace/
origin/version mismatches, actual saved-reference consumption, token renewal,
single-flight refresh and lost-response refusal. They make no provider calls.
Live provider registration, consent interoperability, account scope verification
and customer acceptance remain deployment-specific work.
