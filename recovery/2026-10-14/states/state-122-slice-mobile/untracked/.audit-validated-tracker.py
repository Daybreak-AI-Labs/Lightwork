from pathlib import Path
import json
p=Path('docs/ux-audit-2026-09-30/slices/mobile.json')
rows=json.loads(p.read_text(encoding='utf-8'))
for row in rows:
    if row['id'] in {'LW-047','LW-048','LW-051','LW-052','LW-053','LW-054','LW-055','LW-056','LW-058','LW-059','LW-060'}:
        row['validation'] += ' New user authorization superseded test deferral: mock-only mobile suite passed 75/75 on 2026-09-30; strict TypeScript static check passed all15 TS/TSX sources. This is not installed-device/keychain/accessibility qualification.'
    if row['id'] in {'LW-045','LW-046'}:
        row['validation'] += ' New user authorization: dashboard accessibility DOM regressions passed4/4 (voice cancellation/full fallback/panel replacement/contrast warning). Real browser/assistive technology checks remain outstanding.'
    if row['id']=='LW-048':
        row['implementation_evidence'] += ' Device appearance now explicitly selects System/Light/Dark, persists across connections and follows OS changes only in System mode; failed saves keep previous choice and show recovery.'
        row['remaining']='Native display/language parity remains incomplete: the interface truthfully remains English pending LW043 catalogs/review. Installed two-profile/keychain/large-text platform acceptance still required; mock named-profile, appearance, race and cache tests pass.'
    if row['id']=='LW-142':
        row['status']='implemented-test-needed'
        row['implementation_evidence'] += ' product_operations_notifications.py registers authorized status, explicit reviewed test and safe bounded retry callbacks through the same native service, configuration/receipt revisions and durable receipt recovery. Capacity now refuses new sends rather than evicting accepted idempotency keys.'
        row['validation']='2026-09-30: adapter/core Python AST and whitespace pass; three adapter tests plus receipt-capacity regression authored. Python runtime tests pending root dependency setup/integration (this Python environment has no pytest); no live sends executed.'
        row['remaining']='Integrate settings worker product_operations registry before adapter import/runtime tests. Local Python tests and controlled delivery qualification remain; no provider calls or receipt success fabricated. Full capacity pauses new sends until retention expiry to preserve duplicate protection.'
p.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
