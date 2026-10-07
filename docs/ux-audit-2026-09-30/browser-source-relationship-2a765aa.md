# Browser proof and integrated source relationship

The **90 passing browser journeys** and **1,695 passing JavaScript cases** were
executed at `11c30996c98a0ad0c9583005223019ad0d75b8b4`, as recorded in the
[quiet qualification report](browser-quiet-qualification-2026-10-01.md).
Read-only Git comparison against integrated source
`2a765aa3e93cc7775090f574f027bd855b33aef5` confirms:

- The entire `packages/maverick-dashboard/` tree is identical: **1,757 files**,
  including application Python, templates, assets, fixtures, and tests.
- All **796 tracked frontend files** selected by the explicit extension set in
  the manifest have identical Git mode/type/blob identities across the repository.
- All **41 browser fixture files** and **179 JavaScript test files** match their
  original recorded hashes and identical Git blobs at both commits.

The [source manifest](browser-source-relationship-2a765aa.json) lists all 220
fixture proofs, the unchanged dashboard tree object, the extension scope, raw
receipt hashes, and the complete 18-path diff. Browser fixture hashes were
originally taken from Windows checkout bytes; JavaScript hashes were taken from
Git blobs. Both forms were verified independently. Retained checkout files with
CRLF or mixed CRLF/LF endings normalize to the same Git source content.

Later changes comprise documentation, three Python test files, and two product
modules: `qdrant_store.py` and the new `qdrant_text.py`. The Qdrant changes have
[separate native and offline ONNX qualification](qdrant-current-client-qualification-e22ffbc.md).
These differences are explicitly retained; whole-repository runtime equivalence
is not asserted.

The original browser/JavaScript results therefore remain applicable evidence for
the unchanged dashboard and frontend surfaces at `2a765aa`. This comparison does
**not** relabel those executions as a browser run at the newer SHA, claim browser
coverage of the changed Qdrant paths, or replace integrated Python qualification.
No browser or JavaScript suite was repeated during this documentation pass.
