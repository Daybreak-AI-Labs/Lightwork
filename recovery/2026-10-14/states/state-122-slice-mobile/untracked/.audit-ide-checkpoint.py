from pathlib import Path
import shutil
repo=Path(__file__).resolve().parent
for path in (repo.parent/'audit/jetbrains-build').glob('*gradle.lockfile'):
    shutil.copy2(path,repo/'apps/jetbrains-plugin'/path.name)
folders=[repo/'apps/jetbrains-plugin',repo/'apps/zed-extension']
for folder in folders:
    for path in folder.rglob('*'):
        if not path.is_file() or any(p in {'.gradle','target','build','.intellijPlatform'} for p in path.relative_to(folder).parts):
            continue
        if path.suffix=='.jar': continue
        raw=path.read_bytes()
        try: raw.decode('utf-8')
        except UnicodeDecodeError: continue
        path.write_bytes(raw.replace(b'\r\n',b'\n'))
path=repo/'docs/native-companion-release.md'
path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n'))
