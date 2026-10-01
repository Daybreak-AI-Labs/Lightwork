# Attachment download posture qualification

The full secure run at `500fc873` exposed a test that required all downloads to
stream while inheriting encrypted storage. The native endpoint deliberately
authenticates a size-capped GCM blob in full under its shared decrypt budget
before releasing plaintext. Blocking its read correctly produced HTTP 409.
The same test passes ordinarily because unsealed files use `FileResponse`.

Test-only commit `6d656645153462af2b83394e359256de70dceab8` makes the streaming
case explicitly select and verify unsealed storage, then forbids whole-file
reads of that attachment. It preserves body and safe download-header checks.
The separate encrypted roundtrip still verifies sealed bytes on disk. A new
test flips a byte in the actual GCM tag and verifies HTTP 409 with no plaintext
release, keeping the envelope header intact.

Both complete files, `test_goal_attachments_download.py` and
`test_decrypt_budget.py`, pass **nine tests ordinary and nine secure**, with both
secure flags set for the latter. The runs include decrypt-budget contention,
timeout and oversized-file reservation behavior. Pytest times were 18.97 and
17.61 seconds. Ruff, native Python 3.10 parsing and whitespace checks pass.

The [receipt](attachment-posture-qualification.json) records the exact source,
file/log/JUnit hashes and process results. Each run retained two warnings about
the native account being unable to write an existing pytest cache directory;
no test failed and no file permissions were changed. No production encryption,
authentication, endpoint or memory-budget behavior changed.
