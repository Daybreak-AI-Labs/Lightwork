import ast,json,re,subprocess
from pathlib import Path
paths=subprocess.check_output(['git','diff','--name-only'],text=True).splitlines()
paths += [n for n in subprocess.check_output(['git','ls-files','--others','--exclude-standard'],text=True).splitlines() if not n.startswith('.audit-')]
py=js=0
for name in dict.fromkeys(paths):
 p=Path(name)
 if p.suffix not in {'.py','.js','.html','.md','.json'}:continue
 source=p.read_text(encoding='utf-8');p.write_text(source,encoding='utf-8',newline='\n')
 if p.suffix=='.py':ast.parse(source,filename=name);py+=1
 if p.suffix=='.js':subprocess.run(['node','--check',str(p)],check=True);js+=1
print(f'Python AST {py}; JavaScript syntax {js}; no tests executed')
p=Path('docs/ux-audit-2026-09-30/slices/mobile.json');rows=json.loads(p.read_text(encoding='utf-8'))
for row in rows:
 if row['id']=='LW-044':
  row.update(status='implemented-test-needed',implementation_evidence='Settings worker commit6eaca67f1c16dd1b549e4969fb4827fa46b4fa92 supplies account/device in-place display preferences, OS system mode with live matchMedia changes, explicit theme override, shared base/chat display menu, font/density/text-size presets and rollback/Undo.',remaining='Parent must integrate settings worker checkpoint with this slice. Browser/OS-follow/max-text qualification remains unrun.')
 if row['id']=='LW-045':
  row['status']='implemented-test-needed';row['implementation_evidence']+=' Settings worker6eaca67 supplies shared prior-theme Undo through the same revision-bound preferences endpoint.';row['remaining']='Integrate settings worker6eaca67 and mobile asset-registration38fcc2f together. Browser/background/contrast qualification remains unrun.'
 if row['id']=='LW-141':
  row.update(status='planned',implementation_evidence='personal_notifications.py validates recipient-scoped subscriptions, IANA quiet hours/daily digest, explicit organization+user critical exceptions, per-task mute and project watches; encrypted bounded personal inbox deduplicates semantic versions, rehydrates only current authorized source labels, purges revoked sources and separates available/seen. /notifications UI provides direct preference/CAS/recovery journey with own-task/assigned-decision projection. Config and installer controls added; foundation delegation supplied by settings e3ad241.',validation=f'2026-09-30: Python AST {py} files, standalone JavaScript syntax {js} files and diff whitespace passed. Five DST/recipient/dedupe/mute/revocation regressions authored but unrun. No runtime/browser/provider tests or outgoing notifications executed.',remaining='External per-person recipient/channel routing and receipts, request-less background event ingestion/delivery, transitions between page refreshes, full-history pagination and shared Copilot operation adapter remain unfinished. Implemented in-product inbox observes at most latest200 owned tasks plus bounded assigned decisions and explicitly explains visible-page polling/catch-up, not closed-page external delivery. Requires settings6eaca67 plus e3ad241 preference adapter integration.')
p.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
