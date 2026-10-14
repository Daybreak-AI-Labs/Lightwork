import json, urllib.request, hashlib, zipfile
from pathlib import Path
root=Path(__file__).resolve().parent.parent/'audit/native-tools'
root.mkdir(parents=True,exist_ok=True)
def read(url,cap=2_000_000):
 with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Lightwork-local-build-check"}),timeout=40) as response:
  data=response.read(cap+1)
  if len(data)>cap:raise ValueError('metadata limit')
  return data
release=json.loads(read('https://api.github.com/repos/adoptium/temurin21-binaries/releases/tags/jdk-21.0.8%2B9'))
asset=next(row for row in release['assets'] if row['name']=='OpenJDK21U-jdk_x64_windows_hotspot_21.0.8_9.zip')
checksum_asset=next(row for row in release['assets'] if row['name']==asset['name']+'.sha256.txt')
jdk={'link':asset['browser_download_url'],'checksum':read(checksum_asset['browser_download_url']).decode().strip().split()[0]}
gradle_sha=read('https://services.gradle.org/distributions/gradle-8.13-bin.zip.sha256').decode().strip()
manifest={'jdk':{'url':jdk['link'],'sha256':jdk['checksum'],'name':'jdk.zip'},'gradle':{'url':'https://services.gradle.org/distributions/gradle-8.13-bin.zip','sha256':gradle_sha,'name':'gradle.zip'}}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest),flush=True)
for name,item in manifest.items():
 archive=root/item['name'];digest=hashlib.sha256()
 if not archive.exists():
  count=0
  with urllib.request.urlopen(urllib.request.Request(item['url'],headers={'User-Agent':'Lightwork-local-build-check'}),timeout=60) as response, archive.open('wb') as target:
   while chunk:=response.read(1024*1024):
    count+=len(chunk)
    if count>400_000_000:raise ValueError('archive limit')
    target.write(chunk)
 with archive.open('rb') as source:
  while chunk:=source.read(1024*1024):digest.update(chunk)
 if digest.hexdigest()!=item['sha256']:raise ValueError('checksum mismatch '+name)
 with zipfile.ZipFile(archive) as source:
  for member in source.infolist():
   target=(root/member.filename).resolve()
   if not target.is_relative_to(root.resolve()):raise ValueError('unsafe archive path')
  source.extractall(root)
 print(name+' downloaded, checksum verified and extracted within task workspace',flush=True)
