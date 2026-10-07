# Public SpeechBrain fixture qualification plan

This is the historical preparation checkpoint. The subsequent
[real-model qualification](speechbrain-real-model-qualification.md) completed
installation and both posture runs successfully; its released helper hashes
and execution receipts supersede the preparation-only state below.

The remaining `test_voice_unlock_real_model` skip is locally feasible to attempt
using public fixtures. **Preparation only is complete: no model/sample assets or
runtime wheels have been downloaded, installed or executed. No pass is credited.**
Execution is waiting for the coordinated quiet browser/JavaScript window to end.
This does not require customer recordings, microphone access or provider keys.

The existing test enrolls one speaker from three 16-bit WAVs, expects a matching
fourth utterance above the existing 0.6 threshold, and a different speaker below
0.3. It requires a real 192-dimensional ECAPA embedding. The test, scores and
thresholds remain unchanged; synthetic embeddings would not qualify this case.

The [pinned model](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/tree/0f99f2d0ebe89ac095bcc5903c4dd8f72b367286)
is `speechbrain/spkrec-ecapa-voxceleb` at
`0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`; its model card specifies Apache-2.0.
The five public WAVs are from the
[official SpeechBrain test directory](https://github.com/speechbrain/speechbrain/tree/89ead74d163463d30c62329a09cfdb4c54f5abc1/tests/samples/ASR)
at `89ead74d163463d30c62329a09cfdb4c54f5abc1`, also the `v1.1.1` release tag.
The upstream repository's Apache-2.0 license is included in the asset plan.
Model checkpoints use published SHA-256 pins; small Git artifacts use exact
commit/blob pins, with an additional SHA-256 recorded after download.

| Planned download | Count | Exact bytes |
| --- | ---: | ---: |
| Model files, public WAVs and license | 12 | 89,425,387 |
| Official binary Python wheels | 19 | 171,943,356 |
| Total | 31 | 261,368,743 |

The private environment pins SpeechBrain 1.1.1, PyTorch 2.11.0 and TorchAudio
2.11.0, with a resolved 40-package dependency inventory (the remainder are
read-only shared audit packages). It does not modify the existing SDK environment
containing PyTorch 2.14.1. [Official TorchAudio 2.11 documentation](https://docs.pytorch.org/audio/2.11.0/installation.html)
explicitly supports PyTorch 2.11 and later through a stable ABI; selecting a
private 2.11 pair is an isolation choice, not a claim that 2.14.1 is incompatible.

The pinned 1,919-byte `hyperparams.yaml` was inspected before execution. Its seven
constructors instantiate six distinct built-in SpeechBrain classes: Fbank,
InputNormalization, ECAPA_TDNN, Classifier, CategoricalEncoder and Pretrainer.
There are no custom Python modules or apply/include tags. The current product
overrides `pretrained_path` to the verified local directory and loads on CPU.
Twelve upstream module sources, including all constructors and checkpoint hooks,
have recorded hashes; setup requires the official wheel to match those reviewed
release sources before installation.

The reviewed `from_hparams` path loads that YAML directly; the separate
`foreign_class` path that imports `custom.py` is not used. The model hooks call
`torch.load` without requesting unrestricted loading. The runner sets
`TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`, which [PyTorch documents as forcing weights-only loading](https://docs.pytorch.org/docs/2.11/notes/serialization.html#environment-variables),
and the guard also explicitly passes `weights_only=True`. It refuses unknown
checkpoint paths, a supplied pickle module or an unsafe loading override. A
checkpoint needing extra globals or unrestricted loading is a blocker to inspect,
not permission to relax this boundary. The label encoder uses `ast.literal_eval`.

The guard validates the exact YAML and local checkpoint override before invoking
the real loader. It installs the wrapper before importing SpeechBrain, verifies
the exact `interfaces.load_hyperpyyaml` alias points to that wrapper, records
successful guarded YAML loads, and refuses an otherwise successful session if
the real YAML and all three checkpoint loads did not occur. Python socket destinations are denied; Hugging Face runs in
offline mode with private caches and telemetry disabled. This is a Python-level
network guard, not a claim of operating-system/native containment. CPU intra/inter-op
threads are both one, the existing per-test limit is 180 seconds, and the owned
pytest process has a 300-second outer cap. Timeout cleanup targets only its exact
PID tree on timeout or controller exception and retains cleanup errors and
source/result receipts. The source receipt is written before launch and updated
with the owned pytest PID immediately after launch. No custom model
method or assertion is replaced.

The private helpers live at the task root, not in product code. Their hashes,
complete pinned asset/runtime manifests and source-review references are in the
[preparation record](speechbrain-local-qualification-plan.json). The setup stages
are explicit; `inspect` is the only stage executed so far:

```powershell
.\.venv-audit\Scripts\python.exe speechbrain_qualification_setup.py inspect
.\.venv-audit\Scripts\python.exe speechbrain_qualification_setup.py download
.\.venv-audit\Scripts\python.exe speechbrain_qualification_setup.py install --checkout <frozen-checkout>
.\.venv-audit\Scripts\python.exe run_speechbrain_qualification.py speaker-ordinary-01 ordinary --checkout <frozen-checkout>
.\.venv-audit\Scripts\python.exe run_speechbrain_qualification.py speaker-secure-01 secure --checkout <frozen-checkout>
```

Download uses pinned public HTTPS URLs, exact sizes and hashes, bounded per-file
and overall time, and no credentials. Installation consumes only verified local
wheels with `--no-index --no-deps --require-hashes`; no source distribution or
remote Python setup code is selected. The source checkout must be clean and
unchanged. Both posture runs must actually execute and pass the existing test
before the identity can move out of the pending skip boundary.
