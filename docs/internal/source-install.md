# Engineering source installation

This procedure is for maintainers who already have approved access to the
private repository. A Git clone transfers the whole tracked repository,
including internal research, audit evidence and patent documents. Do not grant
repository access or send a repository archive merely to install Lightwork for
a customer. Use an actual signed [release asset](../../README.md#install) for
customer delivery when one exists for the target platform.

Use Python 3.10–3.12 and a reviewed full commit SHA. The manifest installer
resolves all eight local packages together against the reviewed constraints;
the root is not a pip project.

On macOS/Linux:

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork dashboard
```

On Windows PowerShell:

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip==26.2.1
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe dashboard
```

The explicit Windows paths avoid an activation-policy change. The source
bootstrap scripts in `deploy/desktop` are also engineering paths: they clone
the pinned private repository and require GitHub authorization. Neither route
provisions external provider accounts or proves an installed customer release.
Check the installed cohort with the same environment's Python:

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```
