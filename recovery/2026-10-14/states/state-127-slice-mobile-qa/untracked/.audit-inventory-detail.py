import ast
import json
from pathlib import Path

p = Path('docs/operations/persistence-authorities.json')
d = json.loads(p.read_text(encoding='utf8'))
paths = {r['path'] for r in json.loads(Path('../audit/missing-persistence.json').read_text())}
d['customer_audit_additions'] = {
    'base_sha': 'd822db99ee48aab4fdded47fc799486e8e5ea44f',
    'module_count': len(paths),
    'review': 'September 30 customer-audit additions: source purpose, path construction and persistence call sites. Restore holds cover grants, identity links, dispatch receipts and pending work; warning/project-default records are explicitly non-authorizing data. No distributed or live-provider qualification.',
}
for e in d['entries']:
    if e['path'] not in paths:
        continue
    tree = ast.parse(Path(e['path']).read_text(encoding='utf8'))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    old_seams = {(s['line'], s['kind']) for s in e['source_sites']}
    sites = []
    for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)), key=lambda n:(n.lineno, n.col_offset)):
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
        if (node.lineno, name) not in old_seams:
            continue
        parent = node
        while parent in parents and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = parents[parent]
        sites.append({'line': node.lineno, 'function': getattr(parent, 'name', '<module>'),
                      'kind': name, 'expression': ast.unparse(node)})
    e['source_sites'] = sites
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
