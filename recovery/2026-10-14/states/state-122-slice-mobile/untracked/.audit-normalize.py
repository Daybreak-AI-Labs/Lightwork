from pathlib import Path
import subprocess
for name in subprocess.check_output(['git','diff','HEAD^','HEAD','--name-only'],text=True).splitlines():
 p=Path(name)
 if p.is_file():p.write_text(p.read_bytes().decode('utf-8').replace('\r',''),encoding='utf-8',newline='\n')
