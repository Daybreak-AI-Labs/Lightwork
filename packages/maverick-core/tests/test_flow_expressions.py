"""Transform functions inside {{ }} templating and bounded-loop controls
(foreach max-iterations + early _break)."""
from __future__ import annotations

from maverick.flow import Flow, FlowNode
from maverick.flow.runner import render, run_flow


class TestExpressions:
    def test_case_and_trim_transforms(self):
        d = {"name": "  aDa  "}
        assert render("{{upper(name)}}", d) == "  ADA  "
        assert render("{{trim(name)}}", d) == "aDa"
        assert render("{{title('hello world')}}", {}) == "Hello World"

    def test_default_falls_back_on_missing_or_empty(self):
        assert render("{{default(missing, 'n/a')}}", {}) == "n/a"
        assert render("{{default(x, 'n/a')}}", {"x": "here"}) == "here"

    def test_concat_and_length_and_replace(self):
        assert render("{{concat(first, ' ', last)}}", {"first": "Ada", "last": "L"}) == "Ada L"
        assert render("{{length(items)}}", {"items": [1, 2, 3]}) == "3"
        assert render("{{replace(s, 'a', 'b')}}", {"s": "banana"}) == "bbnbnb"

    def test_missing_bare_key_stays_literal_but_present_renders(self):
        assert render("hi {{ghost}}", {}) == "hi {{ghost}}"
        assert render("hi {{name}}", {"name": "Ada"}) == "hi Ada"

    def test_bool_renders_lowercase(self):
        assert render("{{flag}}", {"flag": True}) == "true"

    def test_unknown_function_is_treated_as_a_key(self):
        assert render("{{bogus(x)}}", {}) == "{{bogus(x)}}"   # not a known fn, no such key

    def test_nested_function_calls_evaluate(self):
        # a nested call's inner comma must not split the outer args, and the
        # inner call must actually evaluate (was silently "" before the fix)
        assert render("{{upper(default(x, 'fallback'))}}", {}) == "FALLBACK"
        assert render("{{upper(default(x, 'fallback'))}}", {"x": "here"}) == "HERE"
        assert render("{{concat(upper(first), ' ', lower(last))}}",
                      {"first": "ada", "last": "LOVELACE"}) == "ADA lovelace"

    def test_literal_comma_inside_nested_call_survives(self):
        assert render("{{default(missing, concat('a', 'b'))}}", {}) == "ab"


class TestLoopControls:
    def _counting_agent(self):
        calls = []

        def ag(node, brief, data):
            calls.append(brief)
            return ("x", 1.0)
        return ag, calls

    def test_foreach_limit_caps_iterations(self):
        body = Flow(id="b", name="b", start="s",
                    nodes={"s": FlowNode(id="s", kind="agent", brief="do {{item}}")})
        f = Flow(id="f", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind="foreach", items="rows", var="item", limit=2, body=body)})
        ag, calls = self._counting_agent()
        run_flow(f, agent_fn=ag, action_fn=lambda *a: ("", None), data={"rows": [1, 2, 3, 4]})
        assert calls == ["do 1", "do 2"]

    def test_foreach_break_stops_early(self):
        body = Flow(id="b", name="b", start="s",
                    nodes={"s": FlowNode(id="s", kind="action", tool="t")})
        f = Flow(id="f", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind="foreach", items="rows", var="item", body=body)})
        seen = []

        def action(node, params, data):
            seen.append(data.get("item"))
            if data.get("item") == 2:
                data["_break"] = True          # ask the loop to stop after this item
            return ("ok", 1.0)
        run_flow(f, agent_fn=lambda *a: ("", None), action_fn=action, data={"rows": [1, 2, 3, 4]})
        assert seen == [1, 2]                   # stopped after the break, not all 4
