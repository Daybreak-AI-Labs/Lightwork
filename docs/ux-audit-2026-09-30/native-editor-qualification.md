# Native editor qualification — 2026-10-01

LW-191's **two existing Emacs ERT tests and one existing Lua mock fixture passed in native editor hosts**. This closes the previously unrun native-runtime contract check. It does not qualify an interactive installed-editor journey or real CLI effects.

The tested checkout was `7db4b18e0d8113baa68b48c0ba811ef579022b9e`. All four product/test input files are byte-identical at integrated checkpoint `9ff7557a378149a5ed0b9be101fe4301f13e4fc4`; exact hashes, commands, log/receipt hashes, and cleanup records are in [the machine-readable receipt](native-editor-qualification.json). No product or test assertion changed.

| Native host | Existing assertions | Result |
| --- | --- | --- |
| GNU Emacs 30.2, Windows x64 portable | `maverick-control-deadline-fences-late-result`; `maverick-halt-requires-exact-acknowledged-scope` | 2 passed, 0 unexpected; 0.438 s controller time |
| Neovim 0.12.5, Windows x64 portable / LuaJIT | Exact setup and assertion blocks mechanically extracted from `apps/client-contract-tests/nvim-command.test.cjs`, wrapping the unchanged actual Lua source | 1 fixture passed; 0.154 s controller time |

The Lua fixture covers missing executable, nonzero exit, signal exit, malformed JSON, wrong acknowledged state, halt, unhalt, timeout and cancellation. It also fences cancelled/late callbacks and a superseded control. These are scenarios within one existing fixture, not nine new test identities. Process/timer APIs remain mocked in both editors. A second run merely changing the server secure-default environment variable would add no meaningful editor coverage, so none was counted.

## Preserved failed attempt and recovery

`audit/editor-native-ordinary-01` was the first restricted-sandbox attempt. Emacs printed its version and a native-address backtrace before any ERT test report, then reached the unchanged 120-second cap. Sandbox cleanup reported access denied. Native cleanup verified the exact executable for recorded PID 45032 and stopped only its tree. A subsequent native process query confirmed all recorded IDs absent; the first failure receipt was retained unchanged.

Native startup and ERT-import-only probes then passed in 0.136 s and 0.837 s. The **exact original guarded ERT command** passed under authorized native execution. The additional `fset` guard was not removed, JIT was not disabled, and assertions/deadlines were not relaxed. Diagnostic controllers set a process-local Windows error mode to avoid a hidden crash-report dialog; this did not change host policy. This establishes a successful native-host execution path, not the precise internal cause of the sandbox crash. Neovim's independent fixture also passed natively.

`audit/editor-native-runtime-release.json` confirms no process at either owned editor runtime path, recorded PID, or recorded direct-parent ID remained. No broad process kill, host installation, registry/ACL change, or credential change occurred.

## Reproduction and provenance

The private qualification helpers, their exact hashes and every executed command are recorded in the JSON receipt. Preparation used `python editor_qualification_setup.py download`, followed by its separate `extract` action. Only the two official pinned archives were downloaded:

| Official artifact | Bytes | SHA256 |
| --- | ---: | --- |
| [GNU Emacs 30.2 portable ZIP](https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2.zip) | 166,905,014 | `414d3a1a21147af257ebd98bdd15976fdcb5ed0563f6de89f76d4a4b5dad9c72` |
| [Neovim 0.12.5 Windows ZIP](https://github.com/neovim/neovim/releases/download/v0.12.5/nvim-win64.zip) | 12,623,588 | `de8625ba8cf65ebf40eb80a388ba1ec8e9c15b30218821e2c639119b05920de1` |

The GNU hash came from its [published checksum file](https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2-sha256sum.txt); the Neovim pin came from [official release metadata](https://api.github.com/repos/neovim/neovim/releases/tags/v0.12.5), tag commit `5885a30e1e1225349079e7a1c4a3848aa8e43e42`. GPL, Apache/Vim and bundled notices remain in the task-local archives. Detached signatures were not verified. No binaries or third-party runtime files are committed to this repository.

The successful existing ERT command uses `emacs.exe -Q --batch --eval <recorded process/network guard> -l apps/emacs/tests/maverick-control-tests.el -f ert-run-tests-batch-and-exit`. The guard rejects real CLI/network entry points; the existing tests replace their process/timer dependencies with mocks. Native Neovim runs the recorded mechanical fixture with `--headless -u NONE -U NONE -i NONE --noplugin -n -l <fixture>`. Both use scrubbed environments, private homes/caches, exact source checks and owned-process supervision. The native probes ran through `probe_editor_qualification.py` with fresh `editor-startup-native-01`, `editor-ert-import-native-01`, `editor-emacs-original-native-01` and `editor-nvim-native-01` labels. The product cohort caps remained 120 seconds per editor; only startup/import probes used 30-second caps.

Interactive editing/focus, genuine external-process stalls and cancellation, editor teardown, extension installation, and other supported editor/OS versions still need their own installed-host checks. These mock results do not replace LW-184's broader client-distribution qualification or any final integrated Python/browser gate.
