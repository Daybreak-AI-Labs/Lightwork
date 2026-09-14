# Spreadsheet preservation, calculation and scanned PDFs

The spreadsheet tool exposes formula cells as objects with `formula`,
`cached_value`, `cell`, and `value_status` (`cached` or `uncalculated`). An empty
formula cache is not reported as an empty input cell or zero. A cached result
can be stale; use the explicit `recalculate` operation when current values are
required. Editing with openpyxl does not calculate formulas.

## Setup

Install the core `spreadsheet` and `pdf` extras. The application Docker image
includes them. The advanced installer has opt-ins for macro cell edits, native
recalculation and scanned-PDF OCR, plus the worker image setting.
Full `release-runtime` artifacts include the parsers and the standalone worker
source. `maverick release-runtime-check` checks both in the actual wheel or
frozen executable; it does not imply that an external engine is configured.

Build the dedicated worker image, then publish it to a registry reachable by
the selected executor if necessary:

```sh
docker build -f deploy/docker/Dockerfile.documents -t lightwork-documents:local .
```

The image contains LibreOffice Calc, Python UNO, Poppler, Tesseract and the English
language pack. For a released deployment, build from an approved base digest,
record the native package versions, scan the image and pin its resulting digest.
Install additional `tesseract-ocr-*` language packs in the worker image before
selecting them in configuration.

```toml
[documents]
macro_cell_edits = true
native_recalculation = true
pdf_ocr = true
engine_image = "lightwork-documents:local" # use registry/image@sha256:… in production
ocr_language = "eng"
ocr_max_pages = 20
timeout = 120
```

These capabilities are opt-in. Native processing requires an existing Docker,
Podman or Kubernetes backend. Each job receives its own private workspace and
has network access disabled; it receives no other client files or application
configuration. Missing engines, image access, language packs or sandbox setup
produce an error and preserve the original document. The tools do not install
system packages or silently execute native parsers on the application host.

## Excel workbooks

```json
{"op":"set_cell","path":"orders.xlsm","sheet":"Orders","cell":"B2","value":25}
{"op":"recalculate","path":"orders.xlsm"}
{"op":"read","path":"orders.xlsm","sheet":"Summary"}
```

Macro-preserving cell edits update OOXML parts directly. Untouched ZIP parts,
including VBA binaries, remain byte-identical. Model-supplied formula-like
strings remain neutralized as text. Editing clears formula caches across the
workbook; recalculation updates them explicitly. Whole-workbook `write` to XLSM
is refused because it would replace the macro-bearing document. Cell edits
require an existing sheet. Digitally signed packages, shared/array formula
members and non-anchor merged cells require a reviewed copy/native editor.

Recalculation sends a VBA-free XLSX copy to LibreOffice, opens it with
`MacroExecMode.NEVER_EXECUTE` and `UpdateDocMode.NO_UPDATE`, and calls
`calculateAll()`. It copies only calculated cell caches back into the original
package; original formulas, styles, VBA and other workbook parts are retained.
Formula errors or concurrent changes to the original stop publication.
The worker uses the documented [macro execution constants](https://api.libreoffice.org/docs/idl/ref/namespacecom_1_1sun_1_1star_1_1document_1_1MacroExecMode.html)
and [calculation interface](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1sheet_1_1XCalculatable.html).

This is Calc calculation, not a claim of complete Excel compatibility. VBA UDFs,
external workbook/data connections, XLM macros, array/data-table formulas and
external-service formulas require native review. No macros are executed. Input
is limited to 64 MiB, expanded parts to 128 MiB, and recalculation to 100,000
formula cells. Qualify the customer's actual workbook before relying on it for
financial or operational decisions.

## Scanned and mixed PDFs

```json
{"source":"invoice.pdf","pages":"1-3","ocr":true}
```

`read_pdf` uses embedded text first and automatically attempts enabled OCR when
the selected pages contain no text. For a mixture of scanned and text pages,
`ocr=true` processes every selected page so image-only pages are included.
Pages are rendered individually with Poppler and transcribed by Tesseract.
Output labels each page `[OCR]`; tables are returned as plain text, without
claiming reconstruction of their structure. Names, numbers and totals still
need normal review when OCR informs a business action.

Select a smaller range when the per-call page or time limit is exceeded. OCR
input is limited to 64 MiB and each rendered page's longest side to 2,200 pixels.
The CI `document-engines` job executes real arithmetic/cross-sheet formulas,
formula-error preservation, repeated recalculation and a generated scanned
invoice. Its synthetic VBA fixture checks byte preservation, not compatibility
with a customer's complete macro program.
