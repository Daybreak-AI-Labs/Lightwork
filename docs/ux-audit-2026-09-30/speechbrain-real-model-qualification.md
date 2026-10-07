# Real SpeechBrain public-fixture qualification

The original real-model speaker test passed in both postures using actual CPU
inference, verified local checkpoints and five public upstream recordings.
**One distinct test passed twice; zero failures, skips or warnings.** No model
method, output, score threshold or test assertion was replaced.

| Posture | Result | Pytest time | Bounded helper time |
| --- | --- | ---: | ---: |
| Ordinary, both secure flags `0` | 1 passed | 2.52 s | 16.453 s |
| Secure, both secure flags `1` | 1 passed | 1.20 s | 4.453 s |

Both runs used clean frozen source
`7151b78c8d3579016c4875077dcb602e7a64ab50` and retained identical source afterward.
The voice implementation, original model test, profile-encryption/path helpers,
and both applicable conftest files have identical Git blobs at review source
`18efb85ca4bca86401c30ad20f625d1ca48102ed`. The
[machine-readable receipt](speechbrain-real-model-qualification.json) records
those six comparisons, exact commands, case identity, versions, guards and raw
artifact hashes. No inference process remains running; both subprocesses exited
zero without timeout or cleanup.

The unchanged test enrolls one speaker from three samples, checks the real
192-dimensional embedding, accepts a fourth utterance above 0.6 and rejects the
other speaker below 0.3. These public fixtures qualify that existing test; they
do not establish microphone/native-device behavior, customer-recording accuracy,
spoof resistance or a sole authentication factor.

## Verified loading and runtime

The [preparation record](speechbrain-local-qualification-plan.md) preserves the
earlier metadata/source review and its then-unexecuted state. Setup subsequently
downloaded exactly **31 artifacts / 261,368,743 bytes**: 89,425,387 model/sample/
license bytes plus 171,943,356 bytes of 19 official binary wheels. All sizes and
hashes were reverified. The SpeechBrain wheel matched all 12 reviewed upstream
module hashes. All five WAVs are mono, 16-bit PCM at 16 kHz. Static checkpoint
disassembly found only OrderedDict and Torch tensor/storage globals.

The model revision is `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`; the sample and
SpeechBrain 1.1.1 release revision is `89ead74d163463d30c62329a09cfdb4c54f5abc1`.
The private runtime uses PyTorch/TorchAudio 2.11.0 and SpeechBrain 1.1.1 with all
40 selected package versions verified during installation. Existing SDK PyTorch
2.14.1 and shared package versions remain unchanged. TorchAudio 2.11 supports
later PyTorch releases; the private pair was chosen for isolation.

Each run logged one successful load of the exact reviewed YAML hash
`6f78854fa04ba59e761437b76a2575d3aba5e5016de3e9b69f0c9a5077fb1a41`
and all three real checkpoints: `embedding_model.ckpt`,
`mean_var_norm_emb.ckpt`, and `classifier.ckpt`. The guard installed its wrapper
before importing SpeechBrain and verified the loader alias used by
`from_hparams` was that wrapper. A successful pytest exit without the guarded
YAML and all three checkpoint loads would have been changed to failure.

Loading retained `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1` and explicit
`weights_only=True`; no unrestricted pickle mode or extra safe globals were
enabled. The YAML and checkpoint paths were pinned to the local manifest. CPU
intra/inter-op thread settings were one. Python network destinations were denied,
Hugging Face was offline with private caches, and inherited credentials were
excluded. This is Python-level network enforcement, not OS/native containment.
The original 180-second per-test and 300-second outer caps were unchanged.

The released helpers additionally scoped Git trust to the exact checkout per
command, persisted the source/PID receipt at launch, and retained bounded owned
process-tree cleanup for timeout or controller exceptions. Their actual hashes
supersede the historical preparation hashes:

| Private helper | Executed SHA-256 |
| --- | --- |
| `speechbrain_qualification_setup.py` | `5a09a01c6b292f3efe56e21e840c5e90aed8e4f30a2dc55183328e7bc5c9cfc4` |
| `speechbrain_qualification_guard.py` | `3d8cd3fbcf11a6fc0ca2582b03a4965aa4be62e86eeced8deb472f713c842061` |
| `run_speechbrain_qualification.py` | `24834b71a4f65d0ab91f442afb1238670c4ad00b0dd3d2b27e7f41e5bc7fc194` |

## Exact skip-ledger change

The recovered identity is
`packages.maverick-core.tests.test_voice_unlock_real_model::test_real_model_accepts_the_speaker_and_rejects_another`.
It occurs once in both the historical 558-entry inventory and current 590-entry
full07 inventory. Its two posture executions therefore add exactly **one**
recovered identity.

| Distinct skipped entries | Historical 558 | Current full07 590 |
| --- | ---: | ---: |
| Executable identities with supplementary passes | 426 | 458 |
| Expanded collection placeholders | 5 | 5 |
| Remaining entries | 127 | 127 |

The [follow-up identity ledger](skip-reconciliation-speaker-followup.json)
verifies the disjoint sets and records the one-row transition. The earlier
[457 + five + 128 checkpoint](skip-reconciliation-final07.md) and its raw JSON
remain unchanged. Remaining entries are 123 executable environment/provider
gates, one intentionally inapplicable parameter, and three unexpanded collection
placeholders. PostgreSQL posture recovery and repeated Qdrant runs are not
counted again. The full07 ordinary result remains **40,072 passed, one failed,
590 skipped**; this focused success does not remove its transport failure or
replace fresh integrated qualification.
