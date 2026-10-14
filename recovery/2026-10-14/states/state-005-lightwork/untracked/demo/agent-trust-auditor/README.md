# Agent Trust Auditor standalone SKU

This reduced defensive SKU compares bounded declared-asset JSON with safe,
allowlisted metadata observations. It deterministically reports unowned,
unpinned, overprivileged, stale, and undeclared-tool conditions. Every finding
cites stable source-namespaced observation identifiers.

Declarations and observations exist only for the duration of one request. The
local JSON store contains minimized, derived findings only, protected by an
exact compare-and-swap revision. It is unsigned and makes no tamper-evidence,
certification, continuous-monitoring, or governance claim. Stable finding IDs
are updated in place, identical reruns are no-ops, and the store retains at
most 5,000 current findings within an 8 MiB file.

| Runtime situation | Analysis backend | Record and governance authority |
| --- | --- | --- |
| `AGENT_TRUST_AUDITOR_STANDALONE=1` | Vendored deterministic rules; no `maverick` import | Unsigned local derived findings only |
| Auto mode, core absent | Vendored deterministic rules | Unsigned local derived findings only |
| Auto mode, core pure rules available | Lightwork Agent Trust Auditor analysis adapter | Unsigned local store; **not** integrated governance |

The standalone has no live-discovery, authority-change, response, lifecycle,
audit-signing, or evidence-pack endpoint. Its schema rejects credential- and
content-shaped field names and has no fields for prompts, messages, tool
arguments, tool results, request/response bodies, payloads, or raw telemetry.
Observation metadata is restricted to service, environment, model, operation,
tool-type, and transport labels. Allowed text values are not a secret scanner;
do not place credentials or other secrets in any submitted value.

Integrated Lightwork is the upsell boundary: configured and live discovery,
tenant-governed ownership and lifecycle, mandatory authority policy, continuous
freshness monitoring, the evidence graph, signed audit, and signed evidence
packs remain integrated-only capabilities. The `/about` page binds every
advertised capability to its actual implementation and authority.

```bash
python -m pip install -r requirements.txt
bash run_standalone.sh                 # http://127.0.0.1:8894
```

Configuration: `AGENT_TRUST_AUDITOR_STANDALONE=1` forces isolation,
`AGENT_TRUST_AUDITOR_STORE` selects the derived-record JSON file, and
`AGENT_TRUST_AUDITOR_PORT` selects the port. Mutations require
`application/json` and an exact non-negative `expected_revision`. The service
admits only loopback Hosts and same-loopback-origin browser writes, streams
bodies through a 512 KiB cap, and serializes local CAS commits across processes.
When retention is full, the oldest finding not present in the current batch is
evicted; submitted declarations and observations are never retained.

Tagged releases package this directory as the licensed
`lightwork-agent-trust-auditor-<version>.zip` source SKU using its explicit
release manifest. It is not a wheel or PyPI distribution. The same archive can
be reproduced from a tagged checkout at the repository root:

```bash
python scripts/build_standalone_skus.py --version <version> \
  --source-revision "$(git rev-parse HEAD)"
```
