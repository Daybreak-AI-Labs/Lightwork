# Renewable named accounts

In **Connections**, a signed-in installation administrator can explicitly enable
saved account authorization and save a provider app registration for the selected
workspace. A workspace administrator can then authorize their own named account
using that registration. Workspace administration alone does not grant access to
enablement or application registration controls. Both registration
secrets and access/refresh tokens are encrypted with that workspace's tenant
key. Password fields are excluded from draft recovery; secrets are not returned
by the listing APIs. The existing manually supplied token form remains available.

Supported account profiles are:

| Profile | Granted use | Pinned destination |
| --- | --- | --- |
| Google Drive evidence | Read Drive documents | `www.googleapis.com` |
| Google Cloud Storage discovery | Read storage objects and metadata | `storage.googleapis.com` |
| Jira Cloud reviewed delivery | Read Jira work and create the reviewed task | `api.atlassian.com`, selected site's returned cloud ID |

The installation operator creates a provider application in the provider's
administration console, using the return address displayed by Lightwork, then
enters its client ID and secret in Connections. Customers see which providers
are configured and review their own consent; normal account setup does not
require them to visit a developer console or handle registration secrets.
Only an installation administrator who can administer the current workspace
can inspect or edit its shared registration metadata. No listing returns the
client secret. Application registration, provider approval requirements and the
account holder's consent screen remain external provider steps. Lightwork does
not create a provider application or claim that a locally saved token has passed
a live customer acceptance test. Google scopes are documented in Google's
[OAuth scope catalog](https://developers.google.com/identity/protocols/oauth2/scopes).
Jira's site-bound API and rotating refresh tokens follow Atlassian's
[OAuth 2.0 application contract](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/).

Shared registrations live under a distinct key in the existing workspace-sealed
store. They are not shared across tenants. Existing personal registrations remain
private to their original owner and are not automatically promoted or copied.
New consent uses the workspace registration when present, with an explicit
owner-only legacy fallback otherwise. Legacy availability is labelled separately
and does not prefill the shared registration editor. Existing consent and saved
credentials without an application-key field continue to resolve only their
owner's legacy registration. Every new consent and credential pins the exact
selected registration key and revision; adding or removing another registration
cannot silently switch that binding.

Review the named connection, requested access and Jira site before opening
consent. On return, finish the connection in the same browser tab, signed-in
account and workspace that started it. A vendor request opened through its
connection detour can be returned to after cancellation or completion. Non-secret
connection input is retained separately from the single-use callback.
The server holds the PKCE verifier and a ten-minute, single-use state bound to
the account, workspace, app registration, original connection version and exact
return address. A changed account, workspace, configuration or credential refuses
the old return. The browser does not retain a verifier or client secret.

The current return implementation is a browser flow. It matches the returned
state against the initiating tab's session storage before offering the explicit
finish action. Moving consent into a separate browser or a different tab does
not transfer that state or the signed-in session. The desktop shell currently
has no external-browser-to-native callback bridge. The same-browser automated
tests do not qualify installed desktop return, native provider interoperability,
or restart recovery of an in-progress native authorization. Those require
additional implementation and installed-target tests.

Google Drive and Jira vendor adapters, the Google Cloud Storage discovery
adapter, and named-connection resolution consume these actual saved records.
Normal token renewal retains the connection's reviewed generation. Replacing
the credential, changing sharing, or reconnecting creates a new generation so
enrolled source references and pending approvals must be reviewed again. Changing
an app registration requires reconnecting its accounts, even if old access bytes
have not expired. Public status checks the current registration binding rather
than displaying a stale ready state; changed registrations show reconnect
guidance, and unreadable authorization settings show unavailability. Authenticated
disconnect must include the reviewed connection revision. A concurrent reconnect
or rotation causes a stale disconnect to fail without deleting the replacement.
Ordinary edits cannot move an OAuth credential to another
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
