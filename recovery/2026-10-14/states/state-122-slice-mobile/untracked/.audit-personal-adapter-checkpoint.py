import json
from pathlib import Path

p = Path('docs/ux-audit-2026-09-30/slices/mobile.json')
rows = json.loads(p.read_text(encoding='utf-8'))
for row in rows:
    if row['id'] == 'LW-141':
        row['implementation_evidence'] += ' product_operations_personal_notifications provides typed reviewed delivery/quiet-hours/event/project/mute/private-channel changes and read-only status through the direct API, current source/link validation, native preference CAS and operation receipts.'
        row['validation'] += ' Personal/organization notification Copilot adapter mocks passed8/8 using slice sources plus integrated collaborator dependency modules.'
        row['remaining'] = row['remaining'].replace(', and personal-preference Copilot adapter remain incomplete', ' remain incomplete')
    if row['id'] == 'LW-142':
        row['validation'] = '2026-09-30: notification adapter mocks passed8/8 (organization3 + personal5), receipt tests6 passed previously. Ruff, Python AST and whitespace pass. Registry dependencies were loaded from the integrated sibling checkout; production registry integration still requires parent broad checks. No live sends.'
        row['remaining'] = 'Parent broad integration checks and controlled delivery qualification remain; no provider calls or receipt success fabricated. Full capacity pauses new sends until retention expiry to preserve duplicate protection.'
    if row['id'] == 'LW-049':
        row['status'] = 'planned'
        row['remaining'] = 'Scoped pairing LW185 and same-handler explicit confirmed/idempotent answer/stop actions remain implementation prerequisites. Native signing/device qualification is validation, not a user decision or reason to stop feasible engineering.'
    if row['id'] == 'LW-057':
        row['validation'] = '2026-09-30: user superseded the test deferral; mobile mock suite passed75/75 and strict TypeScript source analysis passed. Actual iOS/Android keychain/network/accessibility qualification still requires native hosts.'
p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
