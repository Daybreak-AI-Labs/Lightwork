import ast
import json
import re
from pathlib import Path
from maverick_dashboard.i18n_journeys import ROWS
known={r[0] for r in ROWS}
values=[]
for name in ('lightwork-voice-setup.js','lightwork-read-aloud.js'):
    s=(Path('packages/maverick-dashboard/maverick_dashboard/static')/name).read_text(encoding='utf8')
    s=re.sub(r'/\*.*?\*/','',s,flags=re.S)
    s=re.sub(r'^\s*//.*','',s,flags=re.M)
    for m in re.finditer(r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*' ''',s,re.X):
        values.append(ast.literal_eval(m.group()))
print(json.dumps(sorted({v for v in values if len(v)>12 and ' ' in v and v not in known}),ensure_ascii=True,indent=2))
