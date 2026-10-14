from pathlib import Path
import subprocess
for name in subprocess.check_output(['git','diff','--name-only']).decode().splitlines():
 p=Path(name); s=p.read_bytes().decode('utf-8','surrogateescape')
 s=''.join(bytes([ord(c)-0xdc00]).decode('cp1252') if 0xdc80 <= ord(c) <= 0xdcff else c for c in s)
 p.write_text(s,encoding='utf-8')
