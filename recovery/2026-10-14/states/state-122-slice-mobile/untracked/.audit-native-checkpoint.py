from pathlib import Path
import json

p = Path('docs/ux-audit-2026-09-30/slices/mobile.json')
rows = json.loads(p.read_text(encoding='utf-8'))
validation = '2026-09-30: Source review and git diff --check only for Apple changes. Swift compiler/Xcode unavailable on this Windows executor. Shared Swift byte-limit/error/reset tests and watch/vision model regressions authored but not run; app model tests await a native Xcode test target. No native, signing, device, browser or provider tests executed per user deferral.'
items = [
('178', 'implemented-test-needed', 'ConnectionStore.mayRetainSnapshot permits only explicit transient failures; both Apple models clear private data/receivedAt on 401/403 and other ineligible errors. Vision selection derives from current forest and clears when absent. Watch/vision denial and transient-retention regression sources authored.', 'Native mock-denial and retained-snapshot qualification remains unrun.'),
('180', 'implemented-test-needed', 'GlanceView labels actual source date, UTC reporting day and source timestamp, marks failed-refresh data as a saved snapshot, removes Today wording and disables refresh while busy.', 'Yesterday-date, timezone and watch render acceptance remain unrun.'),
('181', 'implemented-test-needed', 'PlanTreeVolume stores only selectedId and derives the selected node from model.forest; removal/revocation clears ID; errors render independently of selection. PlanTreeModelTests covers changed status then denial.', 'Native selection/error/accessibility acceptance remains unrun.'),
('182', 'implemented-test-needed', 'PlanTreeVolume provides a searchable, status-labelled text hierarchy with 50-row pages for all received nodes, accessible task selection and selected-card focus, plus fit/zoom controls. Spatial rendering caps at 200 and labels the complete alternate list.', '1/100/1000-node, VoiceOver and comfort/performance qualification remain unrun.'),
('183', 'implemented-test-needed', 'dashboardData streams URLSession bytes under a hard cap (64 KiB watch, 2 MiB tree), checks cancellation and resource timeout, and invalidates the session. Models own one request, reject concurrent refresh, cancel on connection change/disposal/background and fence late responses. Cap and rapid-refresh/cancellation regression sources authored.', 'Actual URLSession cancellation/streaming and native disposal acceptance remain unrun.'),
('184', 'deferred-decision', 'Mobile and Apple READMEs explicitly label preview source and unqualified native behavior. Watch README removes the undelivered complication claim. No signed installer or support qualification is claimed.', 'Signed customer distribution requires release-owner platform accounts/signing decisions and publication authorization; these are explicitly outside this task. Full compatibility matrix, generated app projects, native host qualification and signed packaging remain unfinished.'),
('185', 'planned', 'Mobile detail and vision selection now hand off exact task paths to separately authenticated dashboard browsers without tokens in URLs; denied access clears native private views. Clients remain deliberately read-only.', 'Short-lived device pairing, scoped device credentials/renewal/revocation UI and QR flow remain unimplemented. Current setup still requires manual credentials and does not satisfy pairing acceptance.'),
]
for ident, status, evidence, remaining in items:
    row = {'id': 'LW-'+ident, 'status': status, 'implementation_evidence': evidence, 'validation': validation, 'remaining': remaining}
    rows = [r for r in rows if r['id'] != row['id']]
    rows.append(row)
p.write_text(json.dumps(sorted(rows, key=lambda r:r['id']), indent=2, ensure_ascii=False)+'\n', encoding='utf-8', newline='\n')
for folder in ['apps/apple-companion-core', 'apps/watch-glance', 'apps/visionos-plan-tree']:
    for target in Path(folder).rglob('*'):
        if target.is_file() and target.suffix in {'.swift', '.md'}:
            raw = target.read_bytes().decode('utf-8')
            if '\r' in raw:
                target.write_text(raw.replace('\r',''), encoding='utf-8', newline='\n')
