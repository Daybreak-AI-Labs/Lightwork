from pathlib import Path
import shutil
repo = Path(__file__).resolve().parent
root = repo.parent/'Lightwork-staging'
ide = repo/'apps/jetbrains-plugin'
shutil.copytree(repo.parent/'audit/jetbrains-build/gradle', ide/'gradle', dirs_exist_ok=True)
for name in ('gradlew', 'gradlew.bat'):
    shutil.copy2(repo.parent/'audit/jetbrains-build'/name, ide/name)
text = (root/'apps/jetbrains-plugin/README.md').read_text(encoding='utf-8')
start = text.index('**Build requirements')
end = text.index('Open **Settings', start)
text = text[:start] + '''**Preview build:** the repository now includes the pinned Gradle wrapper,
IntelliJ SDK project and local packaging command. See [BUILDING.md](BUILDING.md)
for `python build.py`, the installable unsigned plugin ZIP and the installed-IDE
qualification gate. No manual source/SDK assembly is required. A successful build
does not qualify PasswordSafe, an installed IDE or assistive technology.

''' + text[end:]
text = text.replace('(live-run scaffold)', '(preview)').replace('\x1a', '→')
(ide/'README.md').write_bytes(text.encode('utf-8'))
zed = repo/'apps/zed-extension'
text = (zed/'README.md').read_text(encoding='utf-8')
start = text.index('## Building / installing')
text = text[:start] + '''## Building and qualification

**Preview:** the pinned Rust1.97.1 / `wasm32-wasip2` / API0.7.0 build and source
bundle are supplied in [BUILDING.md](BUILDING.md). Run `python build.py` to compile
the locked dependencies and produce an unsigned archive with the WASM component,
source and checksum manifest. This source now compiles locally; no installed Zed
host, authenticated MCP session or accessibility qualification is claimed.

An end-user marketplace release still requires the release owner's selected
host matrix, distribution approval and installed-host evidence. For development,
Zed's **Install Dev Extension** loads the source directory and builds it. The
extension never installs the Lightwork CLI; `maverick` must already be on PATH.
'''
(zed/'README.md').write_bytes(text.encode('utf-8'))
p=zed/'extension.toml'
p.write_bytes(p.read_bytes().replace(b'wasm32-wasip1',b'wasm32-wasip2'))
