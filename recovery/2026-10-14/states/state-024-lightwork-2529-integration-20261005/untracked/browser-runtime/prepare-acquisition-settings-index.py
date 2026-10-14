"""Stage qualified customer paths only, preserving newer unrelated working edits."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

checkout = Path(__file__).resolve().parents[1]
proofs = Path(sys.argv[1])
destination = Path(sys.argv[2])
assert not subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=checkout).strip()
for path in (proofs / '.browser-runtime/settings-catalog-frozen-manifest.json',
             proofs / '.browser-runtime/consent-withdrawal-frozen-manifest.json',
             checkout / '.browser-runtime/designer-teach-frozen-manifest.json',
             checkout / '.browser-runtime/operator-acquisition-frozen-manifest.json'):
    manifest = json.loads(path.read_text(encoding='utf-8'))
    rows = manifest.get('paths', manifest.get('source')) if isinstance(manifest, dict) else manifest
    for row in rows:
        content = (checkout / row['path']).read_bytes()
        assert len(content) > 50, (path, row['path'], 'unexpected empty source')
        assert hashlib.sha256(content).hexdigest() == row['sha256'], (path, row['path'])

new = ['packages/maverick-core/maverick/flow/operator_acquisition.py']
dashboard = 'packages/maverick-dashboard/maverick_dashboard/'
new += [dashboard + path for path in (
    'operator_acquisition.py', 'operator_acquisition_api.py', 'product_operations_operator_acquisition.py',
    'static/lightwork-operator-acquisition.js', 'templates/operator_acquisition.html',
    'consent_publication_withdrawal.py', 'consent_publication_withdrawal_api.py',
    'product_operations_consent_publication.py', 'viewpoints_requests.py', 'product_operations_viewpoints.py',
    'settings_catalog_requests.py', 'settings_catalog_requests_api.py', 'product_operations_settings_catalog.py',
    'static/lightwork-settings-catalog-requests.js')]
excluded = {dashboard + 'templates/goals.html', dashboard + 'product_operations_task_launch.py'}
tracked = subprocess.check_output(['git', 'diff', '--name-only'], cwd=checkout, text=True).splitlines()
paths = []
for path in tracked:
    if path in excluded:
        continue
    before = subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=checkout)
    current = (checkout / path).read_bytes()
    normalized = lambda value: value.replace(b'\r\n', b'\n').rstrip(b'\n')
    if normalized(before) != normalized(current):
        paths.append(path)
paths += new
assert all((checkout / path).stat().st_size > 50 for path in new)
assert len(paths) == len(set(paths)), paths
subprocess.run(['git', 'add', '--', *paths], cwd=checkout, check=True)
(destination / 'staged_manifest.json').write_text(json.dumps(paths, indent=2), encoding='utf-8')
print(f'Qualified path index staged: {len(paths)} files. Future Tasks/region/evolution work and proof helpers excluded.')
