import ast
import json
from pathlib import Path
from maverick.recovery_authorities import authority_policy, CUSTOMER_FILE_AUTHORITY_POLICIES

p = Path('docs/operations/persistence-authorities.json')
data = json.loads(p.read_text(encoding='utf8'))
classification = {
    'recovery_lookup': 'read_projection', 'budget_guidance': 'preferences',
    'conversation_inbox': 'execution', 'deliverable_bundles': 'governed',
    'employee_companion': 'configuration', 'experience_preferences': 'preferences',
    'goal_archive': 'transient', 'installation_offline': 'recovery',
    'knowledge_reindex_jobs': 'execution', 'knowledge_task_context': 'execution',
    'learning_content': 'learning', 'model_tests': 'execution',
    'notification_receipts': 'execution', 'personal_identity': 'configuration',
    'personal_learning': 'learning', 'personal_memory': 'learning',
    'personal_notifications': 'execution', 'personal_notification_delivery': 'execution',
    'personal_notification_recipients': 'execution', 'plugin_execution_pins': 'execution',
    'plugin_workbench': 'execution', 'project_workspaces': 'mixed_api',
    'runtime_configuration': 'configuration', 'task_execution_guard': 'composition',
    'task_handoffs': 'execution', 'workflow_versions': 'configuration',
    'preservation': 'read_projection', 'billing_sync_reviews': 'governed',
    'budget_guidance_api': 'governed', 'clarification_submission': 'composition',
    'content_access': 'configuration', 'conversation_organization': 'preferences',
    'deliverable_sharing': 'governed', 'device_pairing': 'configuration',
    'employee_activation': 'execution', 'employee_departures': 'configuration',
    'goal_share_previews': 'execution', 'permission_requests': 'governed',
    'personal_data_requests': 'execution', 'plugin_workbench_api': 'composition',
    'retention_runs': 'execution', 'settings_experience': 'read_projection',
    'setup_guide': 'configuration', 'task_diagnostics': 'governed', 'build': 'artifact',
}
file_samples = {
    'employee_companion': ['employee_companions/record.enc'],
    'personal_identity': ['personal-data/identity-links.enc'],
    'personal_memory': ['personal-memory/record.enc'],
    'personal_notifications': ['personal-notifications/record.enc'],
    'personal_notification_delivery': ['personal-notifications/deliveries/owner/record.enc'],
    'personal_notification_recipients': ['personal-notifications/recipients.enc'],
    'device_pairing': ['device-pairing/registry.enc'], 'model_tests': ['model-tests/record.json'],
    'knowledge_reindex_jobs': ['knowledge-reindex/context/job.json'],
    'knowledge_task_context': ['knowledge-task-context/1.json'],
    'plugin_execution_pins': ['plugin-flow-pins/run.json', 'plugin-task-pins/1.json'],
    'plugin_workbench': ['plugin-workbench/context.json'],
    'goal_share_previews': ['goal-share-previews/context/review.enc'],
    'retention_runs': ['retention-runs/run.json'],
    'notification_receipts': ['notification-deliveries.json'],
    'learning_content': ['custom/example.ownership.json'],
}
for entry in json.loads(Path('../audit/missing-persistence.json').read_text()):
    path = Path(entry['path'])
    tree = ast.parse(path.read_text(encoding='utf8'))
    constants = {target.id: node.value.value for node in tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name) and target.id.isupper()
                 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
    namespaces = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'GovernedRecordStore' or not node.args:
            continue
        arg = node.args[0]
        namespace = arg.value if isinstance(arg, ast.Constant) else constants.get(arg.id) if isinstance(arg, ast.Name) else None
        if isinstance(namespace, str):
            namespaces.append({'namespace': namespace, 'line': node.lineno,
                               'recovery_policy': authority_policy(namespace + '/record.json', is_sqlite=False)})
    item = {
        'path': entry['path'], 'contract': classification[path.stem], 'owner': entry['path'],
        'purpose': (entry['purpose'] or 'Explicit build artifact publication.').split('\n\n')[0],
        'referenced_store_owners': ['maverick.governed_records'] if namespaces else [],
        'review': 'Customer-audit source purpose, path construction and persistence call sites reviewed; exact restore policy registered. Local regression evidence is recorded separately; no distributed qualification claimed.',
        'governed_namespaces': namespaces, 'literal_path_hints': [], 'observed_mechanism_markers': {},
        'source_sites': [dict(line=s['line'], function=s['function'], kind=s['call']) for s in entry['source_sites']],
    }
    samples = file_samples.get(path.stem, []) + [n['namespace'] + '/record.json' for n in namespaces if n['recovery_policy']]
    if samples:
        item['persisted_authorities'] = [{'sample_path': s, 'recovery_policy': authority_policy(s, is_sqlite=False),
                                        'disposition': 'Retain authenticated bytes under global startup hold; reconcile current consent, source/revision and consumed operation receipts before admission. No automatic replay or grant restoration.'} for s in samples]
    if path.stem == 'conversation_organization':
        item['review'] += ' The bounded conversation-organization-<authenticated-context> namespace contains private labels/pin/archive data only; restoring it grants no source access or execution.'
    data['entries'].append(item)
data['entries'].sort(key=lambda e:e['path'])
p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
