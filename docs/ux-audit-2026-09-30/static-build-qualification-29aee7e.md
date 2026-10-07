# Frozen static and build qualification

Source: **29aee7e75b6d31012f087ec725456eec20e206af**.
Executed 2026-09-30 23:51–23:58 UTC on Windows with Python 3.12.12,
checkout `qualification-final-02` and its matching
`.venv-qualification-final-02` environment.

**All 26 checks passed:** two static gates, 16 offline contracts and eight
wheel builds. The runner returned exit code zero. No source repair or
dependency change was needed. The frozen checkout was clean before and after.

## Static and contract checks

- `python -m ruff check --no-cache .`: passed.
- `python -m vulture`: passed using the repository's configured paths,
  exclusions and confidence threshold.
- The following `maverick` modules passed their `--ci` checks:
  `plugin_matrix`, `reachability`, `dispatch_contract`, `egress_contract`,
  `audit_contract`, `deprecations`, `a11y_audit`, `async_route_audit`,
  `failure_policy`, `schema_migrations`, `migration_governance`, and
  `evaluator_evolution`.
- `maverick.grpc_api.contract --check`,
  `maverick.regulatory_compiler --ci --all`,
  `maverick.domain_audit --ci`, and
  `scripts/verify_constraint_closure.py` passed.

The constraint-closure gate verified 91 external distributions were exactly
pinned. The static accessibility gate inspected 226 templates with no findings;
this is a static result, not a substitute for interactive accessibility testing.

## Wheel builds and provenance

All eight packages passed `python -m build --wheel --no-isolation`:
core (`maverick-agent`), channels, dashboard, evolve, knowledge,
MCP (`maverick-mcp-server`), shield and installer. Each wheel version is 0.1.7.
No distribution was installed, published or deployed.

The existing local contract runner retained its isolated home and stripped
credential environment. To avoid generated build and egg-info files in the
frozen checkout, package sources were exported with `git archive` from the
exact commit into the external audit directory. The source archive SHA256 is
`9aaadb0fe5d09ef4c08f80be38ce11a0e2e72f158b36713c4a3668d861499a30`.
All eight wheel ZIP integrity checks passed and all output hashes were verified.
The core wheel's `maverick/config.py` matches the exported source byte for byte
and matches the Git object after normalizing the export's Windows CRLF endings.

| Wheel | SHA256 |
| --- | --- |
| maverick_agent-0.1.7-py3-none-any.whl | `7bd075c4b424788735840ed41514be6fcb50d37ef01319bdc9feb1d272431fc4` |
| maverick_channels-0.1.7-py3-none-any.whl | `5866cb13cc38beff02e15ff074b4b0e14dfbe78fcf59931eafad592f6c5bbd73` |
| maverick_dashboard-0.1.7-py3-none-any.whl | `a2264c554e825a22c573bd7e84f5614322f020d27d1d991aa48167ebb85d15a6` |
| maverick_evolve-0.1.7-py3-none-any.whl | `ae6c83e858b560349c67a7a8afbe0df077608f9812d7d1fbb1e506945f21b6c1` |
| maverick_installer-0.1.7-py3-none-any.whl | `c9797a6789237c4c2a2cad22740aa424313fd01d7e7cd33f65e69c503fdc04c5` |
| maverick_knowledge-0.1.7-py3-none-any.whl | `1e5194ae0003c88283092618a160fe2e964212a3cfd65ba4af4d1687b9936f57` |
| maverick_mcp_server-0.1.7-py3-none-any.whl | `ad58fb95020bf32b811500388efbfbb84c9be38b75bb37a0c92dc408356318ef` |
| maverick_shield-0.1.7-py3-none-any.whl | `3f7b62bd302a93f59b94289af332afdf3c2cc3b16fd975a6865f2aea2f79d6f0` |

## Evidence and limits

Task-workspace evidence is retained under
`audit/qualification-final-02-contracts/`: `source.json`, `results.json`,
`summary.json`, `wheel-sha256.json`, all per-gate/build logs, and `wheels/`.
The runner log is `audit/qualification-final-02-contracts-run.log`.

This qualification describes only the named commit. Later dashboard changes,
including the separate LW-095 SQL owner-filter repair, are not in these wheels.
The separately running ordinary test suite, secure/heavy test phases and
installed-service qualification are not certified by these static/build results.
The earlier native worker access violation remains an unresolved diagnosis;
passing these gates does not establish its cause or remediation.
