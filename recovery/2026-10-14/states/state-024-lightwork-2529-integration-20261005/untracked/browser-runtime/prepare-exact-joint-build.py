from pathlib import Path
import json
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
old = root.parent / 'local-build-joint-request-economics-20261006'
new = root.parent / 'local-build-joint-request-economics-20261006-final'
assert not new.exists()
new.mkdir()
for name in ('freeze_index.py', 'snapshot_static.py', 'installed_probe.py', 'linux_installed_probe.py', 'pr-body.md'):
    shutil.copyfile(old / name, new / name)
for name in ('linux-dependencies', 'wheels'):
    (new / name).mkdir()
    for path in (old / name).glob('*.whl'):
        if name != 'wheels' or not path.name.startswith('maverick_dashboard-'):
            shutil.copyfile(path, new / name / path.name)
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=root, text=True).splitlines()
assert len(paths) == 46, paths
(new / 'staged_manifest.json').write_text(json.dumps(sorted(paths), indent=2), encoding='utf-8')
print('Prepared exact-integer final export without modifying earlier qualified source or ongoing work.')
