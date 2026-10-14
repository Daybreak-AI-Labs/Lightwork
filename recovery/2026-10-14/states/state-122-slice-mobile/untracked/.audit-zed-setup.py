import hashlib, io, json, pathlib, tarfile, urllib.request
root = pathlib.Path(__file__).resolve().parent.parent / 'audit/native-tools'
def read(url, cap=20_000_000):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Lightwork-local-build-check'}), timeout=60) as response:
        data = response.read(cap + 1)
        if len(data) > cap: raise ValueError('download bound')
        return data
data = read('https://static.crates.io/crates/zed_extension_api/zed_extension_api-0.7.0.crate')
metadata = json.loads(read('https://crates.io/api/v1/crates/zed_extension_api/0.7.0'))
assert hashlib.sha256(data).hexdigest() == metadata['version']['checksum']
with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as source:
    source.extractall(root / 'zed-api', filter='data')
print('Verified zed_extension_api0.7.0 SHA256', metadata['version']['checksum'])
print(read('https://static.rust-lang.org/dist/channel-rust-1.97.1.toml.sha256').decode())
release = json.loads(read('https://api.github.com/repos/mstorsjo/llvm-mingw/releases/latest'))
print(release['tag_name'])
for asset in release['assets']:
    if 'ucrt-x86_64.zip' in asset['name']:
        print(json.dumps({k: asset.get(k) for k in ('name','browser_download_url','digest','size')}))
