"""All-settings catalog rows for access, identity and deployment core.

Sign-in (company sign-in, single sign-on, the company proxy), dashboard
access (administrators, roles, invitations, sessions), workspaces and client
binding, sharing, encryption and the key service, custody of the activity
record, and the server-side plumbing a deployment chooses (record stores,
the work queue, remote workers, linked servers, the license).

Kept apart from settings_catalog.py so each group can be extended without
editing the shared catalog. entries receives the catalog's own row and
column types (a plain import would be circular).

Sign-in, administrator and key rows are also reviewed by
``settings_access_guard`` before they are saved: a change that would lock
the person saving (or everyone) out, or leave stored records unreadable, is
refused until its check passes, and confirmed before anyone is signed out.

Not given a row: ``[client] id`` and the ``[deployment]`` GPU counts (the
reasons are in settings_catalog_structured) and ``[approval_delegation]`` (a
legacy name: the delegation rules it stands for are ``[approval] delegation``,
which has its own row). The governance level (applied with the settings it
stands for) and the unified-custody security monitoring address (with a part
for each workspace) are in settings_catalog_structured.
"""
from __future__ import annotations

# Plain patterns (full match) for text and list entries.
_HTTPS = r"https://[^\s\"'<>?#]{1,300}"
_HTTPS_OR_LOCAL = r"(https://[^\s\"'<>?#]{1,300}|http://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?(/[^\s\"'<>?#]{0,200})?)"
_ENTITY = r"(https://[^\s\"'<>]{1,300}|urn:[A-Za-z0-9:._-]{1,200})"
_REF = r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}"
_HEADER = r"[A-Za-z0-9][A-Za-z0-9-]{0,63}"
_NETWORK = r"[0-9A-Fa-f:.]{2,45}(/[0-9]{1,3})?"
_PRINCIPAL = r"user:[^\s,]{1,200}"
_CLIENT = r"[a-z0-9][a-z0-9._-]{0,63}"
_NAME = r"[A-Za-z0-9._-]{1,64}"
_SECRET32 = r"\S{32,512}"
_REGION = r"[a-z]{2}(-[a-z]+)+-[0-9]{1,2}"
_BUCKET = r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]"
_PREFIX = r"[A-Za-z0-9!_.*'()/{}-]{0,200}"
_HOST = r"[A-Za-z0-9*]([A-Za-z0-9*.-]{0,251}[A-Za-z0-9])?"
_HOST_PORT = r"[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?|\[[0-9A-Fa-f:.]{2,45}\](:[0-9]{1,5})?"
_SIEM = r"(https?|tcp|udp)://[^\s\"'<>]{1,300}"
_REDIS = r"(rediss?|unix)://\S{1,500}"
_PAGE = r"/[A-Za-z0-9/_.-]{0,200}"
_ORIGIN = r"[a-z0-9][a-z0-9._-]{0,63}"
_ALGORITHMS = (("RS256", "RS256 (RSA)"), ("RS384", "RS384 (RSA)"), ("RS512", "RS512 (RSA)"),
               ("ES256", "ES256 (elliptic curve)"), ("ES384", "ES384 (elliptic curve)"),
               ("ES512", "ES512 (elliptic curve)"), ("PS256", "PS256 (RSA-PSS)"),
               ("PS384", "PS384 (RSA-PSS)"), ("PS512", "PS512 (RSA-PSS)"),
               ("EdDSA", "EdDSA (Edwards curve)"))

# Pairs the kernel rejects (merged into the catalog's cross-checks).
REQUIRES_NONEMPTY = {
    # The shared activity record only runs over a signed activity record.
    "audit.unified.enable": "audit.sign",
    "audit.unified.custody.enable": "audit.unified.enable",
    "audit.unified.custody.worm_enable": "audit.worm.provider",
}
REQUIRES_VALUE = {
    ("audit.worm.provider", "s3"): ("audit.worm.bucket",),
}
ORDERED = (
    ("audit.unified.custody.retry_backoff_seconds",
     "audit.unified.custody.max_retry_backoff_seconds"),
)


def _sign_in(S) -> tuple:  # noqa: N803 -- the catalog's row type
    oidc, saml, proxy = "Company sign-in", "Single sign-on", "Sign-in through your company proxy"
    grp = "People & access"
    return (
        # ------------------------------------------------ company sign-in
        S("auth.oidc", "enabled", "Sign in with your company account",
          "People sign in with your company's sign-in provider instead of using Lightwork on "
          "this computer without signing in. Fill in the provider's details below and use "
          "Test connection before turning this on.",
          "toggle", grp, False, "maverick.oidc", subgroup=oidc, test="oidc",
          env=("MAVERICK_OIDC_ENABLED",), weaker=False,
          warning="People will no longer need your company sign-in to use Lightwork.",
          aliases="oidc single sign-on sso identity provider login"),
        S("auth.oidc", "issuer", "Sign-in provider address",
          "The web address of your company's sign-in provider, exactly as the provider shows it.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_HTTPS, max_length=300,
          test="oidc", env=("MAVERICK_OIDC_ISSUER",), weaker="change",
          warning="Lightwork will trust sign-ins from a different provider.",
          aliases="issuer oidc"),
        S("auth.oidc", "client_id", "Lightwork's name at the provider",
          "The name your sign-in provider gave Lightwork when it was registered there.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_REF,
          env=("MAVERICK_OIDC_CLIENT_ID",), aliases="client id"),
        S("auth.oidc", "client_secret", "Lightwork's password at the provider",
          "The password your sign-in provider gave Lightwork. It is stored sealed and never "
          "shown again.",
          "secret", grp, "", "maverick.oidc", subgroup=oidc, max_length=512,
          env=("MAVERICK_OIDC_CLIENT_SECRET",), aliases="client secret"),
        S("auth.oidc", "audience", "Accepted sign-in audience",
          "Sign-ins are accepted only when they were issued for this name, usually the same as "
          "Lightwork's name at the provider.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_REF,
          env=("MAVERICK_OIDC_AUDIENCE",), weaker="change",
          warning="Sign-ins issued for a different application may be accepted.",
          aliases="audience"),
        S("auth.oidc", "session_secret", "Sign-in session key",
          "The key Lightwork uses to keep people signed in after they sign in with your "
          "provider. Use at least 32 characters. It is stored sealed and never shown again.",
          "secret", grp, "", "maverick.oidc", subgroup=oidc, pattern=_SECRET32,
          max_length=512, env=("MAVERICK_OIDC_SESSION_SECRET",), weaker="change",
          warning="Everyone signed in with your company sign-in will be signed out.",
          aliases="session secret cookie"),
        S("auth.oidc", "redirect_uri", "Return address after sign-in",
          "The address of Lightwork's sign-in return page, as registered with your provider.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_HTTPS_OR_LOCAL,
          max_length=300, env=("MAVERICK_OIDC_REDIRECT_URI",), aliases="redirect callback"),
        S("auth.oidc", "jwks_uri", "Address of the provider's signing keys",
          "Where Lightwork reads the keys that prove a sign-in is genuine. Leave empty to use "
          "the one your provider publishes.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_HTTPS, max_length=300,
          test="oidc", env=("MAVERICK_OIDC_JWKS_URI",), weaker="change",
          warning="Lightwork will check sign-ins against different keys.",
          aliases="jwks keys"),
        S("auth.oidc", "algorithms", "Accepted signature types",
          "The kinds of signature a sign-in may carry. Keep the provider's own choice.",
          "multiselect", grp, ["RS256", "ES256"], "maverick.oidc", subgroup=oidc,
          choices=_ALGORITHMS, env=("MAVERICK_OIDC_ALGORITHMS",), aliases="algorithms"),
        S("auth.oidc", "authorization_endpoint", "Provider sign-in page",
          "Only needed when your provider does not publish its details: the page people are "
          "sent to for signing in.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_HTTPS, max_length=300,
          env=("MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",), aliases="authorization"),
        S("auth.oidc", "token_endpoint", "Provider sign-in check address",
          "Only needed when your provider does not publish its details: where Lightwork "
          "confirms a sign-in.",
          "text", grp, "", "maverick.oidc", subgroup=oidc, pattern=_HTTPS, max_length=300,
          env=("MAVERICK_OIDC_TOKEN_ENDPOINT",), aliases="token"),
        S("auth.oidc", "end_session_endpoint", "Provider sign-out page",
          "Where people are sent when they sign out, so they are signed out of your provider "
          "too.",
          "text", grp, "", "maverick_dashboard.oidc_login", subgroup=oidc, pattern=_HTTPS,
          max_length=300, env=("MAVERICK_OIDC_END_SESSION_ENDPOINT",), aliases="logout"),
        # --------------------------------------------------- single sign-on
        S("auth.saml", "idp_metadata_url", "Address of your provider's details",
          "The web address where your single sign-on provider publishes its details for "
          "Lightwork. Use Test connection to check it.",
          "text", grp, "", "maverick_dashboard.saml", subgroup=saml, pattern=_HTTPS,
          max_length=300, test="saml", weaker="change",
          warning="Lightwork will trust sign-ins from a different provider.",
          aliases="saml metadata identity provider"),
        S("auth.saml", "idp_metadata_file", "File with your provider's details",
          "Instead of an address: a file on the server with your provider's details.",
          "path", grp, "", "maverick_dashboard.saml", subgroup=saml, path_kind="file",
          max_length=400, weaker="change",
          warning="Lightwork will trust sign-ins from a different provider.",
          aliases="saml metadata file"),
        S("auth.saml", "sp_entity_id", "Lightwork's name for single sign-on",
          "The name Lightwork uses with your provider, usually Lightwork's own address.",
          "text", grp, "", "maverick_dashboard.saml", subgroup=saml, pattern=_ENTITY,
          max_length=300, aliases="entity id"),
        S("auth.saml", "acs_url", "Single sign-on return address",
          "Lightwork's address that your provider sends people back to after they sign in.",
          "text", grp, "", "maverick_dashboard.saml", subgroup=saml, pattern=_HTTPS,
          max_length=300, aliases="acs assertion consumer"),
        S("auth.saml", "want_assertions_signed", "Require signed sign-ins",
          "Accept a single sign-on only when your provider signed it.",
          "toggle", grp, True, "maverick_dashboard.saml", subgroup=saml, weaker=False,
          warning="Unsigned sign-ins from your provider will be accepted.",
          aliases="signed assertions"),
        S("auth.saml", "sp_cert_file", "Lightwork's certificate file",
          "Optional: the certificate Lightwork shows your provider, as a file on the server.",
          "path", grp, "", "maverick_dashboard.saml", subgroup=saml, path_kind="file",
          max_length=400, aliases="certificate"),
        S("auth.saml", "sp_key_file", "Lightwork's private key file",
          "Optional: the private key that goes with the certificate. Only Lightwork may be "
          "able to read it.",
          "path", grp, "", "maverick_dashboard.saml", subgroup=saml, path_kind="file",
          path_private=True, max_length=400, aliases="private key"),
        S("auth.saml", "name_attr", "Name to show for each person",
          "Optional: which detail from your provider to show as the person's name.",
          "text", grp, "", "maverick_dashboard.saml", subgroup=saml, pattern=_REF,
          aliases="display name attribute"),
        S("auth.saml", "default_relay_state", "Page after sign-in",
          "The Lightwork page people land on after single sign-on, such as / for the home "
          "page.",
          "text", grp, "/", "maverick_dashboard.saml", subgroup=saml, pattern=_PAGE,
          aliases="relay state landing"),
        # ------------------------------------------------ company proxy
        S("auth.proxy", "enabled", "Sign in through your company proxy",
          "Your company's proxy signs people in and tells Lightwork who they are. Only turn this "
          "on when the proxy is the only way to reach Lightwork.",
          "toggle", grp, False, "maverick.proxy_auth", subgroup=proxy,
          env=("MAVERICK_PROXY_AUTH",), weaker=True,
          warning="Lightwork will trust the person your proxy names, so anyone who can reach "
                  "Lightwork without passing through the proxy could pretend to be someone else.",
          aliases="reverse proxy header sso"),
        S("auth.proxy", "header", "Name of the header the proxy sends",
          "The request header that carries the signed-in person's name.",
          "text", grp, "X-Forwarded-User", "maverick.proxy_auth", subgroup=proxy,
          pattern=_HEADER, env=("MAVERICK_PROXY_AUTH_HEADER",), weaker="change",
          warning="Lightwork will read people's names from a different header.",
          aliases="header forwarded user"),
        S("auth.proxy", "trusted_proxies", "Proxy addresses to trust",
          "Only requests from these network addresses (or address ranges) may name who is "
          "signed in.",
          "list", grp, [], "maverick.proxy_auth", subgroup=proxy, pattern=_NETWORK,
          item_label="Address", item_hint="10.0.0.5", item_error="a network address or range",
          weaker="grow", warning="More network addresses will be able to tell Lightwork who is "
                                 "signed in.",
          aliases="trusted proxies network"),
        S("auth.proxy", "trust_loopback", "Trust a proxy on this computer",
          "When no proxy addresses are listed, trust a proxy running on the same computer as "
          "Lightwork.",
          "toggle", grp, False, "maverick.proxy_auth", subgroup=proxy, weaker=True,
          warning="Any program on this computer will be able to tell Lightwork who is signed "
                  "in.",
          aliases="loopback localhost"),
    )


def _people(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp = "People & access"
    access, invites = "Dashboard access", "Invitations and sessions"
    return (
        S("dashboard", "admins", "Administrators",
          "People who are always administrators, whatever their other roles. Each entry is the "
          "name the person signs in with, such as user:name@example.com.",
          "list", grp, [], "maverick_dashboard.admin_pages", subgroup=access,
          pattern=_PRINCIPAL, item_label="Administrator", item_hint="user:name@example.com",
          item_error="a sign-in name that starts with user:",
          env=("MAVERICK_DASHBOARD_ADMINS",), weaker="grow",
          warning="More people will be able to change every setting.",
          aliases="admins administrators owners"),
        S("dashboard", "token", "Server access token",
          "A long password that programs, and other computers, use to reach Lightwork. "
          "Browsers can't send it, so set up a sign-in method first if people use Lightwork "
          "in a browser. To reach Lightwork from other computers, restart it after setting "
          "this. Use at least 32 characters. It is stored sealed and never shown again.",
          "secret", grp, "", "maverick_dashboard._shared", subgroup=access, pattern=_SECRET32,
          max_length=512, env=("MAVERICK_DASHBOARD_TOKEN",), weaker="change",
          warning="Programs using the old token will be refused, and anyone holding the new "
                  "one can do everything an administrator can.",
          aliases="bearer token api access remote"),
        S("dashboard", "default_role", "Role for people without one",
          "The fallback for accounts without an explicit role or verified group mapping. Existing unassigned accounts also inherit changes. Users offers a reviewed viewer/no-department migration.",
          "select", grp, "operator", "maverick_dashboard.rbac", subgroup=access,
          choices_from="roles", weaker="change",
          warning="Everyone without a role of their own will get this role's permissions.",
          aliases="default role viewer operator"),
        S("dashboard", "require_auth", "Always require sign-in",
          "Never let anyone use Lightwork without signing in, not even on this computer. Set "
          "up a sign-in method first.",
          "toggle", grp, False, "maverick_dashboard.auth", subgroup=access,
          env=("MAVERICK_DASHBOARD_REQUIRE_AUTH",), weaker=False,
          warning="Lightwork will again open on this computer without sign-in when no sign-in "
                  "method is set up.",
          aliases="require authentication login"),
        S("dashboard", "local_owner_setup", "Allow owner setup on this computer",
          "On a new installation, let someone at this computer create the owner account "
          "before any sign-in method is set up.",
          "toggle", grp, True, "maverick_dashboard.local_owner", subgroup=access,
          env=("MAVERICK_LOCAL_OWNER_SETUP",), aliases="owner setup first"),
        S("plugins", "workbench", "Plugin qualification workbench",
          "Enable administrator-only version checks in a restricted local container, and pin used plugin bytes for ongoing tasks. No service credentials or network are available during checks.",
          "toggle", grp, False, "maverick.plugin_workbench", subgroup=access),
        S("plugins", "workbench_image", "Reviewed local plugin-check image",
          "Fixed image reference for an already installed Linux check environment containing the exact approved plugin and its validation tools. Lightwork never downloads an image or runs these checks directly on the host.",
          "text", grp, "", "maverick.plugin_workbench", subgroup=access, max_length=71,
          pattern=r"^(sha256:[a-f0-9]{64})?$"),
        S("workflows", "versioning", "Workflow draft and publication history",
          "Keep immutable snapshots, check reviewed input samples, and require administrator publication for new scheduled workflows. Existing schedule contents stay pinned.",
          "toggle", grp, True, "maverick.workflow_versions", subgroup=access),
        S("workflows", "typed_inputs", "Author typed workflow inputs",
          "Let workflow authors add dates, choices, defaults, optional inputs and own connected accounts. Account selections need this setting on and are checked again before execution; other saved inputs still validate when it is off.",
          "toggle", grp, True, "maverick_dashboard.api", subgroup=access),
        S("connections", "allow_self_connect", "Let employees connect their own approved accounts",
          "Employees with permission to operate can sign in to administrator-approved account profiles. Accounts stay private; app registration and sharing remain administrator tasks.",
          "toggle", grp, False, "maverick.connection_oauth", subgroup=access, weaker=True,
          warning="Employees can authorize their own accounts for the approved profiles."),
        S("connections", "self_connect_profiles", "Approved employee account profiles",
          "Comma-separated profiles: google-drive, google-cloud-storage, jira-cloud. Jira includes reviewed write access; approve it only when needed.",
          "text", grp, "google-drive", "maverick.connection_oauth", subgroup=access, max_length=128,
          pattern=r"^(google-drive|google-cloud-storage|jira-cloud)(,(google-drive|google-cloud-storage|jira-cloud))*$"),
        S("dashboard", "extension_form_values", "Allow reviewed browser form values",
          "Allow a person to explicitly share visible form values after reviewing them. Values remain off by default for each page; password values are never read.",
          "toggle", grp, False, "maverick_dashboard.api", subgroup=access, weaker=True,
          warning="Reviewed browser context may include private values entered in forms."),
        S("dashboard", "extension_page_structure", "Allow reviewed browser page structure",
          "Allow a person to review and share page headings and interactive controls with the configured AI service.",
          "toggle", grp, True, "maverick_dashboard.api", subgroup=access, weaker=True,
          warning="Reviewed browser context may include headings and control labels from private pages."),
        S("dashboard", "allow_extension", "Allow the browser extension",
          "Let Lightwork's browser extension work with this installation. It needs the "
          "server's access token.",
          "toggle", grp, False, "maverick_dashboard.app", subgroup=access,
          env=("MAVERICK_DASHBOARD_ALLOW_EXTENSION",), weaker=True,
          warning="The browser extension will be able to act in Lightwork.",
          aliases="extension browser add-on"),
        S("dashboard", "public_url", "Lightwork's address for others",
          "The address people use to reach Lightwork, used in invitation, approval and answer-sharing links.",
          "text", grp, "", "maverick_dashboard.automation_queue", subgroup=access,
          pattern=_HTTPS_OR_LOCAL, max_length=300, env=("MAVERICK_PUBLIC_URL",),
          aliases="public url link address"),
        S("dashboard", "max_request_mb", "Largest upload",
          "The largest request Lightwork accepts, such as one upload of files.",
          "number", grp, 600, "maverick_dashboard.request_size", subgroup=access,
          minimum=1, maximum=4096, integer=True, unit="MB",
          env=("MAVERICK_DASHBOARD_MAX_REQUEST_MB",), weaker="higher",
          warning="Much larger uploads will be accepted, which can fill the server's memory "
                  "or disk.",
          aliases="upload size limit"),
        S("dashboard", "theme", "Colour theme for everyone",
          "The theme people see until they choose their own in Settings.",
          "select", grp, "dark", "maverick_dashboard.app", subgroup=access,
          choices_from="themes", aliases="theme colours dark light"),
        S("dashboard", "density", "Page spacing for everyone",
          "How much space pages use, until people choose their own.",
          "select", grp, "comfortable", "maverick_dashboard.app", subgroup=access,
          choices=(("comfortable", "Comfortable"), ("compact", "Compact")),
          aliases="density compact"),
        S("dashboard", "invites", "Invite people by email link",
          "Administrators can invite people with a one-time link, and invited people sign in "
          "with that link instead of a company sign-in.",
          "toggle", grp, False, "maverick_dashboard.invites", subgroup=invites,
          env=("MAVERICK_DASHBOARD_INVITES",), aliases="invitations invite link"),
        S("dashboard", "invite_email", "Email invitation links",
          "Send each invitation link by email when an email account is set up. Off: copy the "
          "link and send it yourself.",
          "toggle", grp, True, "maverick_dashboard.invites", subgroup=invites,
          aliases="invite email"),
        S("dashboard", "invite_session_days", "Stay signed in after an invitation",
          "How long people who signed in with an invitation stay signed in.",
          "number", grp, 30, "maverick_dashboard.invites", subgroup=invites,
          minimum=1, maximum=365, integer=True, unit="days", weaker="higher",
          warning="People will stay signed in longer.", aliases="session length"),
        S("dashboard", "session_secret", "Invitation session key",
          "The key that keeps invited people and the owner signed in. Lightwork makes one "
          "itself; set your own only to share it between servers. It is stored sealed and "
          "never shown again.",
          "secret", grp, "", "maverick_dashboard.invites", subgroup=invites,
          pattern=_SECRET32, max_length=512, env=("MAVERICK_DASHBOARD_SESSION_SECRET",),
          weaker="change",
          warning="Everyone signed in with an invitation or the owner account will be signed "
                  "out.",
          aliases="session secret"),
        S("dashboard", "scim_token", "Password for automatic account updates",
          "The password your sign-in provider uses to add, change and remove people in "
          "Lightwork automatically. Several can be listed, separated by commas, while you "
          "change it. It is stored sealed and never shown again.",
          "secret", grp, "", "maverick_dashboard.scim", subgroup=access, max_length=2000,
          env=("MAVERICK_SCIM_TOKEN",), weaker="change",
          warning="Your sign-in provider will be able to add, change and remove people with "
                  "this password.",
          aliases="scim provisioning token"),
        S("dashboard", "max_goals_per_min", "New tasks per person each minute",
          "The most tasks one person or program can start in a minute from the dashboard.",
          "number", grp, 30, "maverick_dashboard.app", subgroup=access, minimum=1,
          maximum=10000, integer=True, unit="per minute",
          env=("MAVERICK_DASHBOARD_MAX_GOALS_PER_MIN",), weaker="higher",
          warning="One person will be able to start many more paid tasks at once.",
          aliases="rate limit goals"),
        S("dashboard", "max_goals_global_per_min", "New tasks for everyone each minute",
          "The most tasks everyone together can start in a minute from the dashboard.",
          "number", grp, None, "maverick_dashboard.app", subgroup=access, minimum=1,
          maximum=100000, integer=True, unit="per minute",
          env=("MAVERICK_DASHBOARD_MAX_GOALS_GLOBAL_PER_MIN",), weaker="higher",
          warning="Many more paid tasks will be able to start at once.",
          none_text="Ten times the limit above", aliases="rate limit goals"),
        S("dashboard", "max_sse", "Live pages open at once",
          "How many pages can follow live updates at the same time; more wait.",
          "number", grp, 64, "maverick_dashboard._shared", subgroup=access, minimum=1,
          maximum=10000, integer=True, unit="pages", restart=True,
          env=("MAVERICK_DASHBOARD_MAX_SSE",), aliases="live updates streams"),
        S("dashboard", "shutdown_timeout_seconds", "Time to finish work when stopping",
          "When Lightwork stops, how long it waits for running work to finish.",
          "number", grp, 20, "maverick_dashboard._lifecycle", subgroup=access, minimum=0,
          maximum=300, unit="seconds",
          env=("MAVERICK_SHUTDOWN_TIMEOUT", "MAVERICK_DRAIN_TIMEOUT"),
          aliases="shutdown drain"),
        # ---------------------------------------------------- workspaces
        S("tenancy", "by_user", "A separate workspace for each chat user",
          "Keep each person who writes to Lightwork from a chat app in their own workspace, so "
          "nobody's memory is shared with anyone else.",
          "toggle", grp, False, "maverick.paths", subgroup="Workspaces",
          env=("MAVERICK_TENANT_BY_USER",), weaker=False,
          warning="People writing from chat apps will share one workspace and its memory.",
          aliases="per user isolation workspace"),
        S("tenancy", "egress", "Outside services each workspace may reach",
          "For a workspace, the outside services its agents may reach (allowed) and never reach "
          "(blocked). A workspace not listed uses the installation's own rules.",
          "table", grp, {}, "maverick.tenant.egress", subgroup="Workspaces",
          row_kind="grid", choices_from="workspaces",
          columns=(),  # filled in entries(): Column is only available there
          row_label="Workspace", item_label="Workspace", add_label="Add a workspace",
          empty_text="Every workspace uses the installation's own rules.",
          empty_hint="There are no workspaces yet.",
          weaker="change", warning="Agents in that workspace may reach different outside "
                                   "services.",
          aliases="egress network allow block"),
        S("client", "enforce", "Require a client name",
          "Refuse to run without the client name this installation was set up with, and never "
          "trust this computer without sign-in.",
          "toggle", grp, False, "maverick.client", subgroup="Workspaces",
          env=("MAVERICK_CLIENT_ENFORCE",), weaker=False,
          warning="Records may be kept without a client name, and this computer may be "
                  "trusted without sign-in again.",
          aliases="client binding enforce"),
        S("sharing", "secret", "Share link key",
          "The key that signs links for sharing a record with someone outside Lightwork. "
          "Setting it turns share links on. Use at least 32 characters. It is stored sealed "
          "and never shown again.",
          "secret", grp, "", "maverick.share_link", subgroup="Sharing", pattern=_SECRET32,
          max_length=512, env=("MAVERICK_SHARE_SECRET",), weaker="change",
          warning="Share links will work with this key, and every link made with the old key "
                  "will stop working.",
          aliases="share links secret"),
        S("role_assignments", "default", "Agent permissions for everyone else",
          "The permission set every person and agent without one of their own works under. "
          "Reset to default to use none.",
          "select", grp, "", "maverick.capability", subgroup="Sharing",
          choices_from="capability_roles", weaker="change",
          warning="Everyone without their own permission set will work under this one.",
          aliases="role assignments default permission"),
    )


def _security(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp = "Security"
    keys, custody, worm = "Key service", "Activity record custody", "Tamper-proof storage"
    return (
        S("encryption", "per_tenant", "A separate key for each workspace",
          "Lock each workspace's new records with its own key, so one workspace's key never "
          "opens another's data. Records locked before stay readable.",
          "toggle", grp, False, "maverick.crypto_at_rest", subgroup="Encryption",
          env=("MAVERICK_ENCRYPT_PER_TENANT",), weaker=False,
          warning="New records will be locked with one key for every workspace.",
          aliases="per workspace encryption key"),
        S("encryption", "strict", "Refuse records that were never locked",
          "Treat a stored value that should be locked but is not as tampered with. Turn on only "
          "after older records were locked, or they will be withheld.",
          "toggle", grp, False, "maverick.crypto_at_rest", subgroup="Encryption",
          env=("MAVERICK_ENCRYPT_STRICT",), weaker=False,
          warning="Values that were never locked will be trusted again.",
          aliases="strict encryption"),
        S("kms", "provider", "Where data keys are locked",
          "The service that locks the keys for each workspace's data. Test connection checks "
          "the service can lock and unlock a test value and still open the keys already in use.",
          "select", grp, "local", "maverick.tenant.kms", subgroup=keys,
          choices=(("local", "On this server"), ("aws", "Amazon key service"),
                   ("gcp", "Google Cloud key service"), ("vault", "HashiCorp Vault")),
          restart=True, test="kms", weaker="change",
          warning="Lightwork will lock and unlock its data keys with a different service.",
          aliases="kms byok key management"),
        S("kms", "key_id", "Key name at the key service",
          "The full name of the key at your key service.",
          "text", grp, "", "maverick.kms_backends", subgroup=keys, max_length=300,
          pattern=r"[A-Za-z0-9:/._-]{1,300}", restart=True, test="kms",
          env=("MAVERICK_KMS_KEY_ID",), aliases="key id arn"),
        S("kms", "region", "Key service region",
          "For the Amazon key service: the region the key is in, such as us-east-1.",
          "text", grp, "", "maverick.kms_backends", subgroup=keys, pattern=_REGION,
          restart=True, test="kms", aliases="region"),
        S("kms", "address", "Vault address",
          "For HashiCorp Vault: the web address of your Vault server.",
          "text", grp, "", "maverick.kms_backends", subgroup=keys, pattern=_HTTPS,
          max_length=300, restart=True, test="kms", aliases="vault address"),
        S("kms", "mount", "Vault key area",
          "For HashiCorp Vault: the name of the area that holds the key.",
          "text", grp, "transit", "maverick.kms_backends", subgroup=keys, pattern=_NAME,
          restart=True, test="kms", aliases="vault mount transit"),
        S("kms", "dek_cache_ttl", "Remember unlocked keys for",
          "How long an unlocked data key is remembered before the key service is asked again. "
          "0 keeps it until Lightwork restarts; a key switched off at the service keeps opening "
          "data until then.",
          "number", grp, 0, "maverick.tenant.kms", subgroup=keys, minimum=0, maximum=86400,
          integer=True, unit="seconds", env=("MAVERICK_KMS_DEK_CACHE_TTL",), weaker="change",
          warning="A key switched off at the key service will keep opening data for a "
                  "different length of time.",
          aliases="cache time"),
        S("audit", "require_offhost_key", "Sign the record with a key kept off this server",
          "Refuse to write the activity record unless it is signed with a key that is not kept "
          "on this server. Set that key up first.",
          "toggle", grp, False, "maverick.audit.signing", subgroup=custody,
          env=("MAVERICK_AUDIT_REQUIRE_OFFHOST_KEY",), weaker=False,
          warning="The activity record may be signed with a key kept on this server.",
          aliases="off host signing key"),
        S("audit", "siem_dest", "Send the activity record to",
          "Your security monitoring service's address, starting with https://, tcp:// or "
          "udp://.",
          "text", grp, "", "maverick.cli", subgroup=custody, pattern=_SIEM,
          max_length=300, test="siem", env=("MAVERICK_SIEM_DEST",), weaker="change",
          warning="Activity records will be sent to a different place.",
          aliases="siem splunk syslog forward"),
        S("audit", "siem_token", "Security monitoring access key",
          "The access key sent with the activity record to a web address. Stored sealed and "
          "never shown again.",
          "secret", grp, "", "maverick.audit.forwarder", subgroup=custody, max_length=1000,
          env=("MAVERICK_SIEM_TOKEN",), aliases="siem token hec"),
        S("audit", "siem_allow_insecure", "Allow unencrypted sending",
          "Let the activity record be sent without encryption, only on a trusted private "
          "network.",
          "toggle", grp, False, "maverick.audit.forwarder", subgroup=custody,
          env=("MAVERICK_SIEM_ALLOW_INSECURE",), weaker=True,
          warning="Activity records could be read by anyone on the network path.",
          aliases="insecure plaintext"),
        S("audit.unified", "enable", "Shared activity record",
          "Collect every workspace's activity into one signed record with its own reading "
          "position. Needs the signed activity record.",
          "toggle", grp, False, "maverick.unified_audit", subgroup=custody, weaker=False,
          warning="The shared activity record will stop being kept.", aliases="unified audit"),
        S("audit.unified", "page_size", "Entries read at a time",
          "How many activity entries the shared record reads in one step.",
          "number", grp, 100, "maverick.unified_audit", subgroup=custody, minimum=1,
          maximum=500, integer=True, unit="entries"),
        S("audit.unified", "lease_seconds", "Time to finish one step",
          "How long one worker may hold a step of the shared record before another may take "
          "it over.",
          "number", grp, 120, "maverick.unified_audit", subgroup=custody, minimum=30,
          maximum=86400, integer=True, unit="seconds"),
        S("audit.unified", "max_lag_seconds", "Allowed delay",
          "How far behind the shared record may fall before it is reported as late.",
          "number", grp, 300, "maverick.unified_audit", subgroup=custody, minimum=1,
          maximum=31536000, integer=True, unit="seconds", weaker="higher",
          warning="The shared record may fall further behind before anyone is told."),
        S("audit.unified", "max_attempts", "Tries before giving up",
          "How many times a failed step is tried again.",
          "number", grp, 5, "maverick.unified_audit", subgroup=custody, minimum=1,
          maximum=20, integer=True, unit="tries"),
        S("audit.unified", "retry_backoff_seconds", "Wait between tries",
          "How long to wait before trying a failed step again.",
          "number", grp, 30, "maverick.unified_audit", subgroup=custody, minimum=0,
          maximum=86400, integer=True, unit="seconds"),
        S("audit.unified", "poll_seconds", "Check for new activity every",
          "How often the shared record looks for new activity.",
          "number", grp, 60, "maverick.unified_audit", subgroup=custody, minimum=5,
          maximum=3600, integer=True, unit="seconds"),
        S("audit.unified.custody", "enable", "Keep custody copies",
          "Keep independent copies of the shared record, as chosen below, so any later change "
          "can be proven.",
          "toggle", grp, False, "maverick.unified_audit_custody", subgroup=custody,
          weaker=False, warning="Independent custody copies will stop being made.",
          aliases="custody"),
        S("audit.unified.custody", "checkpoint_enable", "Publish signed checkpoints",
          "Publish a signed summary of the record at each step.",
          "toggle", grp, True, "maverick.unified_audit_custody", subgroup=custody,
          weaker=False, warning="Signed checkpoints will stop being published."),
        S("audit.unified.custody", "worm_enable", "Copy to tamper-proof storage",
          "Copy the record to the tamper-proof storage set below.",
          "toggle", grp, False, "maverick.unified_audit_custody", subgroup=custody,
          weaker=False, warning="Copies will stop going to tamper-proof storage."),
        S("audit.unified.custody", "authority_revision", "Custody setup version",
          "Raise this by one after deliberately changing the custody setup, so Lightwork "
          "accepts the new setup instead of treating it as tampering.",
          "number", grp, 1, "maverick.unified_audit_custody", subgroup=custody, minimum=1,
          maximum=2147483647, integer=True, weaker="change",
          warning="Lightwork will accept a changed custody setup as genuine.",
          aliases="authority revision"),
        S("audit.unified.custody", "max_siem_events_per_run", "Entries sent per step",
          "The most activity entries sent to your security monitoring service in one step.",
          "number", grp, 500, "maverick.unified_audit_custody", subgroup=custody, minimum=1,
          maximum=5000, integer=True, unit="entries"),
        S("audit.unified.custody", "retry_backoff_seconds", "First wait after a failed copy",
          "How long to wait before trying a failed custody copy again.",
          "number", grp, 30, "maverick.unified_audit_custody", subgroup=custody, minimum=1,
          maximum=86400, integer=True, unit="seconds"),
        S("audit.unified.custody", "max_retry_backoff_seconds", "Longest wait after failures",
          "The longest wait between tries after repeated failures.",
          "number", grp, 3600, "maverick.unified_audit_custody", subgroup=custody, minimum=1,
          maximum=604800, integer=True, unit="seconds", weaker="higher",
          warning="Failed custody copies may be retried less often."),
        S("residency", "allowed_regions", "Regions data may be stored in",
          "The only regions Lightwork may keep data in, such as EU or DE. Groups such as EU "
          "include every member country. Empty: any region.",
          "list", grp, [], "maverick.residency", pattern=r"[A-Za-z]{2,3}",
          item_label="Region", item_hint="EU", item_error="a region or country code",
          env=("MAVERICK_RESIDENCY_ALLOWED",), weaker="widen",
          warning="Data will be allowed in more regions.", aliases="residency sovereignty"),
        S("audit.worm", "provider", "Tamper-proof storage",
          "Where copies of the activity record are kept so they cannot be changed or deleted "
          "until they expire.",
          "select", grp, "", "maverick.audit.worm", subgroup=worm,
          choices=(("", "Off"), ("local", "Read-only files on this server"),
                   ("s3", "Amazon S3 with object lock")),
          weaker="change",
          warning="Copies will be kept somewhere else, or no longer kept.",
          aliases="worm immutable object lock"),
        S("audit.worm", "dir", "Folder for read-only copies",
          "For read-only files on this server: the folder to keep them in. Leave empty to use "
          "Lightwork's own folder.",
          "path", grp, "", "maverick.audit.worm", subgroup=worm, path_kind="folder",
          path_writable=True, max_length=400),
        S("audit.worm", "bucket", "Storage bucket",
          "For Amazon S3: the bucket that has object lock turned on.",
          "text", grp, "", "maverick.audit.worm", subgroup=worm, pattern=_BUCKET,
          max_length=63, aliases="bucket s3"),
        S("audit.worm", "prefix", "Folder inside the bucket",
          "For Amazon S3: the folder name inside the bucket, such as lightwork/audit/.",
          "text", grp, "", "maverick.audit.worm", subgroup=worm, pattern=_PREFIX,
          aliases="prefix"),
        S("audit.worm", "region", "Storage region",
          "For Amazon S3: the region the bucket is in, such as us-east-1.",
          "text", grp, "", "maverick.audit.worm", subgroup=worm, pattern=_REGION),
        S("audit.worm", "mode", "Lock strength",
          "Compliance: nobody can remove a copy early. Governance: specially permitted accounts "
          "can.",
          "select", grp, "COMPLIANCE", "maverick.audit.worm", subgroup=worm,
          choices=(("COMPLIANCE", "Compliance: nobody can remove a copy early"),
                   ("GOVERNANCE", "Governance: permitted accounts can remove copies")),
          weaker=True, order=("COMPLIANCE", "GOVERNANCE"),
          warning="Specially permitted accounts will be able to remove copies early."),
        S("audit.worm", "retention_days", "Keep copies for",
          "How long each copy cannot be changed or deleted.",
          "number", grp, 2555, "maverick.audit.worm", subgroup=worm, minimum=1,
          maximum=36500, integer=True, unit="days", weaker="lower",
          warning="Copies will be removable sooner."),
    )


def _other_agents(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp, sub = "Security", "Requests from other agents"
    tool = r"[A-Za-z0-9_.*-]{1,128}"
    return (
        S("a2a", "token", "Password other agents must send",
          "Other agents must send this password with each request. Stored sealed and never "
          "shown again.",
          "secret", grp, "", "maverick.a2a_tasks", subgroup=sub, pattern=_SECRET32,
          max_length=512, env=("MAVERICK_A2A_TOKEN",), weaker="change",
          warning="Agents that know this password will be able to send Lightwork work.",
          aliases="a2a token bearer"),
        S("a2a", "allow_unauthenticated", "Accept requests without a password",
          "Only for a trusted network: accept work from other agents that send no password, "
          "when no password is set.",
          "toggle", grp, False, "maverick.a2a_tasks", subgroup=sub,
          env=("MAVERICK_A2A_ALLOW_UNAUTHENTICATED",), weaker=True,
          warning="Any agent that can reach Lightwork will be able to send it work.",
          aliases="unauthenticated"),
        S("a2a", "allowed_origins", "Web pages allowed to send requests",
          "Web page addresses whose scripts may send requests to Lightwork's agent service.",
          "list", grp, [], "maverick.a2a", subgroup=sub, pattern=_HTTPS_OR_LOCAL,
          item_label="Address", item_hint="https://app.example.com",
          item_error="a web address", env=("MAVERICK_A2A_ALLOWED_ORIGINS",), weaker="grow",
          warning="More web pages will be able to send Lightwork requests.",
          aliases="origins cors"),
        S("a2a", "base_url", "Address other agents use",
          "The address Lightwork tells other agents to use for this service.",
          "text", grp, "", "maverick.a2a", subgroup=sub, pattern=_HTTPS_OR_LOCAL,
          max_length=300, env=("MAVERICK_A2A_BASE_URL",), aliases="base url"),
        S("a2a", "tools", "Tools other agents' tasks may use",
          "When filled in, tasks from other agents may use only these tools. Empty: any tool "
          "the other limits allow.",
          "list", grp, [], "maverick.a2a_tasks", subgroup=sub, pattern=tool,
          item_label="Tool", item_hint="web_search", item_error="a tool name",
          weaker="widen", warning="Tasks from other agents will be able to use more tools.",
          aliases="allowed tools"),
        S("a2a", "deny_tools", "Tools other agents' tasks may never use",
          "Tasks from other agents may never use these tools.",
          "list", grp, [], "maverick.a2a_tasks", subgroup=sub, pattern=tool,
          item_label="Tool", item_hint="shell", item_error="a tool name",
          weaker="shrink", warning="Tasks from other agents will be able to use a tool that "
                                   "was blocked.",
          aliases="blocked tools"),
        S("a2a", "max_dollars", "Spending limit per task from another agent",
          "The most one task from another agent may spend, whatever it asks for.",
          "number", grp, 5.0, "maverick.a2a_tasks", subgroup=sub, minimum=0,
          maximum=100000, unit="dollars", env=("MAVERICK_A2A_MAX_DOLLARS",),
          weaker="higher", warning="Tasks from other agents will be able to spend more."),
        S("a2a", "max_wall_seconds", "Time limit per task from another agent",
          "The longest one task from another agent may run.",
          "number", grp, 3600.0, "maverick.a2a_tasks", subgroup=sub, minimum=1,
          maximum=604800, unit="seconds", env=("MAVERICK_A2A_MAX_WALL_SECONDS",),
          weaker="higher", warning="Tasks from other agents will be able to run longer."),
        S("a2a", "max_depth", "Sub-task levels for another agent's task",
          "How many levels of sub-tasks a task from another agent may start.",
          "number", grp, 3, "maverick.a2a_tasks", subgroup=sub, minimum=1, maximum=64,
          integer=True, unit="levels", env=("MAVERICK_A2A_MAX_DEPTH",), weaker="higher",
          warning="Tasks from other agents will be able to start more sub-tasks."),
        S("a2a", "max_concurrency", "Tasks from other agents at once",
          "How many tasks from other agents may run at the same time. 0 removes the limit.",
          "number", grp, 4, "maverick.a2a_tasks", subgroup=sub, minimum=0, maximum=256,
          integer=True, unit="tasks", env=("MAVERICK_A2A_MAX_CONCURRENCY",),
          aliases="concurrency"),
        S("a2a", "max_tasks", "Tasks from other agents kept track of",
          "How many tasks from other agents Lightwork keeps track of at once.",
          "number", grp, 1000, "maverick.a2a_tasks", subgroup=sub, minimum=16,
          maximum=100000, integer=True, unit="tasks", restart=True,
          env=("MAVERICK_A2A_MAX_TASKS",)),
    )


def _plumbing(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp = "Server & deployment"
    store, queue, grpc, fed = ("Record store", "Shared work queue", "Remote workers",
                               "Linked Lightwork servers")
    return (
        S("world_model", "connect_timeout_seconds", "Wait to reach the database",
          "How long Lightwork waits to connect to the shared PostgreSQL database.",
          "number", grp, 10, "maverick.db_timeouts", subgroup=store, minimum=2, maximum=300,
          integer=True, unit="seconds", env=("MAVERICK_PG_CONNECT_TIMEOUT_SECONDS",)),
        S("world_model", "io_timeout_seconds", "Wait for a database reply",
          "How long Lightwork waits for the shared database to answer before treating the "
          "step as failed.",
          "number", grp, 35.0, "maverick.postgres_io", subgroup=store, minimum=0.1,
          maximum=3600, unit="seconds", env=("MAVERICK_PG_IO_TIMEOUT_SECONDS",)),
        S("world_model", "statement_timeout_ms", "Longest database request",
          "The longest one request to the shared database may run. 0 removes the limit.",
          "number", grp, 30000, "maverick.db_timeouts", subgroup=store, minimum=0,
          maximum=86400, integer=True, unit="seconds", scale=1000,
          env=("MAVERICK_PG_STATEMENT_TIMEOUT_MS",), weaker="higher",
          warning="A runaway database request will be able to run longer."),
        S("world_model", "idle_tx_timeout_ms", "Longest pause inside a database change",
          "How long a half-finished database change may sit idle before it is cancelled. 0 "
          "removes the limit.",
          "number", grp, 60000, "maverick.db_timeouts", subgroup=store, minimum=0,
          maximum=86400, integer=True, unit="seconds", scale=1000,
          env=("MAVERICK_PG_IDLE_TX_TIMEOUT_MS",), weaker="higher",
          warning="A stuck database change will be able to hold records longer."),
        S("world_model", "orphan_reclaim_seconds", "Treat stopped tasks as stuck after",
          "After a restart, tasks untouched for this long are marked as stuck so they can be "
          "looked at.",
          "number", grp, 60.0, "maverick.world_model", subgroup=store, minimum=0,
          maximum=86400, unit="seconds", env=("MAVERICK_ORPHAN_RECLAIM_SECONDS",),
          aliases="orphan reclaim"),
        S("queue", "goal_acquire_timeout_seconds", "Wait for a free task slot",
          "How long a worker waits for a free task slot before putting the task back in the "
          "queue.",
          "number", grp, 5.0, "maverick.worker", subgroup=queue, minimum=0, maximum=3600,
          unit="seconds", env=("MAVERICK_QUEUE_GOAL_ACQUIRE_TIMEOUT",)),
        S("grpc", "token", "Worker service password",
          "The password callers must send to this server's worker service. Stored sealed "
          "and never shown again.",
          "secret", grp, "", "maverick.grpc_api.server", subgroup=grpc, pattern=_SECRET32,
          max_length=512, restart=True, env=("MAVERICK_GRPC_BEARER_TOKEN",),
          weaker="change", warning="Callers using the old password will be refused.",
          aliases="bearer token"),
        S("grpc", "rate_limit", "Worker service requests per caller each minute",
          "How many requests one caller may send the worker service in a minute. 0 removes "
          "the limit.",
          "number", grp, 600, "maverick.grpc_api.server", subgroup=grpc, minimum=0,
          maximum=100000, integer=True, unit="per minute", env=("MAVERICK_GRPC_RATE_LIMIT",),
          weaker="higher", warning="Callers will be able to send more requests."),
        S("federation", "rate_limit", "Requests per linked server each minute",
          "How many requests one linked server may send in a minute. 0 removes the limit.",
          "number", grp, 60, "maverick.federation", subgroup=fed, minimum=0, maximum=100000,
          integer=True, unit="per minute", env=("MAVERICK_FEDERATION_RATE_LIMIT",),
          weaker="higher", warning="Linked servers will be able to send more requests."),
    )


def _journal(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp = "Data retention"
    return (
        S("workspace", "journal_strict", "Stop a change when its record fails",
          "When a workspace change can't be written to the change record, refuse the change "
          "instead of only warning.",
          "toggle", grp, False, "maverick.workspace_journal",
          env=("MAVERICK_WORKSPACE_JOURNAL_STRICT",), weaker=False,
          warning="Workspace changes will go ahead even when they can't be recorded.",
          aliases="journal strict"),
        S("workspace", "journal_max_file_mb", "Largest change record file",
          "Change records larger than this are split into a new file.",
          "number", grp, 8, "maverick.workspace_journal", minimum=1, maximum=4096,
          integer=True, unit="MB", env=("MAVERICK_WORKSPACE_JOURNAL_MAX_FILE_MB",)),
        S("workspace", "journal_max_total_mb", "Space for change records",
          "The most disk space all workspace change records may use.",
          "number", grp, 256, "maverick.workspace_journal", minimum=1, maximum=1048576,
          integer=True, unit="MB"),
    )


def _server(S) -> tuple:  # noqa: N803 -- the catalog's row type
    grp = "Server & deployment"
    store, queue, grpc = "Record store", "Shared work queue", "Remote workers"
    fed, lic, ent = "Linked Lightwork servers", "License", "Enterprise boundary"
    return (
        S("governed_records", "backend", "Where governed records are kept",
          "Governed records (approvals, receipts and other records that need an owner) can be "
          "kept on this server or in the shared PostgreSQL database. Automatic uses the shared "
          "database whenever several servers run together.",
          "select", grp, "auto", "maverick.governed_records", subgroup=store,
          choices=(("auto", "Automatic (recommended)"), ("local", "On this server"),
                   ("postgres", "In the shared PostgreSQL database")),
          restart=True, env=("MAVERICK_GOVERNED_RECORDS_BACKEND",), weaker="change",
          warning="Records kept in the other place will not be shown until you switch back.",
          aliases="governed records postgres database"),
        S("governed_records", "pool_size", "Database connections for governed records",
          "How many connections to keep open to the shared database. 0 opens one when needed.",
          "number", grp, 0, "maverick.governed_records", subgroup=store, minimum=0,
          maximum=32, integer=True, unit="connections", restart=True),
        S("governed_records", "pool_timeout_seconds", "Wait for a free connection",
          "How long to wait for a free database connection before giving up.",
          "number", grp, 5, "maverick.governed_records", subgroup=store, minimum=0.1,
          maximum=60, unit="seconds", restart=True),
        S("world_model", "backend", "Where Lightwork's records are kept",
          "Lightwork's main records (tasks, results and memory) live in a file on this server "
          "or in a shared PostgreSQL database for several servers.",
          "select", grp, "sqlite", "maverick.world_model_backends.postgres", subgroup=store,
          choices=(("sqlite", "In a file on this server"),
                   ("postgres", "In a shared PostgreSQL database")),
          restart=True, env=("MAVERICK_WORLD_BACKEND",), weaker="change",
          warning="Records kept in the other place will not be shown until you switch back.",
          aliases="world model database postgres"),
        S("world_model", "dsn", "Shared database connection",
          "How to reach the shared PostgreSQL database, including its password if it needs "
          "one. It is stored sealed and never shown again.",
          "secret", grp, "", "maverick.world_model_backends.postgres", subgroup=store,
          max_length=1000, restart=True, test="server", env=("MAVERICK_PG_DSN",),
          weaker="change",
          warning="Lightwork will use a different database, and records in the old one will "
                  "not be shown.",
          aliases="dsn connection string postgres"),
        S("world_model", "pool_size", "Database connections for records",
          "How many connections to keep open to the shared database. 0 shares one connection.",
          "number", grp, 0, "maverick.world_model_backends.postgres", subgroup=store,
          minimum=0, maximum=256, integer=True, unit="connections", restart=True,
          env=("MAVERICK_PG_POOL_SIZE",)),
        S("world_model", "rls", "Database-enforced workspace separation",
          "Have the shared database itself keep each workspace's records apart, as a second "
          "wall behind Lightwork's own checks.",
          "toggle", grp, False, "maverick.world_model_backends.postgres", subgroup=store,
          restart=True, env=("MAVERICK_PG_RLS",), weaker=False,
          warning="Only Lightwork's own checks will keep workspaces apart in the database.",
          aliases="row level security rls"),
        S("world_model", "strict_tenant_isolation", "Hide records with no workspace",
          "In the shared database, show each workspace only its own records, never older "
          "records that have no workspace. Turn on after older records were given one.",
          "toggle", grp, False, "maverick.world_model_backends.postgres", subgroup=store,
          restart=True, env=("MAVERICK_STRICT_TENANT_ISOLATION",), weaker=False,
          warning="Older records with no workspace will be visible to every workspace.",
          aliases="strict isolation"),
        S("world_model", "synchronous", "Save every change to disk immediately",
          "How carefully each saved change is written. Safest survives a power cut with no "
          "loss, at some cost in speed.",
          "select", grp, "NORMAL", "maverick.world_model", subgroup=store,
          choices=(("EXTRA", "Safest"), ("FULL", "Safe"),
                   ("NORMAL", "Standard (the last few changes can be lost in a power cut)")),
          restart=True, env=("MAVERICK_WORLD_SYNCHRONOUS",), weaker=True,
          order=("EXTRA", "FULL", "NORMAL"),
          warning="A power cut could lose the last few saved changes.",
          aliases="durability synchronous"),
        S("world_model", "pre_migration_backup", "Back up records before upgrades",
          "Copy the records file before an upgrade changes its layout.",
          "toggle", grp, True, "maverick.world_model", subgroup=store, weaker=False,
          warning="Upgrades will change the records file without a copy to go back to.",
          aliases="backup migration"),
        S("world_model", "archive_after_days", "Move old records to the archive after",
          "When the archive runs, records older than this move to the archive folder below. "
          "Leave empty to never archive.",
          "number", grp, None, "maverick.tiered_storage", subgroup=store, minimum=1,
          maximum=36500, integer=True, unit="days",
          env=("MAVERICK_WORLD_ARCHIVE_AFTER_DAYS",), none_text="Never",
          aliases="archive cold storage"),
        S("world_model", "cold_dir", "Archive folder",
          "The folder on the server that archived records move to.",
          "path", grp, "", "maverick.tiered_storage", subgroup=store, path_kind="folder",
          path_writable=True, max_length=400, env=("MAVERICK_WORLD_COLD_DIR",),
          aliases="archive folder"),
        S("world_model", "cold_codec", "Archive file type",
          "How archived records are packed.",
          "select", grp, "auto", "maverick.tiered_storage", subgroup=store,
          choices=(("auto", "Automatic"), ("parquet", "Column files"),
                   ("zstd", "Compressed (fast)"), ("gzip", "Compressed (widely readable)")),
          env=("MAVERICK_WORLD_COLD_CODEC",)),
        S("workspace", "snapshot_dir", "Folder for workspace snapshots",
          "Where saved copies of agent workspaces are kept. Leave empty to use Lightwork's own "
          "folder.",
          "path", grp, "", "maverick.workspace_snapshot", subgroup=store, path_kind="folder",
          path_writable=True, max_length=400, aliases="snapshots"),
        # ------------------------------------------------------- queue
        S("queue", "backend", "How tasks are handed to workers",
          "Run tasks inside this server, or hand them to worker servers through a shared "
          "queue. The shared queue needs the queue address, the queue key, a queue name and "
          "the shared PostgreSQL database.",
          "select", grp, "", "maverick.queue_dispatcher", subgroup=queue,
          choices=(("", "Inside this server"), ("arq", "Through a shared queue")),
          restart=True, weaker="change",
          warning="Tasks will be run somewhere else.", aliases="queue redis workers"),
        S("queue", "redis_dsn", "Queue address",
          "How to reach the shared queue, including its password if it needs one. Addresses "
          "on other computers must use rediss:// so the connection is encrypted. Stored sealed.",
          "secret", grp, "", "maverick.queue_dispatcher", subgroup=queue, pattern=_REDIS,
          max_length=500, restart=True, test="server", env=("MAVERICK_QUEUE_REDIS_DSN",),
          weaker="change", warning="Tasks will go through a different queue.",
          aliases="redis address"),
        S("queue", "signing_key", "Queue key",
          "The key every server uses to prove a queued task is genuine. Use at least 32 "
          "characters, the same on every server. Stored sealed and never shown again.",
          "secret", grp, "", "maverick.queue_dispatcher", subgroup=queue, pattern=_SECRET32,
          max_length=512, restart=True, env=("MAVERICK_QUEUE_SIGNING_KEY",),
          weaker="change", warning="Servers still using the old key will stop taking tasks.",
          aliases="signing key"),
        S("queue", "namespace", "Queue name",
          "A name shared by this server and its workers, different for every group of servers.",
          "text", grp, "", "maverick.queue_dispatcher", subgroup=queue, pattern=_NAME,
          restart=True, env=("MAVERICK_QUEUE_NAMESPACE",), aliases="namespace"),
        S("queue", "envelope_ttl_seconds", "Queued task expires after",
          "A queued task that is not picked up in this time is refused.",
          "number", grp, 900, "maverick.queue_dispatcher", subgroup=queue, minimum=1,
          maximum=86400, integer=True, unit="seconds",
          env=("MAVERICK_QUEUE_ENVELOPE_TTL_SECONDS",), weaker="higher",
          warning="Old queued tasks will be accepted for longer."),
        S("queue", "worker_max_depth", "Worker task depth limit",
          "How many levels of sub-tasks a worker allows for one queued task.",
          "number", grp, 3, "maverick.queue_dispatcher", subgroup=queue, minimum=1,
          maximum=64, integer=True, unit="levels", env=("MAVERICK_QUEUE_WORKER_MAX_DEPTH",),
          weaker="higher", warning="Queued tasks will be able to start more sub-tasks."),
        S("queue", "worker_max_dollars", "Worker spending limit per task",
          "The most a worker spends on one queued task, whatever the task asks for. Leave "
          "empty to use the per-task spending cap.",
          "number", grp, None, "maverick.queue_dispatcher", subgroup=queue, minimum=0,
          maximum=100000, unit="dollars", env=("MAVERICK_QUEUE_WORKER_MAX_DOLLARS",),
          weaker="higher", warning="Workers will be able to spend more on one task.",
          none_text="The per-task spending cap"),
        S("queue", "worker_max_wall_seconds", "Worker time limit per task",
          "The longest a worker spends on one queued task. Leave empty to use the per-task "
          "time limit.",
          "number", grp, None, "maverick.queue_dispatcher", subgroup=queue, minimum=1,
          maximum=604800, integer=True, unit="seconds",
          env=("MAVERICK_QUEUE_WORKER_MAX_WALL_SECONDS",), weaker="higher",
          warning="Workers will be able to spend longer on one task.",
          none_text="The per-task time limit"),
        S("queue", "redis_ca_certs", "Queue certificate authority file",
          "Optional: the certificate file of the authority that signed the queue's "
          "certificate, when it is your company's own.",
          "path", grp, "", "maverick.queue_dispatcher", subgroup=queue, path_kind="file",
          max_length=400, restart=True, env=("MAVERICK_QUEUE_REDIS_CA_CERTS",)),
        S("queue", "redis_certfile", "Queue client certificate file",
          "Optional: the certificate this server shows the queue.",
          "path", grp, "", "maverick.queue_dispatcher", subgroup=queue, path_kind="file",
          max_length=400, restart=True, env=("MAVERICK_QUEUE_REDIS_CERTFILE",)),
        S("queue", "redis_keyfile", "Queue client private key file",
          "Optional: the private key for that certificate. Only Lightwork may be able to read "
          "it.",
          "path", grp, "", "maverick.queue_dispatcher", subgroup=queue, path_kind="file",
          path_private=True, max_length=400, restart=True,
          env=("MAVERICK_QUEUE_REDIS_KEYFILE",)),
        # ------------------------------------------------ remote workers
        S("grpc_dispatch", "target", "Remote worker address",
          "Run tasks on a remote Lightwork worker at this address (name and port). Leave empty "
          "to run tasks here.",
          "text", grp, "", "maverick.grpc_dispatcher", subgroup=grpc, pattern=_HOST_PORT,
          max_length=260, restart=True, test="server",
          env=("MAVERICK_GRPC_DISPATCH_TARGET",), weaker="change",
          warning="Tasks will run on a different computer.", aliases="grpc dispatch worker"),
        S("grpc_dispatch", "token", "Remote worker password",
          "The password the remote worker expects. Stored sealed and never shown again.",
          "secret", grp, "", "maverick.grpc_dispatcher", subgroup=grpc, max_length=512,
          restart=True, aliases="token bearer"),
        S("grpc_dispatch", "timeout_s", "Wait for a remote task",
          "How long to wait for a remote worker to finish a task. 0 waits as long as the task "
          "runs.",
          "number", grp, 0, "maverick.grpc_dispatcher", subgroup=grpc, minimum=0,
          maximum=86400, unit="seconds", restart=True),
        S("grpc", "tls", "Encrypt connections to workers",
          "Encrypt the connections this server's worker service accepts and makes.",
          "toggle", grp, False, "maverick.grpc_tls", subgroup=grpc, restart=True,
          weaker=False, warning="Connections to workers will not be encrypted.",
          aliases="tls encryption"),
        S("grpc", "tls_cert", "Worker service certificate file",
          "The certificate the worker service shows.", "path", grp, "", "maverick.grpc_tls",
          subgroup=grpc, path_kind="file", max_length=400, restart=True),
        S("grpc", "tls_key", "Worker service private key file",
          "The private key for that certificate. Only Lightwork may be able to read it.",
          "path", grp, "", "maverick.grpc_tls", subgroup=grpc, path_kind="file",
          path_private=True, max_length=400, restart=True),
        S("grpc", "tls_client_ca", "Accepted caller certificates",
          "Optional: only callers with a certificate from this authority may connect.",
          "path", grp, "", "maverick.grpc_tls", subgroup=grpc, path_kind="file",
          max_length=400, restart=True),
        S("grpc", "tls_ca", "Worker certificate authority file",
          "Optional: the authority that signed the remote worker's certificate.",
          "path", grp, "", "maverick.grpc_tls", subgroup=grpc, path_kind="file",
          max_length=400, restart=True),
        S("grpc", "tls_client_cert", "Certificate shown to workers",
          "Optional: the certificate this server shows a remote worker.", "path", grp, "",
          "maverick.grpc_tls", subgroup=grpc, path_kind="file", max_length=400, restart=True),
        S("grpc", "tls_client_key", "Private key shown to workers",
          "Optional: the private key for that certificate. Only Lightwork may be able to read "
          "it.", "path", grp, "", "maverick.grpc_tls", subgroup=grpc, path_kind="file",
          path_private=True, max_length=400, restart=True),
        S("grpc", "max_workers", "Worker service threads",
          "How many tasks the worker service works on at once.",
          "number", grp, 8, "maverick.grpc_api.server", subgroup=grpc, minimum=1,
          maximum=256, integer=True, restart=True, env=("MAVERICK_GRPC_MAX_WORKERS",)),
        S("grpc", "max_concurrent_rpcs", "Requests handled at once",
          "The most requests the worker service handles at the same time; more wait.",
          "number", grp, None, "maverick.grpc_api.server", subgroup=grpc, minimum=1,
          maximum=10000, integer=True, restart=True, env=("MAVERICK_GRPC_MAX_CONCURRENT",),
          none_text="As many as the threads above"),
        S("grpc", "max_depth", "Remote task depth limit",
          "How many levels of sub-tasks a remote task may start.",
          "number", grp, 3, "maverick.grpc_api.server", subgroup=grpc, minimum=1,
          maximum=64, integer=True, env=("MAVERICK_GRPC_MAX_DEPTH",), weaker="higher",
          warning="Remote tasks will be able to start more sub-tasks."),
        S("grpc", "stream_max_seconds", "Longest progress stream",
          "How long one caller may follow a task's progress before reconnecting.",
          "number", grp, 300, "maverick.grpc_api.server", subgroup=grpc, minimum=1,
          maximum=3600, unit="seconds", env=("MAVERICK_GRPC_STREAM_MAX_SECONDS",)),
        # --------------------------------------------- linked servers
        S("federation", "enabled", "Link with other Lightwork servers",
          "Let other Lightwork servers you have linked send this server work, and send work to "
          "them.",
          "toggle", grp, False, "maverick.federation", subgroup=fed, restart=True,
          env=("MAVERICK_FEDERATION_ENABLED",), weaker=True,
          warning="Linked Lightwork servers will be able to send this server work.",
          aliases="federation peers"),
        S("federation", "origin", "This server's name for chat messages",
          "The name this server signs chat messages and marketplace listings with, as the "
          "other linked servers know it.",
          "text", grp, "local", "maverick.federation_envelope", subgroup=fed, pattern=_ORIGIN,
          max_length=64, weaker="change",
          warning="Linked servers that know this server by its old name will refuse its "
                  "messages.",
          aliases="origin"),
        S("federation", "node", "This server's name",
          "The name linked servers know this server by.",
          "text", grp, "maverick", "maverick.federation", subgroup=fed, pattern=_NAME,
          restart=True, env=("MAVERICK_FEDERATION_NODE",), aliases="node name"),
        S("federation", "tls", "Encrypt links between servers",
          "Encrypt the connections between linked servers.",
          "toggle", grp, False, "maverick.grpc_tls", subgroup=fed, restart=True,
          weaker=False, warning="Links between servers will not be encrypted."),
        S("federation", "tls_cert", "Link certificate file", "The certificate this server shows.",
          "path", grp, "", "maverick.grpc_tls", subgroup=fed, path_kind="file",
          max_length=400, restart=True),
        S("federation", "tls_key", "Link private key file",
          "The private key for that certificate. Only Lightwork may be able to read it.",
          "path", grp, "", "maverick.grpc_tls", subgroup=fed, path_kind="file",
          path_private=True, max_length=400, restart=True),
        S("federation", "tls_client_ca", "Accepted linked-server certificates",
          "Optional: only linked servers with a certificate from this authority may connect.",
          "path", grp, "", "maverick.grpc_tls", subgroup=fed, path_kind="file",
          max_length=400, restart=True),
        S("federation", "tls_ca", "Linked-server certificate authority",
          "Optional: the authority that signed the linked servers' certificates.",
          "path", grp, "", "maverick.grpc_tls", subgroup=fed, path_kind="file",
          max_length=400, restart=True),
        S("federation", "tls_client_cert", "Certificate shown to linked servers",
          "Optional: the certificate this server shows when it connects.", "path", grp, "",
          "maverick.grpc_tls", subgroup=fed, path_kind="file", max_length=400, restart=True),
        S("federation", "tls_client_key", "Private key shown to linked servers",
          "Optional: the private key for that certificate. Only Lightwork may be able to read "
          "it.", "path", grp, "", "maverick.grpc_tls", subgroup=fed, path_kind="file",
          path_private=True, max_length=400, restart=True),
        S("federation", "max_workers", "Link service threads",
          "How many linked-server requests are worked on at once.",
          "number", grp, 8, "maverick.federation", subgroup=fed, minimum=1, maximum=256,
          integer=True, restart=True, env=("MAVERICK_FEDERATION_MAX_WORKERS",)),
        S("federation", "max_concurrent_rpcs", "Linked-server requests at once",
          "The most linked-server requests handled at the same time; more wait.",
          "number", grp, None, "maverick.federation", subgroup=fed, minimum=1,
          maximum=10000, integer=True, restart=True,
          env=("MAVERICK_FEDERATION_MAX_CONCURRENT",),
          none_text="As many as the threads above"),
        S("federation", "channel_rate_per_min", "Chat messages accepted from linked servers",
          "How many chat messages a minute each linked server may pass on.",
          "number", grp, 30, "maverick.channel_federation", subgroup=fed, minimum=0.001,
          maximum=10000, unit="per minute", restart=True, weaker="higher",
          warning="Linked servers will be able to pass on more messages."),
        S("federation", "channel_max_age_seconds", "Oldest message accepted",
          "Messages passed on by linked servers older than this are refused.",
          "number", grp, 3600, "maverick.channel_federation", subgroup=fed, minimum=1,
          maximum=604800, unit="seconds", restart=True,
          env=("MAVERICK_FEDERATION_CHANNEL_MAX_AGE",), weaker="higher",
          warning="Older messages from linked servers will be accepted, which makes replaying "
                  "them easier."),
        # ------------------------------------------------------ license
        S("license", "enforce", "Limit features to the license",
          "Turn off the features your license does not include. Off: every feature works.",
          "toggle", grp, False, "maverick.entitlements", subgroup=lic,
          env=("MAVERICK_LICENSE_ENFORCE",), aliases="license entitlements"),
        S("license", "license_file", "License file",
          "Where the signed license file is on the server.",
          "path", grp, "", "maverick.entitlements", subgroup=lic, path_kind="file",
          max_length=400, env=("MAVERICK_LICENSE_FILE",)),
        S("license", "publisher_pubkeys", "Trusted license publishers",
          "Only licenses signed by one of these publishers are accepted.",
          "keys", grp, [], "maverick.entitlements", subgroup=lic,
          env=("MAVERICK_LICENSE_PUBKEYS",), weaker="grow",
          warning="Licenses from another publisher will be accepted.",
          aliases="publisher keys"),
        S("license", "api_url", "License service address",
          "Where Lightwork fetches a renewed license. Use Test connection to check it.",
          "text", grp, "", "maverick.entitlements", subgroup=lic, pattern=_HTTPS,
          max_length=300, test="license", env=("MAVERICK_LICENSE_API",)),
        S("license", "api_token", "License service access key",
          "The access key for the license service. Stored sealed and never shown again.",
          "secret", grp, "", "maverick.entitlements", subgroup=lic, max_length=512,
          test="license", env=("MAVERICK_LICENSE_API_TOKEN",)),
        S("license", "refresh_interval_seconds", "Check for a renewed license every",
          "How often Lightwork asks the license service for a renewed license.",
          "number", grp, 60, "maverick.entitlements", subgroup=lic, minimum=30,
          maximum=86400, integer=True, unit="seconds", restart=True,
          env=("MAVERICK_LICENSE_REFRESH_INTERVAL",)),
        # ----------------------------------------------- enterprise boundary
        S("profile", "name", "Deployment profile",
          "Enterprise turns on the stricter enterprise protections whenever the Enterprise "
          "protections switch is not set on its own.",
          "select", grp, "standard", "maverick.profile", subgroup=ent,
          choices=(("enterprise", "Enterprise"), ("standard", "Standard")),
          env=("MAVERICK_PROFILE",), weaker=True, order=("enterprise", "standard"),
          warning="The stricter enterprise protections will stop applying unless they are "
                  "switched on by themselves.",
          aliases="profile enterprise standard"),
        S("enterprise", "require", "Refuse to start unless protected",
          "Stop Lightwork from starting unless every regulated-deployment protection holds.",
          "toggle", grp, False, "maverick.deployment", subgroup=ent, restart=True,
          env=("MAVERICK_REQUIRE_ENTERPRISE",), weaker=False,
          warning="Lightwork will start even when regulated-deployment protections do not "
                  "hold.",
          aliases="enterprise preflight"),
        S("enterprise", "allowed_hosts", "Outside services tools may reach",
          "With enterprise protections on, tools may reach only these outside services.",
          "list", grp, [], "maverick.enterprise", subgroup=ent, pattern=_HOST,
          item_label="Service", item_hint="api.example.com", item_error="a service name",
          weaker="grow", warning="Tools will be able to reach more outside services.",
          aliases="egress allowed hosts"),
        S("enterprise", "local_providers", "AI services treated as in-house",
          "With enterprise protections on, these AI services count as running inside your "
          "organisation.",
          "list", grp, [], "maverick.enterprise", subgroup=ent, pattern=_NAME,
          item_label="Service", item_hint="my-internal-model", item_error="a service name",
          weaker="grow", warning="More AI services will be allowed to receive your data.",
          aliases="local providers"),
        S("deployment", "type", "How Lightwork was installed",
          "Recorded by the installer. It offers this as the first choice when it runs again.",
          "select", grp, "", "maverick.config", subgroup=ent,
          choices=(("", "Not recorded"), ("desktop", "On this computer"),
                   ("docker", "In a Docker container"), ("vps", "On a server you own"),
                   ("phone", "Phone companion")),
          aliases="deployment type installer"),
        S("deployment", "allow_multiple_control_planes", "Allow several main servers",
          "Let more than one server act as the main server at the same time. Only for special "
          "setups: two main servers can overwrite each other's work.",
          "toggle", grp, False, "maverick.control_plane_lease", subgroup=ent, restart=True,
          env=("MAVERICK_ALLOW_MULTIPLE_CONTROL_PLANES",), weaker=True,
          warning="Two main servers will be able to run at once and overwrite each other's "
                  "work.",
          aliases="control plane lease"),
        S("external_agents", "approval_webhook", "Approval requests for outside agents go to",
          "Optional: a web address that is told when an outside agent's access needs "
          "approval.",
          "text", grp, "", "maverick.external_agents", subgroup="Outside agents",
          pattern=_HTTPS, max_length=300, test="https", weaker="change",
          warning="Approval requests will be sent somewhere else.", aliases="webhook"),
        S("external_agents", "connectors", "Systems outside agents may act through",
          "Outside agents may act through these connected systems. None: they are only "
          "screened.",
          "multiselect", grp, [], "maverick.config", subgroup="Outside agents",
          choices_from="governed_connectors", weaker="grow",
          warning="Outside agents will be able to act through more systems.",
          aliases="connectors salesforce servicenow"),
        S("external_agents", "max_enrolled", "Most outside agents",
          "The most outside agents that can be enrolled at once.",
          "number", grp, 500, "maverick.external_agents", subgroup="Outside agents",
          minimum=1, maximum=500, integer=True, unit="agents", weaker="higher",
          warning="More outside agents can be enrolled."),
    )


def entries(S, C, Column) -> tuple:  # noqa: N803,ARG001 -- the catalog's row and column types
    import dataclasses

    rows = list(_sign_in(S) + _people(S) + _security(S) + _other_agents(S) + _server(S)
                + _plumbing(S) + _journal(S))
    from .settings_catalog_conversation import entries as conversation_entries
    rows.extend(conversation_entries(S))
    for index, row in enumerate(rows):
        if row.id == "tenancy.egress":
            rows[index] = dataclasses.replace(row, columns=(
                C("allow", "Allowed", "words", pattern=_HOST, max_length=253,
                  item_error="a service name"),
                C("deny", "Blocked", "words", pattern=_HOST, max_length=253,
                  item_error="a service name"),
            ))
    rows.append(S(
        "federation", "peers", "Linked servers",
        "The other Lightwork servers this one is linked with: each one's name, its address "
        "and port, and the password the two servers share. Passwords are stored sealed and "
        "never shown again; leave one empty to keep it.",
        "rules", "Server & deployment", [], "maverick.federation",
        subgroup="Linked Lightwork servers", restart=True,
        columns=(C("name", "Server name", "text", required=True, unique=True, pattern=_NAME,
                   max_length=64, item_error="a server name"),
                 C("target", "Address and port", "text", required=True, pattern=_HOST_PORT,
                   max_length=260, item_error="a server name and port"),
                 C("token", "Shared password", "secret", required=True, pattern=_SECRET32,
                   max_length=512, item_error="at least 32 characters with no spaces")),
        item_label="Linked server", add_label="Add a linked server",
        empty_text="No servers are linked yet.", weaker="grow",
        warning="Another server will be able to send this server work, or a linked server's "
                "details change.",
        aliases="federation peers"))
    origin = C("origin", "Server name", "text", required=True, unique=True, pattern=_ORIGIN,
               max_length=64, item_error="a lowercase server name")
    pubkey = C("pubkey", "Signing key", "text", required=True, pattern=r"[0-9A-Fa-f]{64}",
               max_length=64, item_error="a 64-character signing key")
    rows.append(S(
        "federation", "channel_peers", "Servers that may pass on chat messages",
        "Linked servers whose chat messages this server accepts: each one's name, the key its "
        "messages are signed with, and the password used to hide people's chat names. "
        "Passwords are stored sealed and never shown again; leave one empty to keep it.",
        "rules", "Server & deployment", [], "maverick.federation_envelope",
        subgroup="Linked Lightwork servers",
        columns=(origin, pubkey,
                 C("secret", "Shared password", "secret", required=True, pattern=_SECRET32,
                   max_length=512, item_error="at least 32 characters with no spaces")),
        item_label="Server", add_label="Add a server", empty_text="No servers yet.",
        weaker="grow", warning="Another server will be able to pass on chat messages.",
        aliases="channel federation peers"))
    rows.append(S(
        "federation", "marketplace_peers", "Servers that may share marketplace listings",
        "Linked servers whose marketplace listings this server accepts: each one's name and "
        "the key its listings are signed with.",
        "rules", "Server & deployment", [], "maverick.federation_envelope",
        subgroup="Linked Lightwork servers", columns=(origin, pubkey),
        item_label="Server", add_label="Add a server", empty_text="No servers yet.",
        weaker="grow", warning="Another server will be able to share listings with this one.",
        aliases="marketplace federation peers"))
    colour = [C(key, label, "color", required=key in {"bg", "panel", "text", "accent"})
              for key, label in (("bg", "Page background"), ("panel", "Panels"),
                                 ("text", "Text"), ("accent", "Highlights"),
                                 ("border", "Borders"), ("muted", "Quiet text"),
                                 ("warn", "Warnings"), ("danger", "Errors"))]
    rows.append(S(
        "dashboard", "themes", "Your own colour themes",
        "Extra colour themes people can pick in Settings, and that can be chosen above as the "
        "theme for everyone. The first four colours are needed; the others are optional.",
        "table", "People & access", {}, "maverick_dashboard.themes", subgroup="Dashboard access",
        row_kind="named", columns=tuple(colour), pattern=r"[a-z][a-z0-9_-]{0,31}",
        item_error="lowercase letters, numbers and dashes, starting with a letter",
        fixed_keys=("dark", "light", "solarized", "hicontrast"),
        row_label="Theme name", item_label="Theme", add_label="Add a theme",
        empty_text="No colour themes of your own yet.", aliases="themes colours branding"))
    rows.append(S(
        "local_first", "models", "Local model for each kind of work",
        "When local models are preferred, use these for the kinds of work listed.",
        "table", "Integrations", {}, "maverick.provider_local_first",
        subgroup="Local AI models", row_kind="grid", choices_from="model_roles", scalar=True,
        columns=(C("model", "Local model", "pick", choices_from="local_models", required=True),),
        row_label="Kind of work", item_label="Kind of work", add_label="Add a kind of work",
        empty_text="Every kind of work uses the local model above.",
        aliases="local first models roles"))
    return tuple(rows)
