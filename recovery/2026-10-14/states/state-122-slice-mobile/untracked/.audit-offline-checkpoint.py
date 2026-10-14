import ast,json,subprocess
from pathlib import Path
names=subprocess.check_output(['git','diff','--name-only'],text=True).splitlines()
names += ['packages/maverick-core/maverick/mobile_offline_policy.py','packages/maverick-core/tests/test_mobile_offline_policy.py']
count=0
for name in names:
 p=Path(name);s=p.read_text(encoding='utf-8');p.write_text(s,encoding='utf-8',newline='\n')
 if p.suffix=='.py':ast.parse(s,filename=name);count+=1
print(f'Python AST {count} files; no execution of application/tests')
p=Path('docs/ux-audit-2026-09-30/slices/mobile.json');rows=json.loads(p.read_text(encoding='utf-8'))
row=next(r for r in rows if r['id']=='LW-056')
row.update(status='implemented-test-needed',implementation_evidence='Existing confirmed disconnect/reset plus credential-scoped purge now remove all app offline-cache namespaces. mobile_offline_policy.py combines installation/workspace disablement and minimum age/byte ceilings, exposed in All settings and installer. Server bundle includes versioned policy and requires policy-aware client header. Mobile refuses missing/invalid policy, uses source-time expiry, invalidates live cached readers on policy/purge/expiry, serializes writes/removal and fences retired scopes/epochs/global reset. Readiness gives organization-policy message; user cannot relax ceilings.',validation=f'2026-09-30: Python AST {count} files and TypeScript static analysis of all15 mobile source files passed (zero diagnostics). JavaScript regression source syntax and git diff --check passed. Policy ceiling/denial and cache expiry/late-write regressions authored unrun. No test suites/native/provider/browser tests run.',remaining='Device storage/timer/background qualification is unrun. Offline devices learn changed policy only on reconnect; previously accepted policy expiry bounds that interval. Existing unqualified old clients must upgrade; server refuses bundle requests without the policy protocol header. No claims about retroactively deleting pre-upgrade copies on disconnected older builds.')
p.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
