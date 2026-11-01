# `web/` — static landing page

`index.html` (one HTML file, inline CSS, no build step) plus `daybreak-logo.jpg`,
the favicon shared with the dashboard. The page is a short introduction to the
project for people who arrive outside GitHub: what Lightwork does, how to
install it from source, where it can be deployed, and where to find the
documentation, contribution guide and security policy.

## Local preview

```bash
python -m http.server -d web 8000
# open http://127.0.0.1:8000
```

## Calls to action

Keep the links on the page pointing at project resources:

| Link | Target |
| --- | --- |
| View on GitHub | <https://github.com/Daybreak-AI-Labs/Lightwork> |
| Install from source | [`docs/source-install.md`](../docs/source-install.md) on GitHub |
| Read the docs | the documentation site, <https://daybreak-ai-labs.github.io/Lightwork/> |

Releases, when there are any, appear at
<https://github.com/Daybreak-AI-Labs/Lightwork/releases>. No prebuilt binary,
PyPI package or container image is published yet, so the page points at the
source install. Keep the install commands on the page identical to the source
install guide.

## Publishing

No workflow deploys this directory. The repository's GitHub Pages site is the
documentation site, built and deployed by `.github/workflows/docs.yml`, and a
repository has only one Pages site. To publish the landing page, either serve
`index.html` and `daybreak-logo.jpg` from any static host, or add both files to
the documentation build output in that workflow.
