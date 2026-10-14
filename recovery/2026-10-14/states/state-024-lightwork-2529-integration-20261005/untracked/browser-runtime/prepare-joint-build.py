from pathlib import Path
import shutil
old=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\local-build-local-learning-completion-20261006")
new=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\local-build-joint-request-economics-20261006")
assert not new.exists()
new.mkdir()
for name in ('freeze_index.py','snapshot_static.py','installed_probe.py','linux_installed_probe.py'):
    shutil.copyfile(old/name,new/name)
(new/'linux-dependencies').mkdir()
for path in (old/'linux-dependencies').glob('*.whl'):
    shutil.copyfile(path,new/'linux-dependencies'/path.name)
probe=(new/'installed_probe.py').read_text(encoding='utf-8')
probe=probe.replace('== 314','== 331').replace('== 66','== 74')
probe=probe.replace('"/learning/recovery-visibility"):', '"/learning/recovery-visibility", "/learning/rules-together", "/learning/request-program"):')
probe=probe.replace("'tasks.private_check.author'):", "'tasks.private_check.author', 'learning.joint_rules_form', 'learning.run_joint_rules', 'learning.joint_rules_original', 'learning.request_program', 'learning.request_program_original', 'learning.workflow_step_check', 'commitments.economics.create', 'commitments.economics.check', 'commitments.economics.author', 'commitments.economics.original'):")
probe=probe.replace("'recovery_visibility'):", "'recovery_visibility', 'flow.procedure_interactions', 'flow.request_program', 'commitment_economic_alternatives'):")
probe=probe.replace("'lightwork-recovery-visibility.js')", "'lightwork-recovery-visibility.js', 'lightwork-procedure-interactions.js', 'lightwork-request-program.js', 'lightwork-workflow-step-checks.js', 'lightwork-commitment-economic-alternatives.js')")
probe=probe.replace("'recovery_visibility.html')","'recovery_visibility.html', 'procedure_interactions.html', 'request_program.html', '_commitment_economic_alternatives.html')")
(new/'installed_probe.py').write_text(probe,encoding='utf-8')
(new/'pr-body.md').write_text((old/'pr-body.md').read_text(encoding='utf-8'),encoding='utf-8')
print('Prepared isolated current-batch packaging helpers and offline Linux tokenizer dependency.')

