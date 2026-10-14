import json
from pathlib import Path

p = Path('docs/ux-audit-2026-09-30/slices/mobile.json')
rows = json.loads(p.read_text(encoding='utf-8'))
for row in rows:
    if row['id'] == 'LW-185':
        row['status'] = 'implemented-test-needed'
        row['implementation_evidence'] += ' device_pairing.py/API and /devices implement five-minute code approval bound to exact named principal/tenant, encrypted bounded registry, closed own-task route credentials, current SCIM/membership/session revocation checks, explicit removal and rotating renewal with sixty-second exact-generation recovery. Admin paired devices remain own-work scoped. Mobile and shared Apple settings pair/save/renew in secure storage; no proof or credential appears in navigation URLs. Config, installer and direct status/removal UI included; product_operations_devices.py supplies the same-service reviewed own-device Copilot status/removal adapter.'
        row['validation'] = '2026-09-30: mocked Python pairing/auth regressions11/11; mobile mock suite79/79 including four pairing protocol/lifecycle/save failures; strict TypeScript zero diagnostics; Python AST/Ruff and JavaScript syntax checks passed. Apple protocol/persistence regressions authored; Swift/Xcode unavailable, so native builds and installed-device qualification unrun.'
        row['remaining'] = 'Parent must add product_operations_devices to the shared registry (settings worker coordinating that additive entry) and run integrated auth/middleware tests. Real keychain, TLS transport, installed-device, signing, accessibility and revocation/cache timing qualification remain. Pairing uses a human-readable expiring device code; no QR scanner is required. Full offline revocation cannot reach a disconnected device before its previously accepted retention expiry.'
    if row['id'] == 'LW-049':
        row['implementation_evidence'] += ' LW185 scoped own-work credentials now support separately opted-in, default-off reviewed action enrollment; scope allowlist reserves only device answer/stop preparation/execution endpoints. No ordinary API write becomes authorized by token breadth.'
        row['remaining'] = 'Implement reviewed own-task answer/Stop adapters and mobile controls through the existing governed native handlers, with fresh revisions and idempotent receipts. Native signing/device qualification remains separate validation.'
p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
