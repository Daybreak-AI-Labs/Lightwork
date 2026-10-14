import hashlib, os, pathlib, shutil, subprocess, sys, tarfile, urllib.request
try:
    import tomllib
except ImportError:
    import tomli as tomllib
repo = pathlib.Path(__file__).resolve().parent
root = repo.parent / 'audit/native-tools'
def read(url, cap=3_000_000):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Lightwork-local-build-check'}), timeout=60) as response:
        data = response.read(cap+1)
        if len(data)>cap: raise ValueError('metadata bound')
        return data
manifest = read('https://static.rust-lang.org/dist/channel-rust-1.97.1.toml')
expected = read('https://static.rust-lang.org/dist/channel-rust-1.97.1.toml.sha256').decode().split()[0]
assert hashlib.sha256(manifest).hexdigest() == expected
target = tomllib.loads(manifest.decode())['pkg']['rust-std']['target']['wasm32-wasip2']
toolchain = root / 'rust-1.97.1-gnullvm'
if not toolchain.exists():
    shutil.copytree(pathlib.Path('C:/Users/Cstep/.rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm'), toolchain)
archive = root / 'rust-std-1.97.1-wasm32-wasip2.tar.xz'
if not archive.exists():
    archive.write_bytes(read(target['xz_url'], 200_000_000))
assert hashlib.sha256(archive.read_bytes()).hexdigest() == target['xz_hash']
with tarfile.open(archive) as source:
    source.extractall(root / 'rust-wasi', filter='data')
installed = next((root/'rust-wasi').glob('*/rust-std-wasm32-wasip2/lib/rustlib/wasm32-wasip2'))
shutil.copytree(installed, toolchain/'lib/rustlib/wasm32-wasip2', dirs_exist_ok=True)
env = dict(os.environ, CARGO_HOME=str(root/'cargo-home'), RUSTC=str(toolchain/'bin/rustc.exe'), RUSTDOC=str(toolchain/'bin/rustdoc.exe'))
env['PATH'] = str(toolchain/'bin') + os.pathsep + env.get('PATH','')
env['TEMP'] = env['TMP'] = str(root/'gradle-tmp')
env['CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER'] = str(toolchain/'lib/rustlib/x86_64-pc-windows-gnullvm/bin/rust-lld.exe')
print('Verified Rust1.97.1 WASIp2 std SHA256', target['xz_hash'], flush=True)
if sys.argv[1:] == ['bundle']:
    env['LIGHTWORK_CARGO'] = str(toolchain/'bin/cargo.exe')
    raise SystemExit(subprocess.call([sys.executable, 'build.py'], cwd=repo/'apps/zed-extension', env=env))
raise SystemExit(subprocess.call([str(toolchain/'bin/cargo.exe')] + sys.argv[1:], cwd=repo/'apps/zed-extension', env=env))
