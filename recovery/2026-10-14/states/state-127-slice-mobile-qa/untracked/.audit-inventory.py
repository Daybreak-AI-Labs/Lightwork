import ast
import json
from pathlib import Path

repo = Path.cwd()
data = json.loads((repo / 'docs/operations/persistence-authorities.json').read_text())
entries = {r['path']: r for r in data['entries']}
ns = {}
exec(compile((repo / 'packages/maverick-core/tests/test_persistence_authority_inventory.py').read_text(), '<inventory>', 'exec'), {'__file__': str(repo / 'packages/maverick-core/tests/test_persistence_authority_inventory.py')}, ns)
seams = ns['PERSISTENCE_SEAMS']
missing = []
for root in [*map(lambda p: p / p.name.replace('-', '_'), []), repo / 'packages', repo / 'apps']:
    for path in root.rglob('*.py'):
        if any(p in ('tests', 'fixtures', 'build', 'node_modules', '.gradle') for p in path.parts) or path.name.startswith('test_'):
            continue
        relative = path.relative_to(repo).as_posix()
        if relative in entries:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = {}
        for n in ast.walk(tree):
            for ch in ast.iter_child_nodes(n): parents[ch] = n
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call): continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name not in seams: continue
            parent = node
            while parent in parents and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents[parent]
            sites.append({'line': node.lineno, 'function': getattr(parent, 'name', '<module>'), 'call': name})
        if sites:
            missing.append({'path': relative, 'purpose': ast.get_docstring(tree), 'source_sites': sites})
(repo.parent / 'audit/missing-persistence.json').write_text(json.dumps(missing, indent=2))
print(json.dumps(missing, indent=2))
