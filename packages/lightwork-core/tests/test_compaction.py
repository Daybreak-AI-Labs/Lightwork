"""Long-context compaction."""
from __future__ import annotations

from lightwork.compaction import (
    compact_messages,
    make_heuristic_digest,
    should_digest,
)


class TestCompactMessages:
    def test_short_list_passes_through(self):
        msgs = [{"role": "user", "content": "hi"}]
        out = compact_messages(msgs, keep_recent=4)
        assert out == msgs

    def test_recent_messages_untouched(self):
        """The last `keep_recent` messages must not be shrunk."""
        msgs = [
            {"role": "user", "content": "the brief"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                          "content": "huge " * 10000}]},
            {"role": "assistant", "content": "ok2"},
            {"role": "user", "content": "recent-1"},
            {"role": "assistant", "content": "recent-2"},
        ]
        out = compact_messages(msgs, keep_recent=2, max_tool_bytes=100)
        # First (user brief) preserved.
        assert out[0] == msgs[0]
        # Recent two unchanged.
        assert out[-2:] == msgs[-2:]

    def test_old_oversized_tool_result_shrunk(self):
        big = "x" * 5000
        msgs = [
            {"role": "user", "content": "brief"},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "abc",
                "content": big,
            }]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "recent"},
        ]
        out = compact_messages(msgs, keep_recent=2, max_tool_bytes=200)
        # The big tool_result is now small with a digest hint.
        shrunk = out[1]["content"][0]
        assert isinstance(shrunk, dict)
        assert "truncated" in shrunk["content"]
        assert "5000B" in shrunk["content"]
        assert len(shrunk["content"]) < 500

    def test_under_threshold_tool_result_passes_through(self):
        small = "x" * 50
        msgs = [
            {"role": "user", "content": "brief"},
            {"role": "user", "content": [{"type": "tool_result",
                                          "tool_use_id": "abc",
                                          "content": small}]},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "user", "content": "recent"},
        ]
        out = compact_messages(msgs, keep_recent=2, max_tool_bytes=200)
        assert out[1]["content"][0]["content"] == small

    def test_text_content_string_truncated(self):
        msgs = [
            {"role": "user", "content": "brief"},
            {"role": "assistant", "content": "y" * 5000},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "ok"},
        ]
        out = compact_messages(msgs, keep_recent=2, max_tool_bytes=200)
        assert "truncated" in out[1]["content"]
        assert len(out[1]["content"]) < 500


class TestShouldDigest:
    def test_zero_step_no(self):
        assert should_digest(0) is False

    def test_multiples_yes(self):
        assert should_digest(10, every=10) is True
        assert should_digest(20, every=10) is True

    def test_non_multiples_no(self):
        assert should_digest(7, every=10) is False
        assert should_digest(15, every=10) is False


class TestHeuristicDigest:
    def test_includes_first_user_brief(self):
        msgs = [
            {"role": "user", "content": "Plan a trip to Lisbon."},
            {"role": "assistant", "content": [{"type": "tool_use",
                                               "id": "1", "name": "shell",
                                               "input": {"cmd": "ls"}}]},
        ]
        out = make_heuristic_digest(msgs)
        assert "Lisbon" in out
        assert "<digest>" in out
        assert "</digest>" in out

    def test_counts_tool_invocations(self):
        msgs = [
            {"role": "user", "content": "brief"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "shell", "input": {}},
                {"type": "tool_use", "id": "2", "name": "shell", "input": {}},
                {"type": "tool_use", "id": "3", "name": "read_file", "input": {}},
            ]},
        ]
        out = make_heuristic_digest(msgs)
        assert "shell(2)" in out
        assert "read_file(1)" in out

    def test_empty_messages(self):
        assert make_heuristic_digest([]) == ""
