# Supplementary local SDK qualification

Source: `500fc87363a492def69722ef19d2cc518d171286`; Windows AMD64, CPython 3.12.12, executed 2026-10-01. Product and test source were unchanged throughout these runs. This evidence supplements the integrated full suites.

| Complete test files | Ordinary | Both secure-default flags enabled |
| --- | ---: | ---: |
| Queue dispatcher, channel authorization, OTel observability, tool-span semantics, LangChain adapter, WebSocket tool and SSRF pinning, destructive-tool confirmation | 160 passed, 1 intentional skip | 160 passed, 1 intentional skip |
| Learned PRM, including actual CPU training and save/load/score | 21 passed | 21 passed |
| Total unique cases, executed together in each final nine-file run | 181 passed, 1 intentional skip | 181 passed, 1 intentional skip |

The [machine-readable receipt](optional-sdk-qualification-500fc873.json) contains every case result, exact source hashes, commands, secure flags, full private harness source, wheel URLs and hashes, raw artifact hashes, and the initial failed attempts. Exact case comparison with the complete `qualification-final-03` ordinary JUnit identifies **12 previously skipped optional-runtime cases now passing** in both postures:

- ARQ constructs its real worker with the safe named function.
- Slack and Matrix reject empty allowlists; Telegram checks group sender and private-chat authorization.
- The real OTel packages accept exporter headers and emit the expected in-memory GenAI span attributes.
- LangChain constructs its actual structured tool.
- The installed WebSocket package completes an actual loopback echo.
- The installed boto3 package reaches the S3 dry-run confirmation gate without making an AWS call.
- The installed CPU Torch runtime trains the three synthetic examples for five epochs, saves the head, reloads it and produces finite bounded reward scores.

The intentional LangChain skip covers the absence of its optional package; that negative path remains covered by the ordinary shared environment. The other 20 PRM cases exercise feature extraction, local fixtures, malformed metadata/state rejection and fallback behavior. Final ordinary pytest time was 31.79 seconds and secure time was 27.48 seconds, excluding interpreter teardown.

## Isolation and dependency provenance

All eight package paths came from a separate exact-source clone. A private interpreter reused existing shared dependencies read-only through a plain `.pth` path; 43 additional SDK wheels totaling 23,941,023 bytes and six Torch/runtime wheels totaling 133,422,642 bytes came from official PyPI, were SHA-256 verified against the resolution reports, and were installed only into that private interpreter using `--no-index --no-deps`. No shared environment, host installation, service or credential configuration changed.

Direct SDK versions were ARQ 0.28.0, Slack SDK 3.44.1, matrix-nio 0.26.0, python-telegram-bot 22.8, OTel SDK/exporter 1.45.0, langchain-core 1.6.6, websockets 17.1 and boto3 1.43.106. The PyPI Torch distribution is 2.14.1; its bundled version module identifies the runtime as `2.14.1+cpu`, with CUDA, HIP, ROCm and XPU fields unset. The receipt includes that file, all transitive versions and all 49 wheel hashes.

Each pytest invocation ran without xdist, with isolated native Windows HOME/USERPROFILE/TEMP and basetemp, inherited credentials removed, model downloads disabled and numerical-library threads capped at one. Secure runs explicitly set **both** `MAVERICK_TEST_SECURE_DEFAULT=1` and `MAVERICK_SECURE_DEFAULT=1`. A Python audit hook blocked external socket connections and external hostname resolution. These are local SDK, synthetic transport and authorization checks, not live Slack, Matrix, Telegram, Redis, OTel-collector, LangChain-provider or AWS integration qualification.

## Preserved initial failures and fixture DNS

The initial ordinary SDK run reported 156 passes, four failures and one intentional skip. All four failures came from the qualification hook rejecting pre-existing DNS-dependent WebSocket tests before their assertions: `test_check_url_accepts_ws`, `test_run_blocks_ssrf_without_dep`, `test_connects_to_pinned_ip_not_hostname`, and `test_rebind_to_private_ip_is_refused`. One lookup was a numeric metadata address, which needs no DNS; the other lookups used two fixture names.

The final private harness permits numeric address parsing and maps only `example.com` and `rebind.attacker.test`, only within the two WebSocket test files, to `93.184.216.34` via `AI_NUMERICHOST`. It preserves the tests' independently patched pinned resolver, private-IP rejection, TLS hostname and no-connect assertions. Real socket connections still require loopback. No product or test assertion changed. Both complete SDK runs then passed.

Two initial setup report-display attempts hit Windows cp1252 decoding errors after successful pip resolution. Explicit UTF-8 fixed that helper; both original resolution reports were retained.

## Preserved Torch download attempts and successful alternative

The official CPU index resolved `torch==2.14.1+cpu`; its Windows CPython 3.12 wheel request returned **HTTP 403** from `download-r2.pytorch.org`. One bounded fallback to the official `torch==2.8.0+cpu` wheel returned the same error. Exact URLs and outcomes are retained in the receipt. Neither CPU-index runtime was installed. The intermediate learned-PRM runs each passed 20 cases and skipped the actual training case, and those results remain recorded.

The official PyPI Windows wheel provided a working alternative at 124,113,906 bytes, below the 350 MiB bound, with five small Python dependencies and no NVIDIA/host SDK dependency cascade. It installed privately and is a CPU build. Both final complete nine-file runs include a passing `test_learned_prm.py::test_train_load_score_roundtrip`; no selected optional-runtime case remains skipped. No model assets, GPU drivers or additional host toolchains were downloaded or installed. This synthetic training check does not qualify external training data, larger models or GPU execution.
