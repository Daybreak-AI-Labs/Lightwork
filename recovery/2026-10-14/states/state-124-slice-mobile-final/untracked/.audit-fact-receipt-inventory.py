import ast
import json
from pathlib import Path

root = Path(__file__).parent
relative = 'packages/maverick-dashboard/maverick_dashboard/product_operations_facts.py'
tree = ast.parse((root / relative).read_text(encoding='utf-8'))
constructor = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == 'GovernedRecordStore')
path = root / 'docs/operations/persistence-authorities.json'
inventory = json.loads(path.read_text(encoding='utf-8'))
assert not any(entry['path'] == relative for entry in inventory['entries'])
inventory['entries'].append({
    'path': relative,
    'contract': 'execution',
    'owner': relative,
    'purpose': 'Durable admission and completion receipts prevent replay of an exact reviewed Facts mutation, including after a fact is subsequently removed.',
    'referenced_store_owners': ['maverick.governed_records', 'maverick.world_model'],
    'review': 'Reviewed Facts adapter admission-before-effect, context/actor/operation digest binding, native revision CAS and historical receipt recovery (source fa3cd13019fea8ba44bc434d778e32e6f62bdd1a). Receipt metadata contains no fact name/value. Restored receipt absence or stale completion cannot authorize another effect; hold startup until current authority and consumed-operation state are reconciled. Retained minimal replay evidence is disclosed in My Data.',
    'governed_namespaces': [{'namespace': 'fact_operation_receipts', 'line': constructor.lineno, 'recovery_policy': 'fact_operation_receipt_authority'}],
    'literal_path_hints': [],
    'observed_mechanism_markers': {},
    'source_sites': [{'line': constructor.lineno, 'function': '<module>', 'kind': 'GovernedRecordStore', 'expression': ast.unparse(constructor)}],
    'persisted_authorities': [{'sample_path': 'fact_operation_receipts/FOP-example.json', 'recovery_policy': 'fact_operation_receipt_authority', 'disposition': 'Retain authenticated receipt bytes under global startup hold. Reconcile current source/revision, consent and consumed-operation evidence before admission; no automatic replay, grant restoration or inference of completion from matching fact values.'}],
})
path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
