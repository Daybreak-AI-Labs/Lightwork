# Public website chat

A chat box for anonymous visitors on a customer's public website. One
published specialist answers the questions. It is off by default.

## Turn it on

Either answer yes to "Allow a chat box on your public website" in the setup
wizard (the default is no), or set this in the configuration file:

```toml
[public_chat]
enabled = true
```

Settings > All settings > Chat channels has the same switch. While it is off,
every public and admin chat route answers 404 and nothing is stored.

`allow_localhost_origins = true` lets a website at `http://localhost` use the
chat. It is for development only. Never turn it on for a public website.

## Add a website

Administrators with the admin permission manage sites through the API. A
dashboard page for this is not part of this change.

`POST /api/v1/public-chat/sites`

| Field | Rule |
|---|---|
| `display_name` | 1 to 80 characters, plain text |
| `origins` | 1 to 20 exact `https://` origins, for example `https://shop.example`. No path, query or user name. |
| `specialist` | A specialist published in this workspace |
| `greeting` | Up to 280 characters, shown when a visitor opens the chat |
| `daily_cap_dollars` | Required. Above 0, at most 1000. |
| `messages_per_session` | 1 to 30 (default 20) |
| `messages_per_minute` | 1 to 6 (default 6) |
| `retention_days` | 1 to 365 (default 30) |

The response includes `publishable_key` (`lwpk_...`). It is shown once. Only
its SHA-256 digest is stored, so copy it into the website at once.

- `POST /api/v1/public-chat/sites/{id}/rotate` issues a new key. The old key
  and every live visitor session stop at once.
- `POST /api/v1/public-chat/sites/{id}/revoke` stops the site. Its key,
  origins and sessions end at once.
- `GET /api/v1/public-chat/sites/{id}/transcripts` shows the stored messages,
  read-only, oldest first. Each view is an audit event.

## Embed the chat

Add one tag to the host page. The key is a publishable value, not a secret,
but keep it to the sites you listed.

```html
<script src="https://YOUR-LIGHTWORK-HOST/public/chat/v1/widget.js"
        data-key="lwpk_..." async></script>
```

`widget.js` is static and contains no key. It draws a "Chat with us" button
and a panel in a shadow root. Every message is inserted as text, never as
markup.

## What a visitor can and cannot reach

A visitor can:

- open the chat, read the greeting and send up to the session and minute limits;
- receive one answer per message from the site's specialist.

A visitor cannot:

- use any tool, file, knowledge source, connector or agent loop;
- read workspace records, other visitors' sessions, transcripts or the dashboard;
- change the assistant's role. Visitor text is treated as untrusted, and the
  assistant answers only about the specialist's topic.

## Security model

- **Keys.** `lwpk_` plus 32 random bytes, URL-safe. Stored as SHA-256 digests
  only. A bad, unknown or revoked key gets the same generic `401`.
- **Origins.** A request must carry an `Origin` header that exactly matches
  one of the site's origins. A missing or other origin gets `403`. CORS is
  granted only to those exact origins.
- **Visitor sessions.** `lwps_` tokens, valid for at most 30 minutes, stored
  as digests and bound to the origin they were issued for.
- **No cookies.** Neither the widget nor these routes set or read a cookie,
  so there is no ambient credential for a cross-site request to ride on.
- **Limits.** Message bodies are capped at 4 KiB. Starting sessions is rate
  limited per site and per client address. Messages are limited per session
  and per minute.
- **Answers.** Each answer is one tool-free provider call
  (`LLM.complete` with `tools=None`, one physical attempt, no failover),
  through the budgeted path. Before each call, the answer's share (5 cents)
  of today's cap is held. The hold is settled to the real cost afterwards.
  When the cap is used up, visitors get a "not available right now" answer.
- **Transcripts.** Sealed with the at-rest helper, bound to the tenant, site,
  conversation and row. Stored per tenant. Removed after the site's retention
  period when the site is next written or viewed.
- **Errors.** Refusals use fixed, generic text and never say whether a key
  exists.
- **Audit.** Creating, rotating and revoking a site, and viewing transcripts,
  are audit events without key material. Individual visitor requests are not
  audited.

## Known limits

- Each answer sees only the visitor's current message, not earlier turns.
- The visitor waits for the answer in the same request. There is no polling.
- Transcripts past retention are removed on use, not by a background timer.
- The widget's stylesheet is an inline `<style>` element inside its shadow
  root. A host page whose content security policy forbids inline styles may
  block it, so the panel would appear unstyled.
- Rate limits use the client address the dashboard sees. Whether a proxy in
  front of the dashboard passes the real visitor address was not verified.
