import ast
import hashlib
import json
from pathlib import Path

root = Path(__file__).parent
inventory_path = root / 'docs/operations/persistence-authorities.json'
inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
source_test = root / 'packages/maverick-core/tests/test_persistence_authority_inventory.py'
seams_node = next(node for node in ast.parse(source_test.read_text(encoding='utf-8')).body
                  if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PERSISTENCE_SEAMS' for t in node.targets))
seams = set(ast.literal_eval(seams_node.value.args[0]))
digest = hashlib.sha256(b'acme').hexdigest()
claim = '.tenant-namespaces/' + digest[:2] + '/' + digest + '.json'
deletion = '.tenant-deletions/' + digest + '.json'
scopes = {
    'packages/maverick-core/maverick/paths.py': [(claim, 'tenant_namespace_reservation')],
    'packages/maverick-core/maverick/tenant/registry.py': [(deletion, 'tenant_deletion_tombstone'), ('tenant_registry.json', 'tenant_registry')],
    'packages/maverick-core/maverick/backup.py': [(claim, 'tenant_namespace_reservation'), (deletion, 'tenant_deletion_tombstone')],
}
for entry in inventory['entries']:
    if entry['path'] not in scopes:
        continue
    tree = ast.parse((root / entry['path']).read_text(encoding='utf-8'))
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    sites = []
    for node in sorted(ast.walk(tree), key=lambda n: getattr(n, 'lineno', 0)):
        if not isinstance(node, ast.Call):
            continue
        kind = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
        if kind not in seams:
            continue
        enclosing = [f for f in functions if f.lineno <= node.lineno <= f.end_lineno]
        function = max(enclosing, key=lambda f: f.lineno).name if enclosing else '<module>'
        sites.append({'line': node.lineno, 'function': function, 'kind': kind, 'expression': ast.unparse(node)})
    entry['source_sites'] = sites
    entry['owner'] = entry['path']
    entry['review'] += ' Tenant lifecycle recovery review: installation-wide immutable claims and deletion tombstones are protected anti-reuse authority, outside purged tenant directories. Exact snapshot comparison, atomic no-replace restore publication and monotonic rollback preserve current barriers; bootstrap retains bytes under startup hold. No restored absence grants current admission.'
    entry.setdefault('persisted_authorities', []).extend([
        {'sample_path': path, 'recovery_policy': policy,
         'disposition': 'Preserve current identity history across purge, full/additive restore and failed-transaction rollback. Refuse erasure or rewrite of a current barrier; hold restored authority for current reconciliation, never automatic tenant readmission.'}
        for path, policy in scopes[entry['path']]
    ])
    entry['literal_path_hints'] = sorted(set(entry['literal_path_hints']) | {path.split('/')[0] for path, _ in scopes[entry['path']]})
inventory_path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
for name in ['docs/workspace-recovery.md']:
    path = root / name
    path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
