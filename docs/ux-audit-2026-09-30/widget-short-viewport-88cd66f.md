# Widget short-viewport follow-up

The embedded widget now allows vertical panel scrolling when a short visual
viewport cannot display its controls and receipt status together. At an emulated
200px height, the previous `overflow: hidden` clipped the unknown-status message
and prevented wheel scrolling. The repair changes only the panel's overflow to
`overflowX: hidden; overflowY: auto`; viewport sizing, safe-area calculations and
the six existing browser cases remain unchanged.

| Retained attempt | Source at execution | Result |
| --- | --- | --- |
| `widget-fit-be34965-focused-03` | Parent `af07f6d` plus the new test only | New case failed: wheel input left `scrollTop` at zero through the unchanged 10s expectation budget |
| `widget-fit-be34965-focused-04` | Same parent plus the identical test and overflow repair | Complete widget spec: seven passed, zero retries; 4.847s Playwright / 6.019s controller |

Both attempts ran on working trees with recorded uncommitted changes, **not
immutable HEAD runs**. Each retained receipt records identical before/after
source snapshots and `source_unchanged: true`. The repaired files were then
committed as `88cd66f29309ba64026077a835b1c755d0c19256`, with parent
`af07f6dc6e18b4feb145c12dd5ef7e25e3f90b64`. Working-file bytes still exactly matched
the runtime snapshots after commit. Git's CRLF normalization produces the same
committed blobs: widget `7f0d2c61c2c195df6a11e15dd21c730c06fc0f98` and spec
`0233060c582937728d6b9e23eb0d361b97cd5f30`.

The parent widget and original six-case spec blobs exactly match historical
full-qualification source `1719f2fc0e01216e8daea5f77228da9835409657`. The
[historical 108 distinct browser passes and 1711 JavaScript passes in 181 files](browser-final-1719f2f.md)
remain evidence for unchanged surfaces. This follow-up ran the complete updated
seven-case widget spec; it does not claim a new full 109-case browser run or a
new complete JavaScript run.

The new regression uses the real widget in a synthetic same-origin host. After
an uncertain submission it requires real wheel scrolling, the full status
rectangle within the visible panel, and a successful center-point hit test.
Keyboard navigation focuses the receipt and Close buttons; their bounds and
hit tests pass before actual clicks. The receipt check leaves exactly one start
POST and one receipt GET, preserves the draft, and closing restores launcher
focus. The old six cases retain their original assertions and budgets.

These retained captures were visually inspected: the baseline cuts off the
status after its first line; the repaired scrolled view shows the complete
unknown-status text. A separate keyboard-focused view shows Close in reach.
At this height, controls are revealed sequentially by scrolling.

- [Baseline clipped status](widget-short-viewport-88cd66f/baseline-200px-clipped-status.png)
- [Repaired complete unknown status](widget-short-viewport-88cd66f/widget-200px-unknown-status.png)
- [Repaired keyboard-focused Close](widget-short-viewport-88cd66f/widget-200px-close-reachable.png)

[The evidence JSON](widget-short-viewport-88cd66f.json) embeds both exact receipts,
all case IDs and outcomes, source/blob comparisons, commands, and hashes of the
retained logs, reports, diffs, source snapshots, baseline trace and screenshots.
Both logs contain two `NO_COLOR`/`FORCE_COLOR` warnings. After the baseline
receipt was written, printing its retained log to the controller's cp1252
console raised `UnicodeEncodeError`. This separate console failure was observed
in tool output; it is outside the receipt's managed-run `controller_error`
field. The helper subsequently enabled `backslashreplace` for console output.
The original failed Playwright assertion, receipt, report and artifacts were
preserved unchanged.

Installed Chrome 154.0.8037.58 and private Playwright 1.58.2 ran with one worker,
zero retries, unchanged 45s test / 10s expectation budgets and a 180s outer
controller bound. The retained process query found zero remaining processes
matching the owned run, helper or clone labels. No broader runtime was started.
The receipt transport, visual viewport and CSS content zoom are synthetic;
these results do not establish native keyboard, physical-device, touch,
nonzero safe-area inset or human screen-reader behavior.
