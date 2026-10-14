import ast
import json
from pathlib import Path
import re
import subprocess
root=Path('packages/maverick-dashboard/maverick_dashboard')
known=set()
for p in root.glob('i18n_journey_*.py'):
    for n in ast.parse(p.read_text(encoding='utf8')).body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ROWS' for t in n.targets):
            known.update(row[0] for row in ast.literal_eval(n.value))
values=set()
for name in ('lightwork-task-file-recovery.js','lightwork-project-reviews.js'):
    s=(root/'static'/name).read_text(encoding='utf8')
    for m in re.finditer(r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*' ''',s,re.X):
        v=ast.literal_eval(m.group())
        if (' ' in v or v in {'Pending','Approved','Cancelled','Sent','Comment','Done','Failed','Stopped','someone','overdue'}) and v not in known and not v.startswith(('Could not ','.','[','\\','Remove ')):
            values.add(v)
    # A prior raw error must not mark a later product status as user data.
    s=s.replace('=>{if(window.LightworkI18n)', '=>{delete node.dataset.userContent;if(window.LightworkI18n)') if name.endswith('recovery.js') else s.replace('values={}){if(window.LightworkI18n)', 'values={}){delete value.dataset.userContent;if(window.LightworkI18n)')
    (root/'static'/name).write_text(s,encoding='utf8',newline='\n')
print(json.dumps(sorted(values),ensure_ascii=False,indent=2))
for name in subprocess.check_output(['git','diff','--name-only'],text=True).splitlines():
    p=Path(name)
    if p.is_file():
        p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))
