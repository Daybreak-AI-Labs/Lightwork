# Lightwork content catalog (federated)

A catalog lets users browse and install community content — skills
today, plugins / MCP servers / personas next — without editing config
or running install-by-URL. It's the dashboard **Store** tab and the
`maverick skill browse` / `maverick skill add` commands.

## Index format

One `index.json` per kind, served at `<base>/<kind>/index.json`:

```json
{
  "schema_version": 1,
  "kind": "skills",
  "entries": [
    {
      "name": "summarize-url",
      "version": "1.0.0",
      "summary": "Fetch a URL and write a 3-sentence summary.",
      "source": "gh:org/repo:path/to/SKILL.md",
      "sha256": "<hex digest of the fetched content>",
      "author": "org",
      "verified": true,
      "install_count": 0
    }
  ]
}
```

A worked example lives in [`catalog-example/`](./catalog-example/).

## Trust model

Catalog skill installation requires a valid Ed25519 signature from a publisher
whose public key is configured in `[skills].trusted_pubkeys`. No publisher key
is bundled, so an administrator must configure one before installing catalog
skills. The index hash checks the downloaded bytes but does not authenticate
them when the same host controls the index and content. Shield scanning is an
additional best-effort check, not a guarantee against malicious instructions.

Free-text `POST /api/v1/skills` (arbitrary `gh:`/`https:` source) stays
gated behind `MAVERICK_ALLOW_SKILL_INSTALL=1` — that's the RCE vector.
`POST /api/v1/catalog/skills/install` resolves by name and requires both the
hash and a trusted signature. MCP catalog specs have separate trust limits.

## Configuration

```toml
[catalogs]
# Base URLs (no trailing /kind). Multiple indexes merge; earlier wins
# on name collision. The built-in default index is not yet published;
# configure your own under [catalogs] indexes.
indexes = ["https://raw.githubusercontent.com/your-org/your-index/main/catalog"]
```

Self-hosting an index is just serving static `index.json` files — a
GitHub Pages repo or any HTTPS static host. No server code required.

## Caching

Indexes cache to `~/.maverick/cache/catalog/` for 6 hours. An
unreachable index degrades to "no entries" (it never breaks the
dashboard) and serves a stale cache if one exists.

## Surfaces

- Dashboard: `/store` (Store tab) + `GET /api/v1/catalog/{kind}` +
  `POST /api/v1/catalog/skills/install`
- CLI: `maverick skill browse`, `maverick skill add <name>`

## Roadmap

- Plugins / MCP / personas install-from-catalog (schema already
  supports the kinds; only skills wire the install path today)
- Ed25519 publisher signatures + a `verified` badge driven by the
  signing key, not a hand-set bool
- Privacy-respecting install counts (anonymous increment)
