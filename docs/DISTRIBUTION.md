# Distribution & Deployment — how Lightwork ships

> How a user gets Lightwork onto a Mac, Windows PC, Linux box, or a cloud
> (Azure / AWS / GCP). The integrated platform ships as compiled artifacts or a
> container; the three reduced standalone SKUs deliberately ship as licensed,
> versioned source archives rather than pretending to be PyPI distributions.

## TL;DR — what the release pipeline produces

One tagged release (`release.yml` + `desktop.yml`) emits **all** of these, for
**macOS (Apple Silicon), Windows, and Linux**, plus a cloud image:

| Channel | Artifact | Who it's for | Source exposed? |
|---|---|---|---|
| **Single-file binary** | `maverick-macos-arm64`, `maverick-windows-x86_64.exe`, `maverick-linux-x86_64` | technical users / CI / servers | **No** — PyInstaller-compiled |
| **Native installer** | `Lightwork_<ver>.dmg` / `.msi` / `.exe` / `.AppImage` / `.deb` | non-technical end users (double-click) | **No** |
| **Container image** | `ghcr.io/day-ai-labs/maverick:<ver>` | **Azure / AWS / GCP**, k8s, on-prem Docker | **No** |
| **Reduced standalone SKU** | `lightwork-<sku>-<ver>.zip` | local evaluation of GRC / platform hunt / environment hunt | **Yes** — licensed source archive |
| Supply-chain | Sigstore `.sig`/`.pem` per release artifact + SHA-256 manifest + CycloneDX SBOMs | security / procurement review | n/a |

The standalone SKUs are **not PyPI packages**. Their explicit release manifests
drive deterministic ZIP builds and each GitHub Release carries SHA-256 checksums,
a CycloneDX SBOM, and Sigstore signature/certificate files. `publish.yml`
separately contains an opt-in, allowlisted Trusted Publishing path for the real
workspace Python distributions; standalone source archives never enter it.

**What "compiled" means (be honest with buyers):** PyInstaller bundles Python
**bytecode**, not source. That stops casual copying and means no `pip install`
gives anyone the packages — but a determined reverse-engineer can decompile
bytecode. If stronger IP protection is required, compile with **Nuitka**
(Python → C → native) or keep crown-jewel logic server-side/license-gated. See
§Hardening.

---

## macOS (incl. Mac Mini)

**Option A — download the installer (once a release is cut):**
1. Download `Lightwork_<ver>_aarch64.dmg` from the GitHub Release.
2. Open it, drag **Lightwork** to Applications.
3. First launch is **unsigned** → Gatekeeper says "unknown developer."
   Right-click the app → **Open** → **Open** (one time only). Signing removes
   this (see §Signing).

**Option B — build it locally on the Mac Mini** (no release needed; what we run
tomorrow): `scripts/build-macos-app.sh` — installs the packages non-editable
from this source tree, PyInstaller-compiles them into a `maverick` binary, and
wraps it into a double-click `Lightwork.app` that opens the local dashboard.
Compiled, no PyPI, no source shipped. See the script header for prerequisites
(Homebrew + `python@3.12`).

**Footprint:** ~200–300 MB app, no GPU, runs on any Apple-Silicon Mac Mini. The
model is called over an API (Anthropic / Azure OpenAI / Bedrock) **or** run
locally via Ollama for a fully air-gapped box (`maverick init` → choose Ollama).

---

## Windows & Linux

Same pipeline, same guarantees:
- **Windows:** `Lightwork_<ver>_x64.msi` or the NSIS `.exe` (double-click), or
  the standalone `maverick-windows-x86_64.exe`. Unsigned → SmartScreen "More
  info → Run anyway" until code-signed.
- **Linux:** `.AppImage` (chmod +x, run) or `.deb` (`sudo dpkg -i`), or the
  standalone `maverick-linux-x86_64` binary.

To build locally on either OS, the recipe is identical to the Mac script:
install the packages non-editable, then `pyinstaller --clean --noconfirm
--distpath ../dist build/maverick.spec` (run from `build/`). PyInstaller must
run **on the target OS** — a Windows exe builds on Windows, a Linux binary on
Linux. CI's `release.yml` does all three on hosted runners.

---

## Cloud: Azure / AWS / GCP (the "drop it in" path)

The container image is the universal cloud artifact. Pull it and run — it needs
only a mounted config dir and a model key (or a private model endpoint your
cloud already offers, e.g. Azure OpenAI / Bedrock).

```bash
# the image (built + pushed by release.yml)
docker pull ghcr.io/day-ai-labs/maverick:latest
```

**AWS** — ECS/Fargate task, or plain EC2:
```bash
docker run -d -p 8000:8000 \
  -e MAVERICK_DASHBOARD_TOKEN=... \
  -e ANTHROPIC_API_KEY=...            # or point at Bedrock via the provider config
  -v /srv/maverick:/home/maverick/.maverick \
  ghcr.io/day-ai-labs/maverick:latest dashboard --host 0.0.0.0
# Fargate: same image in a task definition; 0.5 vCPU / 1–2 GB is plenty.
```

**Azure** — Container Apps (serverless) or AKS:
```bash
az containerapp create -n lightwork -g <rg> --environment <env> \
  --image ghcr.io/day-ai-labs/maverick:latest \
  --target-port 8000 --ingress internal \
  --secrets model-key=... --env-vars ANTHROPIC_API_KEY=secretref:model-key \
  --cpu 0.5 --memory 1Gi
# Point at Azure OpenAI (already in-tenant) instead of Anthropic — zero new
# model-vendor review for a bank that's already approved Azure OpenAI.
```

**GCP** — Cloud Run:
```bash
gcloud run deploy lightwork \
  --image ghcr.io/day-ai-labs/maverick:latest \
  --port 8000 --cpu 1 --memory 2Gi --no-allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=...   # or Vertex/Gemini via provider config
```

**k8s / on-prem:** the same image in a single-replica control-plane Deployment;
mount a PVC at `/home/maverick/.maverick` for the world DB + audit chain.
SQLite is the default; Postgres adds database HA/isolation and supports separate
remote-worker scale, but not multiple dashboard/serve replicas yet.

Governance + data stay in the customer's tenant; only the model call goes out
(and even that can be an in-tenant endpoint). That's the whole self-hosted
pitch, and the container is what makes it a 5-minute deploy on any cloud.

---

## Cut a release (populate the download page)

Downloads only exist once a release is cut. The pipeline is already wired; it
just needs to run on a tag:

```bash
# preferred: manual dispatch (avoids the GITHUB_TOKEN tag-trigger gap that left
# v0.1.5 with no installers), from a green main:
gh workflow run release.yml -f tag=v0.1.7
```

`release.yml` then: builds the 3-OS binaries → builds + pushes the GHCR image →
builds the three versioned standalone source archives → generates checksums and
SBOMs → Sigstore-signs the artifacts → creates the GitHub Release → dispatches
`desktop.yml`, which builds the installers and attaches them to that same Release.

**Before cutting a real (public) release:** merge the intended code to `main`,
decide signing (below), and bump the version. A release is public and
outward-facing — cut it deliberately.

---

## Signing / notarization (removes the scary prompts)

Current builds are **unsigned**. For internal/demo use that's fine (right-click
→ Open on macOS; "Run anyway" on Windows). For design partners:

- **macOS:** Apple Developer Program membership ($99/yr) → a Developer ID cert →
  notarization. Wire the cert + notary creds as CI secrets; `tauri build` and the
  binary both get signed + stapled.
- **Windows:** an Authenticode code-signing cert (OV ~$200–400/yr, or EV) → sign
  the `.msi`/`.exe`.
- Already in place regardless: **Sigstore** keyless signatures (`.sig`/`.pem`)
  on the raw binaries — verifiable with `cosign verify-blob`
  (`deploy/verify-release.sh`).

## Hardening (stronger IP protection, if needed)

- **Nuitka** compile (`nuitka --standalone --onefile`) → native machine code,
  far harder to decompile than PyInstaller bytecode.
- Keep the highest-value logic (e.g. proprietary pack generation / evaluator)
  **server-side** behind a licensed API, so the shipped binary orchestrates but
  doesn't contain it.
- License-gate the binary (offline license key checked at boot) to control who
  can run it, independent of who can copy it.
