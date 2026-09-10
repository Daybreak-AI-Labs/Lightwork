# `web/` — static landing page

`index.html` (one HTML file, inline CSS, no build step) plus `daybreak-logo.jpg`,
the favicon shared with the dashboard. Deploy via GitHub Pages with this
directory as the source, then put the production domain in front of it.

## Positioning

The page sells Lightwork as what it is: a governed, self-improving AI
**workforce platform** for regulated enterprises — provable learning and the
Operating Record are the pitch. It never positions the product as an "agent
runtime": free OS agent runtimes are a commodity layer, not the competitive
category (see the category note in `CLAUDE.md`).

The calls to action match the licensing model: **Request access** and
**Read the docs**. There is no public download or install-from-source block;
releases are delivered under license from a reviewed, signed build. The
"Request access" link currently points at the repository — replace it with the
access-request or demo form once one exists.

## Why it's not the README

`README.md` at the repo root is a developer's first impression and shows up on
PyPI / pkg-info. A landing page targets the buyer: a one-sentence pitch, the
governance and learning story, and how it deploys. Mixing the two audiences in
one document serves neither.

## Local preview

```bash
python -m http.server -d web 8000
# open http://127.0.0.1:8000
```

## Deploy

1. In repo Settings → Pages, set source to `main` branch / `/web` folder
2. The site appears at `https://daybreak-ai-labs.github.io/Lightwork/`

To serve it from the production domain (whichever is chosen for Lightwork;
the former `maverick.dev` plan predates the rebrand):

3. Add a `web/CNAME` file containing that domain
4. Point an A record at the four GitHub Pages IPs (185.199.108-111.153)
5. Enable "Enforce HTTPS" in Pages settings

## What's intentionally missing

- A real access-request / demo form target (see above)
- Screenshots (freeze one once the dashboard UI settles)
- Pricing (proprietary; handled per engagement — no public pricing yet)
- Trust badges (add after the first signed release is public)
