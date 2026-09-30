# Catalog fixture and Windows qualification checkpoint

Existing catalog mutation fixtures now obtain the authenticated all-layer revision before submitting a save. Explicit stale revisions are preserved. Reset assertions distinguish an intentional product-default mask from inherited absence. Missing samples for immutable workbench images and approved account profiles are supplied; plain-language descriptions avoid developer terminology.

Windows qualification no longer fails every integration case because an autouse fixture attempts privileged symlink creation. Only the two actual link-replacement scenarios skip when Windows reports privilege 1314. POSIX permission-bit checks remain explicitly skipped on Windows. Program validation now rejects Windows documents selected as executables; the runtime chooser fixture uses a Windows executable suffix and preserves its document-refusal assertion.

Validation performed locally, no provider/network calls:

- Initial repaired five-module sweep: 1,381 passed, two Windows privilege-dependent scenarios skipped; eight failures identified (two stale HTTP mocks and six product-default mask expectations). Those causes were repaired.
- Follow-up selected six reset cases: six passed, 670 deselected.
- Follow-up Gist/Pushover mocked HTTP assertions passed before the later run encountered the missing test import; that test import is corrected.
- Structured module: 27 passed, two POSIX scenarios skipped; the Windows document-as-program failure was fixed and its targeted regression passed.
- Earlier access sweep: 400 passed; all eight reported causes were repaired and the targeted rerun passed seven with two platform qualifications skipped.
- Earlier remaining-category sweep: 335 passed before the old integration fixture errors; no complete all-catalog pass is claimed. Root owns the integrated full-suite rerun.

Ruff and whitespace checks apply to the changed fixture/product files. No skips conceal a failed supported platform behavior.
