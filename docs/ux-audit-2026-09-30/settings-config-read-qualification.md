# Settings configuration-read qualification

Source: isolated `slice-settings-timeout` checkout of
`b6d19b46e831ce373dd97d8ec9e9c5186a366855`, Windows, Python 3.12.12.
Local checks only; no provider calls, production changes, or dependency changes.

## Observed failure and bounded diagnosis

The immutable `12ff6b8` ordinary qualification stopped with 5,669 passes,
10 skips and two lost workers. It did not complete the ordinary suite.
The reported tests were:

- `test_settings_catalog_governance.py::test_every_row_saves_reads_back_and_resets`
- `test_settings_catalog_integrations.py::test_secrets_are_sealed_never_shown_and_audited_as_set`

The first worker's 120-second diagnostic stack dump was followed by a Windows
access violation with incomplete/corrupted frames. This is not evidence of a
normal 180-second pytest timeout. The second worker's diagnostic stack showed
Settings rendering inside `config._unseal_config_tree`, loading a credential
key and checking its Windows file ACL; its later worker loss did not include
an access-violation message. The native exit causes remain unresolved.

The unchanged governance test passed alone before the patch, including its
120-second stack dump, in 196.03 seconds overall. The measured all-groups
rendering step consumed 135.656 seconds and the 214-setting batch save consumed
20.985 seconds. Its live stack showed active repeated key-file/ACL reads,
not a deadlock. Each encrypted setting previously resolved the same key again
within each configuration read, and a full Settings page reads configuration
through several independent authority and presentation consumers.

## Product change

Configuration-tree decryption now uses the existing authenticated
`unseal_many_from_str` primitive. It resolves each distinct key once within
that single tree read. The existing deployment-key scope and re-entry guard
remain in force, each ciphertext is authenticated, and the resulting nested
containers remain detached from the source. No key or plaintext cache survives
the read. A later read still checks key availability and permissions and sees
rotated keys. A corrupt member still refuses the entire managed overlay.

Both original exhaustive Settings tests remain unchanged: all 214 coherent
governance save/read/reset checks, per-setting audit assertions, sealed-storage
checks, every Settings group and all integration credential nondisclosure
assertions remain exercised. No timeout was increased or disabled.

## Local evidence

- Configuration, encryption and four new tree-decryption regressions:
  **88 passed, 2 skipped** in 4.21 seconds. The two skips require unavailable
  Windows symlink privileges; they are not encryption/rotation test skips.
- Both original Settings cases after the patch: **2 passed** in 69.70 seconds
  overall; call durations **32.18 seconds** (governance) and **25.02 seconds**
  (integrations). Governance all-groups rendering took **28.938 seconds** and
  its unchanged batch save took **0.484 seconds**. These are local diagnostic
  observations, not a controlled cross-machine performance guarantee.
- Both unchanged cases in two xdist workers with `--dist=loadfile` and
  `--max-worker-restart=0`: **2 passed** in 57.33 seconds, call durations
  **33.29 / 28.57 seconds**. No worker loss occurred.
- The four new regressions and both unchanged cases with
  `MAVERICK_TEST_SECURE_DEFAULT=1`, also in two workers: **6 passed** in
  87.93 seconds, Settings call durations **32.90 / 28.18 seconds**. No worker
  loss occurred. Both worker runs retained the 120-second diagnostic dump and
  180-second per-case thread timeout settings from the failed full run.
- Ruff and Git whitespace checks passed for the changed files.
- The new tests cover fresh per-read key resolution, detached dict/list/tuple
  values, mixed legacy/rotated keys, revoked file access, corrupt-overlay
  refusal, and subsequent recovery. Existing cold-boot/re-entry regressions
  remain in the passing configuration test file.

The local diagnostic logs are `audit/settings-timeout-probe.log`,
`audit/settings-batched-probe.log`, `audit/config-batch-regressions.log`,
`audit/settings-batch-workers.log` and `audit/settings-batch-secure.log` in
the task workspace. The original failure evidence remains in
`audit/qualification-final-01-ordinary.log` and its failures JSONL.
Full ordinary qualification must be rerun from an immutable integrated commit;
the performance fix alone does not establish that the native crash is fixed.
