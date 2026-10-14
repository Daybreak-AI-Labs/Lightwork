import ast
import subprocess
from pathlib import Path

names = subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines()
names += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines()
checked = 0
for name in names:
    if name.startswith('.audit-'):
        continue
    path = Path(name)
    if path.suffix not in {'.py', '.js', '.html', '.md', '.json'}:
        continue
    source = path.read_text(encoding='utf-8')
    path.write_text(source, encoding='utf-8', newline='\n')
    if path.suffix == '.py':
        ast.parse(source, filename=name)
        checked += 1
print('Python AST:', checked, 'files. Application and tests not executed.')
