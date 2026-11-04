"""Shield guard: tool-call scanning over nested arguments."""

from __future__ import annotations


class TestScanToolCallNestedArgs:
    def test_rm_rf_in_nested_list_is_blocked(self):
        from maverick_shield.guard import Shield
        s = Shield(backend="builtin")
        # The dangerous command is split across a list; scanning repr(args)
        # would bury it behind quotes/commas so the rm_rf_root rule never matches.
        v = s.scan_tool_call("shell", {"argv": ["rm", "-rf", "/"]})
        assert not v.allowed

    def test_arg_strings_collects_nested_leaves(self):
        from maverick_shield.guard import _collect_arg_strings
        leaves = _collect_arg_strings({"a": ["x", {"b": "y"}], "c": 3})
        assert "x" in leaves and "y" in leaves
