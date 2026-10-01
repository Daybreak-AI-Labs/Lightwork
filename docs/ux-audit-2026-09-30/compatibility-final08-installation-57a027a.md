# Python 3.10/3.11 final08 installation evidence

The private Python **3.10.19** and **3.11.14** environments now contain the exact eight wheels built from **`57a027adfdd8df955d33aaea88ec7089aafc6c60`**. This checkpoint verifies installation and provenance; it adds **no collection or runtime test pass claim**. Full ordinary and serial-heavy compatibility phases remain scheduled after the parent-owned Python 3.12 phases.

| Check | Python 3.10 | Python 3.11 |
| --- | ---: | ---: |
| Fresh install attempt | `final08-install-03` | `final08-install-01` |
| Installed distributions | 100 | 96 |
| Unchanged third-party pins | 92 | 88 |
| First-party wheels force-reinstalled | 8 | 8 |
| Installed entries compared to exact wheel bytes | 4,856 | 4,856 |
| Installed RECORD entries hash/size verified | 4,887 | 4,887 |
| Enumerated rewritten RECORD entries without hashes | 8 | 8 |
| Pip check, extras, constraints, private module origins | Passed | Passed |
| Native-account read and origin readiness | Passed | Passed |

Installation used the exact local wheel paths with `--force-reinstall --no-index --no-deps --no-compile`. The eight dependency metadata sets and constraints matched the retained plans, so no third-party package, credential, host configuration or network dependency changed. Both installs verified private distribution origins, every ordinary wheel payload, installed RECORD hashes, local-wheel provenance and declared generated entry-point scripts. Non-importing `find_spec` checks resolved all eight packages inside the respective environment. Native-account checks then read those module origins, the install/payload receipts, final Node receipt, dependency manifests and runner plugins successfully.

The source checkout remained clean at the exact frozen SHA. Its archive verifier independently matched **4,827 source payloads**; that count excludes wheel-generated metadata included in the 4,856 installed-entry count above. The binding now requires each interpreter's successful install receipt by SHA before a full compatibility phase starts. Test source and product wheel source are both final08. The Node binding is the retained successful `final-node-qualification-final-08-02.json` receipt; its external-temp ancestor-shadowing limit remains explicit.

Two earlier 3.10 attempts stopped **before mutation**. The default sandbox could not reopen an old final06 wheel; native execution could not read the sandbox-owned new final08 wheel. Both failures remain preserved. The corrected default-sandbox install compares new wheel metadata against the retained, hashed old verification receipts instead of reopening the protected old archives. It still freshly hashes every new wheel and installed payload. No ACL or permissions were changed. All 109 earlier provenance artifacts were retained before rebinding.

Generated Windows launcher bytes were checked against installed RECORD and their declared entry points, not compared to nonexistent wheel launcher entries. Rewritten RECORD files are enumerated without a self-hash. This is not a proof that no unlisted stale file exists. Prior final06 collection and critical-group passes remain tied to their original recorded sources; they are not relabeled as final08 runs.

The [machine-readable evidence](compatibility-final08-installation-57a027a.json) records all eight wheel hashes, commands, bounded durations, receipt hashes, module origins, limitations and retained failures. Its SHA-256 is `09bfad1331e7c88153930223afa560a83f6b67dff09efdc7716488f4d950b0e1`. Raw receipts remain in the task-local `audit/compat-interpreters` directories, separately from this repository summary.
