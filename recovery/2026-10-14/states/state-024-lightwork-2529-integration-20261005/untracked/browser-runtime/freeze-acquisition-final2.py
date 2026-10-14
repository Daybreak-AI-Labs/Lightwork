from pathlib import Path
import hashlib,json,shutil,subprocess
repo=Path(r'C:\Users\Cstep\Documents\Codex\2026-09-10\new-chat\work\lightwork-2529-integration-20261005')
prior=Path(r'C:\Users\Cstep\Documents\Codex\2026-09-10\new-chat\work\local-build-acquisition-settings-recovery-20261006')
final=Path(r'C:\Users\Cstep\Documents\Codex\2026-09-10\new-chat\work\local-build-acquisition-settings-recovery-20261006-final2')
path='packages/maverick-dashboard/maverick_dashboard/static/lightwork-draft-recovery.js'
assert hashlib.sha256((repo/path).read_bytes()).hexdigest()=='b4983e3ea0f432d352c0a457bd38683021e46b0cb18db152dd1d8f2d171a1631'
subprocess.run(['git','add','--',path],cwd=repo,check=True)
assert not final.exists();final.mkdir()
for name in ['staged_manifest.json','freeze_index.py','verify-staged-candidate.py','installed_probe.py','linux_installed_probe.py','installed_chat_recovery_server.py','installed-chat-long-draft-probe.cjs','pr-body.md']:
 shutil.copy2(prior/name,final/name)
subprocess.run(['py','-3.12',str(final/'freeze_index.py'),str(repo)],check=True)
(final/'wheels').mkdir()
for pattern in ['maverick_agent-*.whl','maverick_channels-*.whl']:
 for wheel in (prior/'wheels').glob(pattern):shutil.copy2(wheel,final/'wheels'/wheel.name)
shutil.copytree(prior/'linux-dependencies',final/'linux-dependencies')
s=(final/'installed_chat_recovery_server.py').read_text(encoding='utf-8').replace('port=8919','port=8920');(final/'installed_chat_recovery_server.py').write_text(s,encoding='utf-8')
s=(final/'installed-chat-long-draft-probe.cjs').read_text(encoding='utf-8').replace(':8919',':8920');(final/'installed-chat-long-draft-probe.cjs').write_text(s,encoding='utf-8')
with (final/'pr-body.md').open('a',encoding='utf-8') as f:f.write('\nThe installed native Chat recovery path now compares complete long drafts and preserves differing text after the first300 characters. Its responsive named values replace the squeezed comparison table; Cancel, delayed restore and newer edits retain the right draft without submitting a task. The preceding fresh-wheel two-installation transfer also recovered its exact original and executed exact ordinary/Copilot large answers with no shared storage or outside calls.\n')
print('Immutable final2 frozen with only the qualified shared Chat layout change; newer P04/P16/Tasks work preserved.')
