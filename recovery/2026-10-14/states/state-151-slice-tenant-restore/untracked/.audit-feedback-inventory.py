import ast
import json
import subprocess
from pathlib import Path

root = Path(__file__).parent
source_sha = 'b6d19b46e831ce373dd97d8ec9e9c5186a366855'
upstream = root.parent / 'Lightwork-staging'
path = root / 'docs/operations/persistence-authorities.json'
inventory = json.loads(path.read_text(encoding='utf-8'))
ux_name = 'packages/maverick-core/maverick/ux_store.py'
source = subprocess.check_output(['git', '-C', str(upstream), 'show', source_sha + ':' + ux_name]).decode('utf-8')
entry = next(entry for entry in inventory['entries'] if entry['path'] == ux_name)
entry['literal_path_hints'] = sorted(set(entry['literal_path_hints']) | {'ux_store-v2.json', 'ux_store.json'})
entry['review'] += ' Follow-up reviewed against source ' + source_sha + ': default/shared stores read and write tenant-resolved dashboard/ux_store-v2.json. Legacy ux_store.json is deliberately retained unread because the former singleton did not establish reliable workspace lineage; no automatic adoption or migration by principal/goal match.'
entry['source_sites'] = []
tree = ast.parse(source)
functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    kind = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
    if kind not in {'data_dir', 'write_text', 'write_bytes', 'atomic_write_text', 'atomic_write_bytes'}:
        continue
    enclosing = max((f for f in functions if f.lineno <= node.lineno <= f.end_lineno), key=lambda f: f.lineno)
    entry['source_sites'].append({'line': node.lineno, 'function': enclosing.name, 'kind': kind, 'expression': ast.unparse(node)})
entry['source_sites'].sort(key=lambda site: site['line'])

feedback_name = 'packages/maverick-dashboard/maverick_dashboard/goal_feedback_store.py'
feedback_source = subprocess.check_output(['git', '-C', str(upstream), 'show', source_sha + ':' + feedback_name]).decode('utf-8')
tree = ast.parse(feedback_source)
call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'data_dir')
assert not any(e['path'] == feedback_name for e in inventory['entries'])
inventory['entries'].append({
    'path': feedback_name, 'contract': 'business_records', 'owner': feedback_name,
    'purpose': 'Tenant-resolved, owner-stamped human feedback on goal results; bounded-tail display and accumulated-signal counts, separate from governed signoff and execution approval.',
    'referenced_store_owners': ['maverick.ndjson_log', 'maverick.paths'],
    'review': 'Reviewed source ' + source_sha + ': _path delegates to data_dir("dashboard", "goal-feedback-v2.ndjson"); append_row and tail_rows retain private NDJSON semantics. This feedback projection is customer data, not an independent execution grant. The former file beside dashboard_overrides_path(), goal-feedback.ndjson, is retained unread: matching an owner string or colliding goal number cannot prove workspace lineage. Other learning stores and signoff authority retain their own controls.',
    'governed_namespaces': [],
    'literal_path_hints': ['dashboard/goal-feedback-v2.ndjson', 'goal-feedback.ndjson'],
    'observed_mechanism_markers': {},
    'source_sites': [{'line': call.lineno, 'function': '_path', 'kind': 'data_dir', 'expression': ast.unparse(call)}],
    'persisted_authorities': [{'sample_path': 'dashboard/goal-feedback-v2.ndjson', 'recovery_policy': None,
                             'disposition': 'Ordinary tenant customer data, without independent execution authority. Retain old deployment-global feedback unread; restoring or matching identifiers does not authorize adopting its lineage.'}],
})
inventory['customer_audit_additions']['module_count'] += 1
path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
