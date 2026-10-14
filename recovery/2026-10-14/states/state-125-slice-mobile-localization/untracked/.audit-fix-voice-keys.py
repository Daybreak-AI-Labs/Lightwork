from pathlib import Path
p=Path('packages/maverick-dashboard/maverick_dashboard/i18n_journey_voice_controls.py')
s=p.read_text(encoding='utf8')
for old,new in [('Checking the existing model file and verifying its bytes.','Checking the existing model file and verifying its bytes…'),('Downloading model.','Downloading model…'),('Cancelling. Stopping the download and cleaning up.','Cancelling… Stopping the download and cleaning up.')]:
    s=s.replace('("'+old+'",','("'+new+'",')
p.write_text(s,encoding='utf8',newline='\n')
p=Path('packages/maverick-dashboard/tests/js/voice_setup.test.js')
p.write_text(p.read_text(encoding='utf8').replace("'Downloading model.'","'Downloading model…'"),encoding='utf8',newline='\n')
