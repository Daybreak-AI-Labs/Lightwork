# Frozen static, contract and wheel qualification

Source: **2c1e8a888eee011a2851836079b57c9929b8d745**.
Executed on Windows with Python 3.12.12 from 2026-10-01T00:44:23+00:00 through 2026-10-01T00:50:59+00:00.
Checkout `qualification-final-03`; matching environment
`.venv-qualification-final-03`. All eight import origins were verified inside
the frozen checkout before the shared dependency directory.

**All 26 checks passed:** Ruff, Vulture, 16 offline contract gates, and eight
wheel builds. The runner and wheel verifier returned zero. The frozen HEAD
and working tree were clean before and after. No source repair or dependency
change was required.

## Gates

Ruff used `python -m ruff check --no-cache .`; Vulture used the repository
configuration. The following `maverick` modules passed `--ci`:
`plugin_matrix`, `reachability`, `dispatch_contract`, `egress_contract`,
`audit_contract`, `deprecations`, `a11y_audit`, `async_route_audit`,
`failure_policy`, `schema_migrations`, `migration_governance`, and
`evaluator_evolution`. The remaining four contract commands passed:
`maverick.grpc_api.contract --check`,
`maverick.regulatory_compiler --ci --all`, `maverick.domain_audit --ci`, and
`scripts/verify_constraint_closure.py`.

The static accessibility gate inspected 226 templates with zero findings.
The dependency gate verified 91 exactly pinned external distributions.
Migration governance reported SQLite and PostgreSQL heads at version 46.

| Gate | Seconds | Result |
| --- | ---: | --- |
| `ruff` | 4.19 | Passed |
| `vulture` | 29.23 | Passed |
| `maverick.plugin_matrix` | 0.20 | Passed |
| `maverick.reachability` | 24.11 | Passed |
| `maverick.dispatch_contract` | 41.97 | Passed |
| `maverick.egress_contract` | 20.32 | Passed |
| `maverick.audit_contract` | 13.93 | Passed |
| `maverick.deprecations` | 0.16 | Passed |
| `maverick.a11y_audit` | 9.40 | Passed |
| `maverick.async_route_audit` | 4.58 | Passed |
| `maverick.failure_policy` | 0.21 | Passed |
| `maverick.schema_migrations` | 0.18 | Passed |
| `maverick.migration_governance` | 0.19 | Passed |
| `maverick.evaluator_evolution` | 0.18 | Passed |
| `maverick.grpc_api.contract` | 0.18 | Passed |
| `maverick.regulatory_compiler` | 0.37 | Passed |
| `maverick.domain_audit` | 0.18 | Passed |
| `verify_constraint_closure` | 1.35 | Passed |

## Wheels and source provenance

All eight packages passed `python -m build --wheel --no-isolation`.
To keep build/egg-info output out of the frozen source, the runner exported
only package sources from the exact commit with `git archive` and built in
`audit/qualification-final-03-contracts/build-source`. Git trust was scoped to
the exact checkout in process-local configuration; no global Git configuration
was changed. Gate processes used an isolated home, filtered credential
environment, and disabled bytecode writes. No package was installed, published
or deployed, and no live provider qualification was invoked.

All eight wheel ZIP integrity and SHA256 checks passed. Each of the **4,824
non-metadata payload files** was compared byte-for-byte directly with the Git
archive member, not the mutable build tree. The archive embeds the exact source
commit above. Generated wheel distribution metadata is covered by the ZIP and
whole-wheel digest checks.

Source archive SHA256: `936fd5e3c42d7a3c2053737d69be6b58ae03e9d1a42fb9423cf7f1ec70cdce63`.

| Wheel | Payload files checked | SHA256 |
| --- | ---: | --- |
| `maverick_agent-0.1.7-py3-none-any.whl` | 3854 | `82e703ad559c3123f2d0503564f159d247ed3dd641821e8dcca2b6d9b34abaef` |
| `maverick_channels-0.1.7-py3-none-any.whl` | 31 | `7778be77c7c84eaa5cfa8ba53aa2061a5f866c68f051fab4f81ad155255d27e8` |
| `maverick_dashboard-0.1.7-py3-none-any.whl` | 887 | `123f62085708fe6afef8a608c728403d180bf8580f8f0fe396e51c701006f92f` |
| `maverick_evolve-0.1.7-py3-none-any.whl` | 12 | `459024ba5784506dad1d34a9b5294aadc05844d000885c8f747dabba42f2daa9` |
| `maverick_installer-0.1.7-py3-none-any.whl` | 5 | `d8fcc6dcafd8d7f1abcd73b1a853b096697fe2411ad19337c4e70994570059b9` |
| `maverick_knowledge-0.1.7-py3-none-any.whl` | 12 | `32cb4ec35d2a38806aa87770e674fdf8cabcaa43173a40979c83ccc748218aca` |
| `maverick_mcp_server-0.1.7-py3-none-any.whl` | 9 | `b0a52d537d2f4967eb6bdff99ae2b5b7f9e3568b23f3425279650aa7a6b49eeb` |
| `maverick_shield-0.1.7-py3-none-any.whl` | 14 | `bf8d2427cbb64d72255925ed9a8c5ce0d66df8034347a8e0d16f956b357ac851` |

## Evidence and limits

Task-local artifacts are retained in `audit/qualification-final-03-contracts/`:
`source.json`, `results.json`, `summary.json`, `import-provenance.json`,
`wheel-sha256.json`, `wheel-payload-verification.json`, per-gate/build logs,
the source archive, and all eight wheels.

The task-local runner invocation was:
```text
python run-local-contracts.py qualification-final-03 2c1e8a888eee011a2851836079b57c9929b8d745
python verify-local-contracts.py qualification-final-03 2c1e8a888eee011a2851836079b57c9929b8d745
```

This evidence qualifies only the named source and these static/contracts/build
checks. The independently running ordinary suite, secure/heavy test phases,
and interactive or installed-service validation have separate results.
