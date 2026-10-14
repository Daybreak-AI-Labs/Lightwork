import ast, json, re, subprocess
from pathlib import Path
paths=subprocess.check_output(['git','diff','--name-only'],text=True).splitlines()
paths += [n for n in subprocess.check_output(['git','ls-files','--others','--exclude-standard'],text=True).splitlines() if not n.startswith('.audit-')]
py=js=0
for name in dict.fromkeys(paths):
 p=Path(name)
 if p.suffix not in {'.py','.js','.cjs','.html','.md','.json'}:continue
 source=p.read_text(encoding='utf-8');p.write_text(source,encoding='utf-8',newline='\n')
 if p.suffix=='.py':ast.parse(source,filename=name);py+=1
 if p.suffix in {'.js','.cjs'}:subprocess.run(['node','--check',str(p)],check=True);js+=1
 if p.suffix=='.html':
  for i,script in enumerate(re.findall(r'<script\b[^>]*>([\s\S]*?)</script>',source,re.I)):
   if not script.strip():continue
   script=re.sub(r'\{%[\s\S]*?%\}','',script);script=re.sub(r'\{\{[\s\S]*?\}\}','0',script)
   q=Path('.audit-script.js');q.write_text(script,encoding='utf-8',newline='\n')
   subprocess.run(['node','--check',str(q)],check=True);js+=1
print(f'Python AST {py}; JavaScript syntax {js}; no tests executed')
p=Path('docs/ux-audit-2026-09-30/slices/mobile.json');rows=json.loads(p.read_text(encoding='utf-8'))
for row in rows:
 if row['id'] in {'LW-043','LW-184'}:row['status']='planned'
 if row['id']=='LW-142':
  row.update(status='planned',implementation_evidence='notification_receipts.py persists bounded content-free tenant receipts with queued/sending/accepted/failed/unknown state, dedupe and three-attempt safe test retry ceiling. notifications.py records actual backend response, preserves tenant context in async workers and sanitizes secrets; notify tool says queued, PushRegistry waits for actual response. Authorized /settings/notifications direct UI + health/test/retry API + config/installer control added; sender rechecks current admin authority and config before dispatch.',validation=f'2026-09-30: Python AST {py} files and JavaScript syntax {js} standalone/template blocks passed; git diff --check passed. Receipt/async queue/idempotency tests authored but unrun. No notification delivery/provider/browser calls or test suites run.',remaining='Direct delivery UI/service implemented; Copilot operation catalog adapter absent (existing catalog supports only closed learning-example actions). Authenticated health/retry/browser and actual service delivery validation deferred. Personal preference/digest integration belongs to LW141.')
 if row['id'] in {'LW-143','LW-144','LW-145'}:
  row['remaining']='Root accepted this budget tranche for the next implementation wave; '+row['remaining']
p.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
