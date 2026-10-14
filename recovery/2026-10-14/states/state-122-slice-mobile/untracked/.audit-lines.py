from pathlib import Path
import subprocess
for name in subprocess.check_output(['git','diff','--name-only']).decode().splitlines():
 p=Path(name); s=p.read_bytes().decode('utf-8').replace('\r','')
 p.write_text(s,encoding='utf-8',newline='\n')
