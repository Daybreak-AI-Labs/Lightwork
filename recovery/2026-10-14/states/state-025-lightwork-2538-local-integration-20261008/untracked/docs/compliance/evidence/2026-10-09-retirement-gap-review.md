# AI-system retirement: code-review correction

**Review date:** 2026-10-09  
**Review type:** Local source and documentation review; not a certification audit  
**Owner approval:** Pending  
**Deployment status:** No installation was changed or verified

## Finding

`packages/maverick-core/maverick/retirement.py` provides an internal helper
that can create a signed `AI_SYSTEM_RETIRED` event when called with the needed
dependencies. No customer page, dashboard API, or CLI invokes it. The helper
does not provide an operator-facing inventory, approval/review flow, or a
verified mapping from an AI system to the data subjects eligible for erasure.
Previous compliance materials described the code helper as an implemented,
closed, and conforming customer control. That overstated the shipped product.

## Local correction

The core helper now leaves `erased` false when no erase handler is supplied,
when the explicit subject scope is missing, or when any requested deletion
step fails. Partial operation details are retained with safe messages, and raw
exception text is kept out of the signed customer-facing record. Archive
failures now follow the same privacy rule, and an archive choice without an
archive handler is explicitly marked incomplete. The focused retirement suite
passed 16 tests locally on October 9. The operation remains unavailable through
Lightwork's customer UI/CLI, so the customer lifecycle journey is still open.

The current risk register now marks R-24 open. The crosswalk and program-status
pages describe the control as partial. The approved v1.0 policy and SoA have not
been approved for amendment; their working copies label the proposed correction
as pending owner review. The June 24 management-review record and Q2 audit report
remain historical; the Q2 report now links this dated correction.

## Evidence boundary

This review establishes only local source behavior and focused tests. It does
not establish an installed-customer journey, a complete system-to-data map,
external audit custody, or certification conformity. No GitHub, CI, merge,
deployment, or customer-system action was performed.
