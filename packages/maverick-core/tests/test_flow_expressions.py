"""Transform functions inside {{ }} templating and bounded-loop controls
(foreach max-iterations + early _break)."""
from __future__ import annotations

from maverick.flow import Flow, FlowNode
from maverick.flow.runner import eval_condition, render, run_flow


class TestArithmeticAndLists:
    def test_arithmetic(self):
        assert render("{{add(a, b)}}", {"a": 2, "b": 3}) == "5"      # int, not 5.0
        assert render("{{sub(10, 3)}}", {}) == "7"
        assert render("{{mul(qty, price)}}", {"qty": 3, "price": 4}) == "12"
        assert render("{{div(10, 4)}}", {}) == "2.5"
        assert render("{{div(1, 0)}}", {}) == "0"                    # divide-by-zero -> 0

    def test_nested_arithmetic_and_round(self):
        assert render("{{round(div(10, 3), 2)}}", {}) == "3.33"

    def test_list_first_last(self):
        assert render("{{first(items)}}", {"items": ["a", "b"]}) == "a"
        assert render("{{last(items)}}", {"items": ["a", "b"]}) == "b"


class TestConditionComposition:
    def test_single_predicate_still_works(self):
        assert eval_condition("x > 5", {"x": 10}) is True
        assert eval_condition("x > 5", {"x": 1}) is False

    def test_and_requires_both(self):
        c = "amount > 100 and region == 'EU'"
        assert eval_condition(c, {"amount": 150, "region": "EU"}) is True
        assert eval_condition(c, {"amount": 150, "region": "US"}) is False
        assert eval_condition(c, {"amount": 50, "region": "EU"}) is False

    def test_or_requires_either(self):
        c = "a == 1 or b == 2"
        assert eval_condition(c, {"a": 9, "b": 2}) is True
        assert eval_condition(c, {"a": 1, "b": 9}) is True
        assert eval_condition(c, {"a": 9, "b": 9}) is False

    def test_or_of_ands_precedence(self):
        # (tier == gold) OR (amount > 1000 AND region == 'EU')
        c = "tier == 'gold' or amount > 1000 and region == 'EU'"
        assert eval_condition(c, {"tier": "gold", "amount": 0, "region": "US"}) is True
        assert eval_condition(c, {"tier": "silver", "amount": 2000, "region": "EU"}) is True
        assert eval_condition(c, {"tier": "silver", "amount": 2000, "region": "US"}) is False


class TestOrderingComparisons:
    def test_iso_date_strings_order_lexically(self):
        # the footgun: a date compare used to force float() and silently be False
        assert eval_condition("created >= '2026-01-01'", {"created": "2026-07-05"}) is True
        assert eval_condition("created >= '2026-01-01'", {"created": "2025-12-31"}) is False
        assert eval_condition("created < '2026-01-01'", {"created": "2025-06-30"}) is True

    def test_plain_string_ordering(self):
        assert eval_condition("tier > 'a'", {"tier": "b"}) is True
        assert eval_condition("tier < 'a'", {"tier": "b"}) is False

    def test_numeric_ordering_stays_numeric_not_lexical(self):
        # 10 vs 9 must be numeric (10 > 9), not the string-wise '10' > '9' == False
        assert eval_condition("n > 9", {"n": 10}) is True
        assert eval_condition("n >= 100", {"n": 100}) is True

    def test_missing_key_is_never_ordered_true(self):
        assert eval_condition("ghost >= '2026-01-01'", {}) is False
        assert eval_condition("ghost > 5", {}) is False


class TestSecretFunction:
    def test_secret_resolves_from_the_provider(self, monkeypatch):
        monkeypatch.setenv("STRIPE_KEY", "sk_test_123")
        assert render("{{secret('STRIPE_KEY')}}", {}) == "sk_test_123"

    def test_missing_secret_is_empty_not_the_key(self, monkeypatch):
        monkeypatch.delenv("NO_SUCH_SECRET", raising=False)
        assert render("{{secret('NO_SUCH_SECRET')}}", {}) == ""

    def test_secret_in_an_action_param_is_not_persisted_in_run_data(self, monkeypatch):
        # The whole point: the connector gets the resolved secret, but it never
        # lands in the flow's persisted data (only the transient rendered param).
        monkeypatch.setenv("API_KEY", "s3kret-value")
        seen = {}

        def action(node, params, data):
            seen["params"] = params
            return ("ok", 1.0)
        f = Flow(id="f", name="f", start="a",
                 nodes={"a": FlowNode(id="a", kind="action", tool="t",
                                      params={"key": "{{secret('API_KEY')}}"}, output="r")})
        res = run_flow(f, agent_fn=lambda *a: ("", None), action_fn=action)
        assert seen["params"]["key"] == "s3kret-value"   # connector receives it
        assert "s3kret-value" not in str(res.data)        # but flow data doesn't carry it


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
