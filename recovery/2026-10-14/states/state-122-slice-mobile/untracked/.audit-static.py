from pathlib import Path
import ast, subprocess, re
files = subprocess.check_output(['git','diff','--name-only'],text=True).splitlines()
files += ['packages/maverick-dashboard/tests/test_theme_contrast.py','packages/maverick-dashboard/maverick_dashboard/static/lightwork-read-aloud.js','packages/maverick-dashboard/maverick_dashboard/static/lightwork-theme-preview.js','packages/maverick-dashboard/tests/js/audit_accessibility.test.js','apps/mobile-companion/tests/audit-recovery.test.cjs']
pycount=jscount=0
for name in files:
 p=Path(name);s=p.read_text(encoding='utf-8')
 if p.suffix == '.py':ast.parse(s,filename=name);pycount+=1
 if p.suffix in ('.js','.cjs'):
  subprocess.run(['node','--check',name],check=True);jscount+=1
 if p.suffix=='.html':
  for i,script in enumerate(re.findall(r'<script\b[^>]*>([\s\S]*?)</script>',s,re.I)):
   if not script.strip():continue
   script=re.sub(r'\{%[\s\S]*?%\}','',script)
   script=re.sub(r'\{\{[\s\S]*?\}\}','0',script)
   target=Path('.audit-script.js');target.write_text(script,encoding='utf-8',newline='\n')
   result=subprocess.run(['node','--check',str(target)],capture_output=True,text=True)
   if result.returncode:raise RuntimeError(f'{name} inline {i}: {result.stderr}')
   jscount+=1
print(f'Python AST: {pycount} files; JavaScript syntax: {jscount} standalone and template blocks. No tests executed.')
