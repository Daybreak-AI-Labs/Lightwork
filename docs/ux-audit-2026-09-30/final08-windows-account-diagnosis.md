# Final08 Windows execution-account diagnosis

The first final08 ordinary attempt was incomplete: it stopped after 1255.54s
with ten recorded failures, exit 1, unchanged source and no JUnit file. The
operator stopped only owned pytest tree 30944 using native cleanup after the
default account received AccessDenied. Its original logs and failure records
remain intact; this attempt is not a complete-suite result.

The default attempt's source/log receipts are owned by
`The_Firm\CodexSandboxOffline`; prior final07 and the subsequent native probe
receipts are owned by `The_Firm\Cstep`. Read-only `CreateFileW` probes retained
the product's access 128, share 3, OPEN_EXISTING 3 and flags 0x02200000:

- CodexSandboxOffline received **WinError 5 / Access is denied** at
  `C:\Users\Cstep`; its two previously opened directory handles were closed.
- Cstep passed that parent but received WinError 5 at the current sandbox-owned
  basetemp `C:\Users\Cstep\AppData\Local\Temp\lw-iojyl1dj`; six handles were
  closed. This native probe of the old fixture was also a failure.

The Kubernetes fixture retained an empty extracted export and `export.complete`,
but no originals directory. Source inspection places its failure in the first
current-workspace snapshot reading `workspace/input.txt`, before backup or
publication. The original exception omitted both parent and Win32 error, so the
later probes demonstrate a reproducible account-context denial without claiming
to recover the original syscall error. No transient-sharing retry, ACL change
or parent-guard relaxation was made.

On fresh native-owned directories, the complete four affected test modules
passed **156 cases, zero failures or skips**, in 7.71s pytest / 8.66s controller:

| Complete module | Passed |
| --- | ---: |
| `test_client_reliability.py` | 51 |
| `test_customer_deployment_paths.py` | 32 |
| `test_q3_2026_batch4.py` | 33 |
| `test_q3_2026_batch9.py` | 40 |

Every one of the ten original failure IDs is present among these 156 passing
call reports. Six were Kubernetes/OCR parent-guard-path failures; four were Bash
permission denials or dependent missing-file assertions. The evidence JSON
records each exact mapping and reconciles the report IDs with the JUnit cases.

Both attempts bind unchanged source
`57a027adfdd8df955d33aaea88ec7089aafc6c60`. The implicated Kubernetes guard,
transfer code, helper tests, file-lock code and root/core fixtures also have
identical blobs in final07 `2a765aa3e93cc7775090f574f027bd855b33aef5`; the exact
Kubernetes case passed there in 0.104s. This evidence supports an execution-account
problem, not a demonstrated new product regression or a waiver of the failures.

The native probe was serial with the existing probe's `-x` and 90s cap. It does
not replace the four-worker full ordinary qualification. The separate
`native-02` full run was underway when this note was prepared; **no complete
full-suite pass is claimed here**.

[The JSON](final08-windows-account-diagnosis.json) contains exact receipt values,
all 156 native case IDs, the ten failure/pass mappings, artifact hashes, fixture
hashes, unchanged source blobs and account-probe observations. No application
tests were rerun by the documentation task, and no product or test files changed.
