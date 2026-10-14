from pathlib import Path
p=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\local-build-local-learning-completion-20261006\\pr-body.md")
s=p.read_text(encoding='utf-8')
s=s.replace('core/dashboard wheels','core/dashboard/channels wheels').replace('292 registered operations','314 registered operations').replace('37 served assets','41 served assets').replace('24 Python modules and four JavaScript files','40 Python modules and six JavaScript files')
p.write_text(s,encoding='utf-8')
print('Published artifact summary now matches the verified current three-wheel build.')

