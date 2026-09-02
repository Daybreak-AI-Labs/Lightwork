# Daybreak Labs — marketing site

The public site for **Daybreak Labs** (an AI lab for agentic workforces, built on
compliance and governance) and its product, **Lightwork**. Plain HTML + CSS + two
small scripts — no build step, no framework. Every claim is verifiable from this
repo.

## Design: "the lab"

Second-edition design system (`styles.css`): a dark graphite ground, hairline
cell grids, mono section indices, one brand blue, and the sunrise gradient from
the mark used only as a thin *daybreak line*. Type is **Instrument Sans**
(display + body) and **JetBrains Mono** (labels, indices, receipts), both
self-hosted from `fonts/` so the site makes no third-party request except the
Cloudflare analytics beacon. `lab.js` draws the homepage's "gate instrument"
figure on a canvas (five gates, a signed record); it draws one static frame under
`prefers-reduced-motion` and pauses off-screen. `app.js` is the demo-request modal.

## Pages
| File | What it is |
|---|---|
| `index.html` | Home — hero + gate instrument, problem, how it works, why, Autonomous Compliance, Autonomous Shoring, six products, proof, security, who it's for, from the lab, CTA |
| `product.html` | Lightwork Platform — how the governed runtime works |
| `second-shift.html` | Second Shift — the 2,020 specialist packs across 53 suites |
| `fleet-governance.html` | Fleet Governance & Memory — govern and learn from agents you didn't build |
| `autonomous-compliance.html` | Autonomous Compliance — the agentic replacement for OneTrust-class platforms: six suites, how it's built, the 149-row capability map, how a migration runs |
| `autonomous-shoring.html` | Autonomous Shoring & BPO — moving a whole function to a governed agentic workforce |
| `daybreak-forge.html` | Daybreak Forge — bespoke departments from the agent factory |
| `security.html` | Security & deployment — control→mechanism table, deployment models, audit/evidence, compliance status |
| `company.html` | The lab, the thesis, what we build, contact |
| `onepager.html` | Printable one-page summary |
| `privacy.html` | Privacy & cookies |
| `404.html` | Not found |
| `styles.css` | The shared design system |
| `lab.js`, `app.js` | Gate-instrument figure; demo modal |

**Brand assets**: `daybreak-mark-dark.png` (the circuit-sunrise D mark, used in the
nav and footer), `daybreak-logo.png` (full horizontal lockup with tagline, 970×305,
kept for decks and social), `favicon-32/180/512.png`, and `og.png` (1200×630).

## Preview
Open `index.html` in any browser (fonts are local, so it works offline). For a
quick visual check with the pre-installed Chromium: `npx playwright screenshot
--full-page file://$PWD/index.html index.png`.

## Deploy — live
Hosted on **Cloudflare Pages**, project `daybreak-labs`:
- Production: https://daybreak-labs.pages.dev
- Redeploy: `CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… npx wrangler pages deploy pitch/site --project-name=daybreak-labs --branch=main`

**Custom domain `daybreakailabs.com`** (+ `www`) is attached to the project. Two
things must both be true for it to serve: the domain's nameservers point at
Cloudflare (`aaden.ns.cloudflare.com`, `leia.ns.cloudflare.com`), **and** the
zone's DNS records for the apex and `www` are proxied `CNAME`s to
`daybreak-labs.pages.dev`. Records imported from the previous host (Wix) are
not replaced automatically; until they are, Pages reports the domain as
`pending: CNAME record not set` and visitors get a Cloudflare challenge or
error page instead of the site. Editing those records needs a token with
**Zone → DNS → Edit** on the zone (an account-scoped token cannot do it). Email
stays on **Google Workspace** — preserve the `MX` (`aspmx.l.google.com`) and the
SPF/verification `TXT` records through the switch.

## Demo form delivery (Web3Forms)
**Book a demo** / **Request access** submit to [Web3Forms](https://web3forms.com)
when an access key is present, and otherwise fall back to a pre-filled `mailto:`
(never a dead end). Set the key at deploy time **without editing `app.js`**, via
either:
- a `<meta name="web3forms-access-key" content="YOUR-KEY">` in each page `<head>`, or
- `window.LIGHTWORK_ACCESS_KEY = "YOUR-KEY";` (e.g. an uncommitted `config.js`).

The key is the only thing standing between the live form and direct-to-inbox
delivery — get a free one at web3forms.com (sends to chris@daybreakailabs.com).

## Finish before launch
- Set the Web3Forms access key (above) so demo requests reach the inbox directly.
- Confirm the SOC 2 wording on `security.html` matches reality before publishing.
- The Autonomous Compliance capability numbers (149 rows; 75 / 67 / 0 / 7) come from
  `docs/ONETRUST_PARITY_BREAKDOWN_2026-08-31.md`; refresh them when that review is re-run.
