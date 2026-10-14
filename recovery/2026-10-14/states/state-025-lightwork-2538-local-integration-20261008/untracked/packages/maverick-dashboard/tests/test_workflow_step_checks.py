from __future__ import annotations

import pytest
from fastapi import HTTPException
from maverick_dashboard.workflow_step_checks import (
    ReviewIn,
    compare_expected_answer,
    validate_expected_answer,
)


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        ("approved", "approved", True),
        ("approved", "Approved", False),
        (" approved ", "approved", False),
        ("", "", True),
        (True, "Yes", True),
        (False, "Yes", False),
        (7, "7", True),
    ],
)
def test_expected_answer_is_an_exact_plain_language_comparison(actual, expected, matches):
    result = compare_expected_answer(actual, expected)

    assert result["expected"] == expected
    assert result["matches"] is matches
    assert "value you entered" in result["message"]


def test_expected_answer_refuses_values_the_step_check_cannot_display_safely():
    with pytest.raises(ValueError, match="supported text, whole-number or yes/no"):
        compare_expected_answer({"secret": "nested result"}, "secret")


def test_review_request_keeps_optional_expected_answer_exact_and_bounded():
    common = {"flow": {"id": "sample"}, "step": "review", "context": "a" * 64,
              "inputs": {"amount": 2}}

    assert ReviewIn(**common, expected_answer="  Exact  ").expected_answer == "  Exact  "
    assert ReviewIn(**common).expected_answer is None
    with pytest.raises(ValueError):
        ReviewIn(**common, expected_answer="x" * 4097)


def test_expected_answer_size_and_unicode_are_checked_before_review():
    validate_expected_answer("😀" * 4096)
    with pytest.raises(HTTPException, match="unsupported text"):
        validate_expected_answer("\ud800")
    with pytest.raises(HTTPException, match="16 KB"):
        validate_expected_answer("x" * (16 * 1024 + 1))
