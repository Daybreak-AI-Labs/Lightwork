# Lightwork Accessibility Conformance Report — DRAFT

**Status: draft for internal review. Not for publication.** This draft follows
the layout of an ITI VPAT® 2.5 (WCAG edition) Accessibility Conformance Report
for WCAG 2.2 Level A and AA. It records only what was measured. Every
criterion without measurement is marked **Not evaluated**. A published ACR may
use "Not evaluated" only for Level AAA, so every row marked that way must be
evaluated before this report is released.

| | |
|---|---|
| Product | Lightwork dashboard (web application, `maverick-dashboard`) |
| Build | Branch `ux-u`, based on integration branch `ux-int` at `372255d9` (PR #2529 head plus wave-1 UX work and stream I's accessibility fixes); automated scan at commit `1be8f45a` |
| Report date | 5 October 2026 |
| Standard | WCAG 2.2 (W3C Recommendation, 12 December 2024), Levels A and AA |
| Evaluation methods | Automated: axe-core 4.13.0 in headless Chromium 145 (Playwright 1.58.2). Scripted: keyboard-only runs with real key presses, reflow and zoom, target size, reduced motion, sign-in paste and autofill. Template tests in the repository. **No assistive-technology testing with real users was done. No screen reader (NVDA, JAWS, VoiceOver, TalkBack), speech input, switch access or Windows forced-colours testing was done.** |
| Scope | The 18 most-used pages (`/chat`, `/start`, `/goals`, `/approvals`, `/deliverables`, `/me`, `/settings`, `/settings/all`, `/providers`, `/spending-limits`, `/users`, `/tenants`, `/audit`, `/privacy`, `/security`, `/agents`, `/workflows`, `/overview`), signed in as an administrator, in the light, dark and high-contrast themes, plus the five top flows and the sign-in forms named below. Other pages are outside this draft's evidence. |
| Contact | Product owner, Daybreak Labs |

## Evidence

| Ref | What | Where |
|---|---|---|
| E1 | axe-core 4.13.0, WCAG 2.2 A/AA tags (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`), 18 pages × 3 themes, desktop 1280×900, reduced motion emulated. Run 2026-10-05 14:12–14:15 UTC: **0 violations** on all 54 scans. Before the fix in this branch the same run found 30 `target-size` nodes on `/audit` in each theme (fixed by commit `1be8f45a`). Rules axe could not decide ("needs review"): `color-contrast` on 18/18 pages (light, dark) and 17/18 (high contrast); `aria-valid-attr-value` on 18/18; `aria-prohibited-attr` on 4/18; `link-in-text-block` on 1/18. | `packages/maverick-dashboard/tests/browser/accessibility_axe.spec.js` (runs in the browser suite in CI) |
| E2 | Measured accessibility report for build `11075fcf` (4 October 2026): axe on 74 pages × 3 themes, keyboard-only flows, target size, reflow at 320 px, 200% and 400% zoom, reduced motion, landmarks, labels, status messages, Lighthouse. Earlier build; several findings were fixed afterwards (E3). | Measurement report §3.1–§3.6 (`gold/quant/report.md`, evidence workspace) |
| E3 | Stream I fixes on `ux-int`: contrast tokens, links in text, task row link names, keyboard scrolling of table boxes, focus kept after approval decisions, form errors tied to fields, skip link first in the tab order. Re-measured on `/goals`, `/approvals`, `/settings`, `/users`: 0 axe violations in light, dark and high contrast (was 16 failing elements). | Commits `1035aea9`, `1e6e8ad4` and the stream I merge `a1b27a3a` |
| E4 | Sign-in, owner set-up, invitation acceptance and supplier access-code fields: correct `type` and `autocomplete`, no paste blocking, no puzzle (template tests); in a real browser every field took a keyboard paste and a simulated password-manager fill signed in. Run 2026-10-05 14:16 UTC: pass. Vendor console sign-in, set-up, password change and two-step code fields: template tests pass. | `packages/maverick-dashboard/tests/test_sign_in_autofill.py`, `packages/maverick-dashboard/tests/browser/sign_in_paste_autofill.spec.js`, `apps/vendor-console/tests/test_sign_in_autofill.py` |
| E5 | The repository's static template gate (`python -m maverick.a11y_audit --ci`): image text, page language, form labels, link and button names, positive tabindex, heading skips within a file. | `maverick.a11y_audit` |

## Table 1: WCAG 2.2 Level A

| Criterion | Conformance level | Remarks and explanations |
|---|---|---|
| 1.1.1 Non-text Content | Not evaluated | E1 and E5 found no image, button or link without a text alternative on the 18 pages. Whether alternatives are meaningful was not reviewed. Charts were not reviewed. |
| 1.2.1 Audio-only and Video-only (Prerecorded) | Not evaluated | The Walkthroughs page plays videos with a captions track. Not reviewed. |
| 1.2.2 Captions (Prerecorded) | Not evaluated | Walkthrough videos load a captions track; caption accuracy and completeness not reviewed. |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | Not evaluated | Not reviewed. |
| 1.3.1 Info and Relationships | Not evaluated | E1: no violations of the structure rules (labels, lists, tables, landmarks) on 18 pages; `th-has-data-cells` needed review on 5 pages in E2. E2: one `main` and one `h1` on every page. Reading with a screen reader not tested. |
| 1.3.2 Meaningful Sequence | Not evaluated | Not reviewed. |
| 1.3.3 Sensory Characteristics | Not evaluated | Not reviewed. |
| 1.4.1 Use of Color | Not evaluated | E2 found links inside sentences told apart by colour only on up to 25 pages; E3 fixed this on the re-measured pages and E1 found 0 `link-in-text-block` violations on 18 pages × 3 themes (1 page needs review). Colour-only meaning in badges, charts and status dots not reviewed. |
| 1.4.2 Audio Control | Not evaluated | Read-aloud and voice features not reviewed. |
| 2.1.1 Keyboard | Supports | E2 §3.2: the five top flows (send a chat message, approve through its confirmation, change a setting, invite a person, open a task's result) were completed with real key presses. Scroll boxes without keyboard access (E2) were fixed in E3; E1 found 0 `scrollable-region-focusable` violations. Pages outside the scope (for example the workflow canvas) were not tested. |
| 2.1.2 No Keyboard Trap | Supports | E2 §3.2: no keyboard trap in the five flows; dialogs keep focus by design and close with Escape. |
| 2.1.4 Character Key Shortcuts | Not evaluated | The page finder uses Ctrl/Cmd+K. Single-key shortcuts were not inventoried. |
| 2.2.1 Timing Adjustable | Not evaluated | The 30-second Undo window after an approval decision and sign-in session limits were not reviewed against this criterion. |
| 2.2.2 Pause, Stop, Hide | Not evaluated | E2: with reduced motion, running animations stop on all 10 pages tested. Without reduced motion, live-status dots animate indefinitely; not reviewed against this criterion. |
| 2.3.1 Three Flashes or Below Threshold | Not evaluated | Not measured. |
| 2.4.1 Bypass Blocks | Supports | E2: a skip link on 74/74 pages, and Enter moves focus to the main content. E3: the skip link is the first Tab stop. |
| 2.4.2 Page Titled | Supports | E2: 74 pages with 74 distinct titles; E1: no `document-title` violations. |
| 2.4.3 Focus Order | Partially supports | E2 found focus dropped to the page body after an approval decision, after creating an invitation and when the chat composer is rebuilt. E3 keeps focus after approval decisions, and the Users page now moves focus to a new invitation link. The chat composer focus loss was not re-measured on this build. |
| 2.4.4 Link Purpose (In Context) | Not evaluated | E1: no `link-name` violations. E3 named the task row links ("Result", "Steps", "Plan"). Link text was not reviewed by a person. |
| 2.5.1 Pointer Gestures | Not evaluated | Not reviewed. |
| 2.5.2 Pointer Cancellation | Not evaluated | Not reviewed. |
| 2.5.3 Label in Name | Not evaluated | E2 (Lighthouse) found the top-bar "Docs" link named "Documentation (opens in a new tab)"; in this build its name is "Docs (opens in a new tab)", which contains the visible label. Other controls not reviewed. |
| 2.5.4 Motion Actuation | Not evaluated | Not reviewed. |
| 3.1.1 Language of Page | Supports | E2: `lang="en"` on all 74 pages; E1: no `html-has-lang` or `html-lang-valid` violations. |
| 3.2.1 On Focus | Not evaluated | Not reviewed. |
| 3.2.2 On Input | Not evaluated | Display preferences apply a theme as soon as it is chosen (announced, with Undo). Not reviewed against this criterion. |
| 3.2.6 Consistent Help | Not evaluated | The Docs link and Copilot sit in the same place in the shared top bar. Not formally reviewed. |
| 3.3.1 Error Identification | Not evaluated | E2 found errors not tied to their fields in all three forms tested (settings amount, invite email, approval acknowledgement). E3 ties form errors to their fields; not re-measured with assistive technology. |
| 3.3.2 Labels or Instructions | Not evaluated | E1 and E5: every form control has a label on the 18 pages. Instructions not reviewed. |
| 3.3.7 Redundant Entry | Not evaluated | Not reviewed. |
| 4.1.2 Name, Role, Value | Not evaluated | E1: no violations of the name, role and ARIA rules on 18 pages × 3 themes. Needs review: `aria-valid-attr-value` on 18 pages and `aria-prohibited-attr` on 4 pages (an `aria-label` on elements that do not allow one, for example the workspace banner text in E2). No screen reader test. |

## Table 2: WCAG 2.2 Level AA

| Criterion | Conformance level | Remarks and explanations |
|---|---|---|
| 1.2.4 Captions (Live) | Not evaluated | No live audio or video was found in the scope pages; not confirmed for the whole product. |
| 1.2.5 Audio Description (Prerecorded) | Not evaluated | Walkthrough videos not reviewed. |
| 1.3.4 Orientation | Not evaluated | Rotation was not tested (E2 §6). |
| 1.3.5 Identify Input Purpose | Not evaluated | E4: every sign-in field names its purpose (`username`, `current-password`, `new-password`, `one-time-code`); the My work email field uses `email`. Other fields that collect a person's own details were not inventoried. |
| 1.4.3 Contrast (Minimum) | Not evaluated | E1 found 0 `color-contrast` violations on 18 pages × 3 themes, but axe could not decide contrast for many text nodes on every page: translucent "glass" surfaces and the page's gradient background hide the real background colour. E2's solid-surface pass found failures on the earlier build (approval cards in light, muted row links and brand blue on raised surfaces in dark); E3 lifted those tokens and re-measured 0 violations on 4 pages. In the light theme with reduced transparency a body gradient still leaves about 400 contrast results per page undecidable. Stream K (separate branch) is replacing the glass with solid panes, which will change these results; re-measure after it merges. |
| 1.4.4 Resize Text | Supports | E2 §3.3: at 200% browser zoom (and 400%) no page of the 19 tested lost content or scrolled sideways. Text-only zoom was not tested. |
| 1.4.5 Images of Text | Not evaluated | Not reviewed. |
| 1.4.10 Reflow | Supports | E2 §3.3: 76 pages at 320 CSS px, no two-direction scrolling outside data tables (which scroll in their own box); measured on build `11075fcf`, not re-measured on this build. |
| 1.4.11 Non-text Contrast | Not evaluated | E2: the focus outline measured 6.14:1 against its background in the dark theme. Control borders, icons and charts not measured. |
| 1.4.12 Text Spacing | Not evaluated | Not tested (E2 §6). |
| 1.4.13 Content on Hover or Focus | Not evaluated | Not tested (E2 §6). |
| 2.4.5 Multiple Ways | Supports | The sidebar and the page finder (Ctrl/Cmd+K, search by page name) are on every page of the shell; E2 §2 opened the finder and searched on 20 pages. |
| 2.4.6 Headings and Labels | Not evaluated | One `h1` per page and labelled controls (E2, E1); whether headings and labels describe their topic was not reviewed. |
| 2.4.7 Focus Visible | Not evaluated | E2 §3.2: all 295 Tab stops in the five keyboard flows showed a visible indicator (2 px outline) in the dark theme. Light and high-contrast themes were not measured. |
| 2.4.11 Focus Not Obscured (Minimum) | Partially supports | E2 found two failures: the "Workspace" banner link was the first Tab stop and sat behind the sticky sidebar (E3 made the skip link the first Tab stop; whether the banner link can still be hidden was not re-measured), and the first link in the main area after the skip link was fully hidden under the 56 px sticky top bar on Tasks and Approvals (not re-measured on this build). |
| 2.5.7 Dragging Movements | Not evaluated | The workflow canvas and any drag-to-reorder lists were not reviewed. |
| 2.5.8 Target Size (Minimum) | Partially supports | E1: 0 violations on the 18 pages after this branch gave the Audit log's detail toggles a 24 px target. E2 also found Today's task links (19 px tall) and the draft-recovery summaries on Chat (21.7 px); Today is outside E1's pages and both are not re-measured on this build. |
| 3.1.2 Language of Parts | Not evaluated | The shell marks people's own text with `lang="und"` and `dir="auto"`. Not reviewed. |
| 3.2.3 Consistent Navigation | Not evaluated | One shared sidebar and top bar; not formally reviewed. |
| 3.2.4 Consistent Identification | Not evaluated | Not reviewed. |
| 3.3.3 Error Suggestion | Not evaluated | E2: the spending-per-task amount only accepted values like 7.01 or 7.51, and the browser's own message was the only feedback. Not re-measured. |
| 3.3.4 Error Prevention (Legal, Financial, Data) | Not evaluated | Approvals of high-risk requests ask for confirmation and offer a 30-second Undo; raising or removing a spending limit asks first. Not reviewed against this criterion. |
| 3.3.8 Accessible Authentication (Minimum) | Supports | E4: sign-in, owner set-up, invitation acceptance and the supplier access code allow paste and password managers, use the right `autocomplete` values and ask no puzzle; company sign-in (OIDC/SAML) is available. Passkeys are not offered. Two-step codes in the vendor console (an internal tool, not shipped to customers) are covered by template tests only. Step-up checks before sensitive changes send people back to their identity provider and were not tested. |
| 4.1.3 Status Messages | Not evaluated | E2: toasts use polite and assertive live regions and saves report through `role="status"`; noise found (the stop control's label announced after page load, one status line per approval card). No screen reader was used to confirm announcements. |

## Known issues

1. Colour contrast cannot be confirmed on translucent surfaces and the gradient
   background (1.4.3). Many text nodes per page are undecidable in every theme.
   Re-measure after stream K's solid panes merge.
2. Focus can be hidden under the sticky top bar for the first link in the main
   area on Tasks and Approvals (2.4.11), measured on build `11075fcf`.
3. Undersized targets on Today's task links and Chat's draft-recovery
   summaries (2.5.8), measured on build `11075fcf`.
4. Chat composer focus is dropped when the form is rebuilt (2.4.3), measured on
   build `11075fcf`.
5. Best practice, not a WCAG failure: `landmark-unique` ×1 on `/approvals`.
   Needs review: `aria-prohibited-attr` ×1 on `/approvals` (stream I), and on 4
   of the 18 pages in E1.
6. No assistive-technology testing with real users has been done. Screen
   reader, speech input, switch access, forced colours, text spacing and
   content-on-hover were not tested.

## Before this report can be published

- Evaluate every row marked "Not evaluated", including a manual review with
  NVDA and VoiceOver on the top 10 flows, keyboard-only in all three themes,
  200% text-only zoom and 400% zoom (rubric A11Y-1 and A11Y-9).
- Re-run E1 and the E2 measurements on the release build, after stream K.
- Replace this draft's build and date, and keep the report no older than
  12 months.
