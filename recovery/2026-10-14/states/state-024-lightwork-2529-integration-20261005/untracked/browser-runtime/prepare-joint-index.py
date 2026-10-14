from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parent.parent
build = root.parent / 'local-build-joint-request-economics-20261006'
core = 'packages/maverick-core/maverick/'
dash = 'packages/maverick-dashboard/maverick_dashboard/'
paths = [
    'apps/installer-cli/maverick_installer/wizard.py',
    'docs/FEATURES.md', 'docs/post-install-operation-coverage.md',
    'docs/research/lightwork-invention-implementation-status-2026-10-01.md',
    'docs/research/lightwork-invention-register.md',
    core + 'config.py', core + 'recovery_authorities.py',
    'packages/maverick-core/tests/test_features_wiring.py',
]
paths += [dash + name for name in (
    'app.py', 'commitment_viability_api.py', 'copilot_context.py',
    'page_assets.py', 'plain_labels.py', 'product_operations.py',
    'product_operations_workflows.py', 'settings_catalog.py', 'settings_store.py',
    'ui_visibility.py', 'static/lightwork-product-operations.js',
    'static/lightwork-ui.js', 'templates/commitment_viability.html',
    'templates/flow_designer.html', 'templates/flow_designer.js',
    'templates/flow_designer_core.js', 'templates/learning.html',
    'workflow_step_checks.py', 'static/lightwork-workflow-step-checks.js',
)]
for name in ('procedure_interactions', 'request_program'):
    paths += [core + 'flow/' + name + '.py']
    paths += [dash + name + '.py', dash + name + '_api.py',
              dash + 'product_operations_' + name + '.py',
              dash + 'static/lightwork-' + name.replace('_', '-') + '.js',
              dash + 'templates/' + name + '.html']
paths += [core + 'commitment_economic_alternatives.py']
paths += [dash + name for name in (
    'commitment_economic_alternatives_store.py',
    'commitment_economic_alternatives_api.py',
    'product_operations_commitment_economic_alternatives.py',
    'static/lightwork-commitment-economic-alternatives.js',
    'templates/_commitment_economic_alternatives.html',
)]
assert len(paths) == len(set(paths))
assert all((root / path).is_file() for path in paths)
previous = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=root, text=True).splitlines()
assert set(previous) <= set(paths), previous
subprocess.run(['git', 'add', '--', *(path for path in paths if path not in previous)], cwd=root, check=True)
(build / 'staged_manifest.json').write_text(json.dumps(sorted(paths), indent=2), encoding='utf-8')
(build / 'working_source_hashes.json').write_text(json.dumps({
    path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths
}, indent=2), encoding='utf-8')
print(f'Staged {len(paths)} exact current-batch paths; other ongoing work excluded.')
