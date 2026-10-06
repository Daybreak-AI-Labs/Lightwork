"""Shared UI helpers instead of page copies.

Pages each carried their own DOM builder, fetch wrapper and status-line
setter. Eight copies of the DOM builder took ``el(tag, text, cls)`` while
the shared ``mvEl`` and every other copy take ``(tag, cls, text)``, so a call
copied from one page to another silently put the text in the class. These
ratchets keep one argument order, and keep the shared fetch and status
helpers in lightwork-ui.js.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"
_BUILDER = re.compile(r"function\s+el\s*\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*(\w+)\s*)?\)")


def _scripts() -> list[Path]:
    return sorted([*PACKAGE.glob("static/*.js"), *PACKAGE.glob("templates/*.html"), *PACKAGE.glob("templates/*.js")])


def test_dom_builders_take_the_class_before_the_text() -> None:
    reversed_order = []
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        for match in _BUILDER.finditer(text):
            second, third = match.group(2), match.group(3) or ""
            if re.fullmatch(r"te?xt|label|content", second) and re.fullmatch(r"cls|className|klass|cl", third):
                line = text.count("\n", 0, match.start()) + 1
                reversed_order.append(f"{path.relative_to(PACKAGE)}:{line}")
    assert reversed_order == [], "Write el(tag, cls, text), the order mvEl uses: " + ", ".join(reversed_order)


def test_the_shared_fetch_and_status_helpers_exist() -> None:
    ui = (PACKAGE / "static" / "lightwork-ui.js").read_text(encoding="utf-8")
    assert "window.mvFetchJSON = async function (url, opts, action)" in ui
    assert "window.mvStatus = function (element, text, opts)" in ui


_DEFINITION = re.compile(
    r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\s*)?\([^)]*\)\s*(?:=>)?\s*\{"
)

# The local fetch-and-read wrappers still allowed, each because moving it onto
# mvFetchJSON would change what the page does:
_KEPT_WRAPPERS = {
    # Also runs on the recovery page, whose shell has no lightwork-ui.js.
    ("lightwork-provider-spending.js", "request"),
    # Checks the page's access token on both sides of the request and of the
    # body read, and hands the Response to observe(); a stale answer from a
    # previous sign-in is dropped between the two.
    ("lightwork-source-analysis.js", "json"),
    ("lightwork-workspace-members.js", "api"),
    # From the owner's PR #2529 (Settings recovery, taught-workflow releases,
    # instruction records, the privacy handoff and workflow action forms):
    # each shows the server's own detail sentence as written, and its own
    # status-specific wording, where mvFetchJSON would put mvErrorText's
    # words. Kept as the owner wrote them until they are moved deliberately.
    ("lightwork-webhook-recovery.js", "request"),
    ("lightwork-local-flow-release.js", "request"),
    ("lightwork-instruction-capture.js", "request"),
    ("lightwork-dsar-handoff.js", "call"),
    ("flow_designer_action_fields.js", "task"),
    # Also from #2529 (local AI runtime, retention maintenance): each shows the
    # server's own detail and checks the workspace context of the reply
    # before it counts, in the page's own words.
    ("lightwork-local-runtime.js", "call"),
    ("lightwork-retention-maintenance.js", "call"),
    # From the owner's later commits on #2529 (learning, competence, private
    # rules, viewpoints, plugin practice, questionnaires, notices, imports,
    # tool removal, team updates, answer feedback). Every one shows the
    # server's own sentence as written, with the page's own fallback for a
    # reply that carries none (mvFetchJSON filters it through mvPlainDetail
    # and falls back to mvErrorText). Most also do what mvFetchJSON cannot:
    # a 409 whose sentence says the account or workspace changed clears the
    # page's private values (onAccess covers 401/403 only), a reply's context
    # is compared with the page's before it counts, and a timeout or
    # unreadable reply says the original outcome is unknown and that nothing
    # repeats. Kept until mvFetchJSON grows these, then moved together.
    ("lightwork-answer-panel.js", "request"),
    ("lightwork-automation-imports.js", "request"),
    ("lightwork-competence-transport.js", "request"),
    ("lightwork-compliance-questionnaire-builder.js", "call"),
    ("lightwork-department-team-updates.js", "request"),
    ("lightwork-generated-tool-removal.js", "request"),
    ("lightwork-learning-distinctions.js", "request"),
    ("lightwork-learning-holdout-setup.js", "request"),
    ("lightwork-learning-jobs.js", "request"),
    ("lightwork-learning-passes.js", "request"),
    ("lightwork-learning-pending-cases.js", "request"),
    ("lightwork-learning-test-cases.js", "request"),
    ("lightwork-model-answer-scoring.js", "request"),
    ("lightwork-plugin-practice.js", "call"),
    ("lightwork-plugin-practice.js", "checkResult"),
    ("lightwork-privacy-notice-proposals.js", "call"),
    ("lightwork-procedure-interactions.js", "request"),
    ("lightwork-reward-model-training.js", "request"),
    ("lightwork-viewpoints.js", "request"),
    ("lightwork-work-restructuring.js", "request"),
    ("lightwork-workflow-step-checks.js", "request"),
}

# Status-line setters that still write the text themselves, each named with
# why it does not go through mvStatus:
_KEPT_SETTERS = {
    # Not a status line: rendered results, output blocks, badges or translated labels.
    ("lightwork-answer-panel.js", "say"),
    ("lightwork-guided-actions.js", "show"),
    ("lightwork-maintenance-ops.js", "show"),
    ("lightwork-notification-badge.js", "show"),
    ("lightwork-privacy-rights-consumer-delivery.js", "status"),
    ("audit.html", "show"),
    ("compliance_scope_actions.html", "show"),
    ("compliance_scoping.html", "show"),
    ("learning.html", "show"),
    ("privacy_notice_governance.html", "show"),
    ("privacy_rights_consumer_delivery.html", "show"),
    ("privacy_rights_orchestration.html", "show"),
    # The line is its own live region and turns into an alert for a failure;
    # mvStatus leaves a page's own live region alone, so failures would stop
    # being assertive.
    ("lightwork-consent-workbench.js", "say"),
    ("lightwork-consent-publish-settings.js", "say"),
    ("lightwork-plugins.js", "setStatus"),
    ("lightwork-vendor-onboarding.js", "show"),
    ("privacy_notice_governance.html", "say"),
    # A note inside one settings row; the page announces through its own region.
    ("lightwork-settings-all.js", "say"),
    # Pages served without the dashboard shell (no lightwork-ui.js).
    ("lightwork-third-party-portal.js", "setStatus"),
    ("lightwork-workspace-recovery.js", "say"),
}


def _definitions():
    for path in _scripts():
        if path.name == "lightwork-ui.js":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _DEFINITION.finditer(text):
            start, depth = match.end() - 1, 0
            for end in range(start, len(text)):
                if text[end] == "{":
                    depth += 1
                elif text[end] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            yield path.name, match.group(1) or match.group(2), text[start:end + 1]


def test_pages_read_json_through_mv_fetch_json() -> None:
    wrappers = {
        (name, function)
        for name, function, body in _definitions()
        if "fetch(" in body and ".json()" in body and len(body) < 1500
    }
    assert wrappers - _KEPT_WRAPPERS == set(), "Send the request through window.mvFetchJSON"
    assert _KEPT_WRAPPERS - wrappers == set(), "A kept wrapper is gone; drop it from _KEPT_WRAPPERS"


def test_status_lines_are_written_through_mv_status() -> None:
    setters = {
        (name, function)
        for name, function, body in _definitions()
        if function in {"say", "setStatus", "status", "show"}
        and "textContent" in body and "mvStatus" not in body and len(body) < 600
    }
    assert setters - _KEPT_SETTERS == set(), "Write the status line with window.mvStatus"
    assert _KEPT_SETTERS - setters == set(), "A kept setter is gone; drop it from _KEPT_SETTERS"
