# Deployment

Four deployment targets, all driven by the same `maverick init` wizard.

## Desktop

For most users, request the signed binary for the target operating system in a
[customer package](mailto:chris@daybreakailabs.com?subject=Lightwork%20customer%20package).
Verify its checksum and exact-tag signature, run its `dashboard` command, and
complete setup in the local application. A tag without that binary is not a
customer installation path. The eight-package PyPI cohort remains unpublished;
do not install Lightwork distribution names from a public index. Full-repository
clones include internal research, audit and patent documents and are restricted
to authorized engineering source installs.

Runs as your user. Application state defaults to `~/.maverick/`; customer task
files use the selected sandbox workspace. Guided setup proposes
`~/Documents/Maverick`, while advanced setup proposes `~/maverick-workspace`;
`MAVERICK_WORKDIR` supplies a deployment default and an explicit saved
`[sandbox] workdir` takes precedence. Local setup provisions the selected path
before reporting success. The dashboard and enabled server/channel adapters
listen on their configured ports when started.

Answer-sharing links use **Settings → Lightwork's address for others** (or the
deployment-owned `MAVERICK_PUBLIC_URL`) when configured. The address must already
reach this installation; saving it does not publish a local server. Without it,
links use the address the page was opened at, and Chat explains when that address
is limited to this computer or a private network.

Named-workspace sharing keeps an opaque token-hash locator in the private,
deployment-wide `MAVERICK_HOME/dashboard-share-routes` directory. The destination
world store still checks the token's expiry, revocation and current review gate.
Dashboard replicas must share this private control-plane state as well as their
world backend for the same share links to work on every replica; PostgreSQL alone
does not replicate these locators. Include the directory in application-state
backups. Expired locators are pruned in bounded batches when new links are made.

**Artifact status:** a successful tagged release is configured to publish three single-file
PyInstaller binaries, checksums, SBOMs, and Sigstore material; ask for the
exact operating-system file and verification material before relying on one. The engineering
bootstrap packages and the Windows customer package candidate
are separate paths. The customer candidate includes the frozen runtime and
offline WebView2 installer, but still requires publisher and clean-machine
acceptance before release. Engineering bootstrap packages are
not attached to product releases. Check the exact release asset inventory and
its signature before describing a binary as available. Engineering source
installation is not qualified customer delivery.

## Docker

Container deployments require licensed access to the Lightwork image registry.
Authenticate with the credentials supplied for your deployment and use the
approved, pinned image version in place of `latest` in this example.

```bash
docker run -it --rm \
  -v ~/maverick-workspace:/workspace \
  -v ~/.maverick/config.toml:/home/maverick/.maverick/config.toml:ro \
  -e MAVERICK_WORKDIR=/workspace \
  -e ANTHROPIC_API_KEY=... \
  ghcr.io/daybreak-ai-labs/lightwork:latest \
  start "..."
```

For untrusted skills, **do not** mount `~/.maverick/` into the same
container that runs the agent tools. A local sandbox inside this
container can read mounted files and process environment variables.
Mount only the workspace and a read-only `config.toml`; keep API keys
scoped to this container run.

This one-shot example persists task files at `/workspace`; application state
outside those mounts is disposable. A saved `[sandbox] workdir` must also point
inside the workspace mount. The standard persistent-state image default is
`/home/maverick/.maverick/workspace`; Compose explicitly uses `/workspace`.
On upgrades, relocate files from an older workdir before replacing its container
and verify persistence after replacement. A saved path is not rewritten just
because the deployment default changed.

## VPS

Always-on, accessible from anywhere via channel adapters.

The `vps` deployment target generates:

- A `systemd` unit at `/etc/systemd/system/maverick.service`
- A Caddy reverse proxy config (if you need HTTPS for WhatsApp/SMS webhooks)
- Config under `/etc/maverick/config.toml` (`MAVERICK_CONFIG` env)

The generated service uses the configured sandbox workdir and validates that
the service account can use it. It does not assume that every setup selected
`~/maverick-workspace`.

The VPS installer is part of licensed deployment materials, not a public
download. [Request a customer package](mailto:chris@daybreakailabs.com?subject=Lightwork%20VPS%20deployment),
verify its included artifacts, and follow its version-specific installation
instructions. Confirm the generated service and reverse proxy before exposing
the dashboard to users.

### Security-sensitive world-schema upgrades

The v32 execution-principal and v33 HALT-generation migrations are additive
database changes, but they are not a mixed-binary security guarantee. Before an
upgrade that crosses either version, drain and stop every goal executor and
every process that can arm or clear the shared HALT, upgrade those processes as
one cohort, let one upgraded process migrate the world store, and only then
resume dispatch. A pre-v32 executor does not enforce the exact execution binding;
a pre-v33 HALT writer does not rotate or compare the clear generation.

Do not treat the SQL migration's online-safe classification as permission to
run old and new executors concurrently when relying on either property. The
database remains upgrade-compatible; the security semantics take effect after
all relevant writers are on the upgraded code.

## Behind a load balancer or reverse proxy

The dashboard's per-address limits (the public phone and TV consent endpoints
count each caller's address, 120 requests a minute for each of enrolling,
sending and refreshing) use the address of whoever opened the connection. Behind
a load balancer that is the balancer, so every phone and TV in the fleet shares
one allowance and one busy app can lock the rest out. The dashboard only
believes the `X-Forwarded-For` header from addresses listed in the
`FORWARDED_ALLOW_IPS` environment variable, which defaults to loopback only. To
count each device on its own, list your balancer in the environment the
dashboard starts in:

```bash
# the balancer's address; current uvicorn also accepts a range such as 10.0.0.0/16
export FORWARDED_ALLOW_IPS="10.0.1.20"
```

- List only proxies you run, and make sure they add the address of the
  connection they received to `X-Forwarded-For` (most load balancers do). Do not
  use `*` when the dashboard can also be reached directly: any caller could then
  choose the address it is counted under.
- The setting changes the caller address for everything in the dashboard that
  reads it, not just these limits, so keep the list as short as the real
  proxies.
- With reverse-proxy sign-in switched on (`[auth.proxy] enabled`), the
  dashboard turns this handling off so that a forwarded header can never
  impersonate a trusted proxy. `FORWARDED_ALLOW_IPS` then has no effect and the
  devices behind the balancer still share one per-address allowance.
- The per-app limit (Settings, "Requests per minute from each app") is
  separate and always applies to the whole app, whatever the address.
- To check, send requests from two devices and look at the dashboard's request
  log: it should show each device's own address instead of the balancer's.

## Phone (companion mode)

Lightwork itself runs on Desktop or VPS — your phone is a frontend that
talks to it through one of the channels below. This avoids the cost,
privacy, and capability tradeoffs of running an agent on the phone
itself, while keeping parity with the desktop experience.

Start the channel server on whichever machine Lightwork runs on:

```bash
maverick serve
```

### Channel matrix

| Channel  | Status   | Setup | Public webhook needed? |
|----------|----------|-------|------------------------|
| Telegram | ready    | Create a bot via @BotFather, paste token | no |
| Discord  | ready    | Create a Discord app + bot, enable Message Content Intent | no |
| Slack    | ready    | Create app, enable Socket Mode, paste both tokens | no |
| Signal   | ready    | Install signal-cli, register your number | no |
| Email    | ready    | IMAP + SMTP credentials (Gmail app password works) | no |
| Matrix   | ready    | Homeserver URL + user_id + access token | no |
| WhatsApp | ready    | Twilio Business API + public HTTPS endpoint | YES |
| SMS      | ready    | Twilio + public HTTPS endpoint | YES |
| iMessage | ready    | macOS only + Full Disk Access permission | no |

Multiple channels can be enabled at once; each runs in its own async task.

### Channel setup recipes

**Telegram (easiest):**

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Run `/newbot`, follow prompts, copy the token
3. `maverick init` — enable telegram, paste token at the env-var prompt
4. `maverick serve`
5. Find your bot in Telegram and message it

**Discord:**

1. https://discord.com/developers/applications → New Application
2. Bot tab → Add Bot → enable Message Content Intent → copy token
3. OAuth2 → URL Generator → scopes: `bot`; permissions: Read Messages, Send Messages
4. Open the generated URL to invite the bot to your server
5. `maverick init`, enable discord, paste token

**Slack:**

1. https://api.slack.com/apps → Create New App → From scratch
2. Socket Mode → Enable; generate an App-Level Token with `connections:write` scope
3. OAuth & Permissions → add bot scopes: `chat:write`, `im:history`, `im:read`
4. Install to your workspace; copy Bot Token
5. Event Subscriptions → enable; subscribe to `message.im`
6. `maverick init`, enable slack, paste both tokens

**Signal:**

1. Install signal-cli: https://github.com/AsamK/signal-cli
2. Register: `signal-cli -u +12345550199 register`
3. Verify with the SMS code: `signal-cli -u +12345550199 verify 123-456`
4. `maverick init`, enable signal, enter your number

**Email:**

1. Use an app password (Gmail Settings → 2FA → App Passwords)
2. `maverick init`, enable email, paste IMAP/SMTP details + app password

**Matrix:**

1. Create account on matrix.org (or self-hosted homeserver)
2. Get access token (Element → Settings → Help & About → Access Token)
3. `maverick init`, enable matrix, paste homeserver, user_id, token

**WhatsApp / SMS (require public webhook):**

1. Sign up at twilio.com; verify a sender
2. Run Lightwork on a VPS (you need a public HTTPS endpoint)
3. Caddyfile in `deploy/vps/Caddyfile` shows the reverse-proxy pattern
4. `maverick init`, enable whatsapp/sms, paste Twilio creds + from number
5. In Twilio console, set the webhook URL to
   `https://yourdomain.com/webhook/whatsapp` (or `/sms`)

**iMessage (macOS only):**

1. System Settings → Privacy & Security → Full Disk Access
2. Add `/usr/bin/python3` (or your Python interpreter) to the list
3. `maverick init`, enable imessage
