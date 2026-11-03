# `web/`: the Daybreak Labs website

The public site at <https://daybreakailabs.com>, for people who arrive outside
GitHub: what Daybreak Labs does, what Lightwork is and how it works, how to
install it, and where to find the source, documentation and community. Plain
HTML and CSS with one small script (`lab.js`); no build step, no framework.

`widget/` is not part of the site. It is the embeddable chat widget, which runs
on a Lightwork dashboard's own origin; see [`widget/README.md`](widget/README.md).

## Pages

| File | What it is |
| --- | --- |
| `index.html` | Home: the problem, how the runtime works, Cortex, open source, the six ways to use it, proof, security, who it's for, benchmarks, and the source install |
| `product.html` | Lightwork Platform: how the governed runtime works |
| `cortex.html` | Cortex, the governed company memory |
| `packs.html` | Specialist packs, and drafting a new pack for review |
| `fleet-governance.html` | Governing and remembering for agents you didn't build |
| `autonomous-compliance.html` | Privacy, GRC, third-party and AI-governance work run by governed agents |
| `autonomous-shoring.html` | The tooling for moving a whole business function onto governed agents |
| `security.html` | Controls, where each one stops, deployment, audit evidence, vulnerability reporting |
| `company.html` | The lab, the thesis, why open source, contact |
| `privacy.html`, `terms.html` | Website privacy and terms of use |
| `404.html` | Not found |
| `_redirects` | Cloudflare Pages redirects for retired pages (`/second-shift`, `/daybreak-forge`, `/onepager`) |
| `sitemap.xml`, `robots.txt` | Search engines |
| `styles.css`, `lab.js`, `fonts/`, `*.png` | Design system, the two canvas figures, self-hosted fonts, brand assets |

## Copy rules

- Lightwork is open source under the Apache License 2.0, and nothing on the
  site is for sale: calls to action point at the repository, the source
  install, the documentation and GitHub Discussions.
- Every claim must be verifiable from this repository. State where a control
  stops, as `SECURITY.md` and the README do.
- Deployment is self-hosted or air-gapped. No pack, agent, suite or department
  counts; no "data never leaves" claims; no certification claims.
- Keep the install commands identical to
  [`docs/source-install.md`](../docs/source-install.md).
- Never use the retired repository owner name; links go to
  `https://github.com/Daybreak-AI-Labs/Lightwork`.

## Preview

```bash
python -m http.server -d web 8000
# open http://127.0.0.1:8000
```

Fonts are self-hosted, so the pages also work opened directly from disk. The
`_redirects` file and extension-less URLs (`/product`) only work on Cloudflare
Pages.

## Deploy

`.github/workflows/pages-deploy.yml` publishes `web/` (without `widget/` and
this README) to the Cloudflare Pages project `daybreak-labs` on every push to
`main` that touches the site, and on manual dispatch. It needs the
`CLOUDFLARE_API_TOKEN` repository secret. A pull request does not deploy;
merging it to `main` does.
