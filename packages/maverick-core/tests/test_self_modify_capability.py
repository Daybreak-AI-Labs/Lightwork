"""Capability-algebra non-escalation proof for a code patch (Gap 5).

Pins the differential: a patch widens authority iff it introduces a capability
CLASS the baseline did not already use, decided through Capability.permits (the
real least-privilege algebra), and that a missing baseline can only over-report.
"""
from __future__ import annotations

from maverick import self_modify_capability as cap
from maverick.self_modify_capability import capability_delta, classes_in, widens


def _patch(added: str, *, context: str = "old", removed: str | None = None) -> str:
    body = f" {context}\n"
    if removed is not None:
        body += f"-{removed}\n"
    body += f"+{added}\n"
    return f"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n{body}"


class TestClassesIn:
    def test_detects_named_classes(self):
        assert classes_in(["import socket"]) == frozenset({"network"})
        assert classes_in(["subprocess.run(x)"]) == frozenset({"process"})
        assert "dynamic_code" in classes_in(["y = eval(z)"])
        assert "fs_write" in classes_in(["p.write_text('x')"])

    def test_comment_only_line_is_not_a_capability(self):
        assert classes_in(["# subprocess is avoided"]) == frozenset()

    def test_clean_code_has_no_classes(self):
        assert classes_in(["value = 1 + 2", "return value"]) == frozenset()


class TestWidensDifferential:
    def test_adding_a_new_class_widens(self):
        # baseline (context) has no network; the added line introduces it.
        w, new = widens(_patch("import socket"))
        assert w is True and new == ["network"]

    def test_editing_within_an_existing_class_does_not_widen(self):
        # context already does networking; the patch tweaks a network call.
        p = _patch("resp = requests.post(url)", context="resp = requests.get(url)")
        w, new = widens(p)
        assert w is False and new == []

    def test_clean_edit_does_not_widen(self):
        w, new = widens(_patch("TUNING = 0.7"))
        assert w is False and new == []

    def test_baseline_files_prevent_false_positive(self):
        # The added line uses network, but the full baseline file already did too,
        # so with the real file as baseline it is NOT an escalation.
        p = _patch("s2 = socket.socket()")
        w, _ = widens(p, baseline_files={"x.py": "import socket\ns = socket.socket()"})
        assert w is False

    def test_missing_baseline_is_conservative(self):
        # Same added line, but WITHOUT the baseline file the context under-counts
        # and the change reads as introducing network -> over-reports (fail-closed).
        p = _patch("s2 = socket.socket()")
        w, _ = widens(p)
        assert w is True


class TestCapabilityDeltaShape:
    def test_returns_before_after_probe_over_the_algebra(self):
        before, after, probe = capability_delta(_patch("subprocess.run(x)"))
        assert probe == cap.CAPABILITY_CLASSES
        # after permits 'process'; before does not -> the gate sees escalation.
        assert after.permits("process") is True
        assert before.permits("process") is False
        # a class neither uses is permitted by neither.
        assert after.permits("network") is False

    def test_empty_grant_permits_nothing_not_everything(self):
        # A clean patch -> before and after both permit no capability class
        # (guards the capability-algebra 'empty allow == all' inversion).
        before, after, probe = capability_delta(_patch("x = 1"))
        assert not any(before.permits(c) for c in probe)
        assert not any(after.permits(c) for c in probe)
