"""Test suite for src/category_params.py -- the single category normaliser
and threshold accessor introduced for the Best Options evaluator (see
.squad/decisions/inbox/danny-best-options-design.md, finding F9, and
.squad/decisions/inbox/linus-best-options-scoring.md).

Hermetic: no network, no I/O. Cross-checks every threshold value against
rule_evaluator.CATEGORY_THRESHOLDS_CC/CSP directly to guarantee this
module never becomes a second, drifting source of truth for those
numbers.
"""

import pytest

from src.category_params import (
    DEFAULT_CATEGORY,
    category_label,
    normalize_category,
    resolve_category,
    thresholds_for,
)
from src.rule_evaluator import CATEGORY_THRESHOLDS_CC, CATEGORY_THRESHOLDS_CSP


class TestResolveCategory:
    @pytest.mark.parametrize("raw", [None, "", "   ", "not-a-real-category", "xyz"])
    def test_missing_or_unrecognised_defaults_and_flags(self, raw):
        key, defaulted = resolve_category(raw)
        assert key == DEFAULT_CATEGORY
        assert defaulted is True

    @pytest.mark.parametrize("raw", ["balanced", "Balanced", "BALANCED", " balanced "])
    def test_explicit_default_category_is_not_flagged_as_a_guess(self, raw):
        key, defaulted = resolve_category(raw)
        assert key == "balanced"
        assert defaulted is False

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("high_yield", "high_yield"),
            ("high yield", "high_yield"),
            ("High Yield", "high_yield"),
            ("high-yield", "high_yield"),
            ("highyield", "high_yield"),
            ("rising_star", "rising_star"),
            ("Rising Star", "rising_star"),
            ("rising-star", "rising_star"),
            ("risingstar", "rising_star"),
            ("aristocrat", "aristocrat"),
            ("Aristocrat", "aristocrat"),
            ("compounder", "compounder"),
        ],
    )
    def test_every_alias_form_normalises_to_the_canonical_key(self, raw, expected):
        key, defaulted = resolve_category(raw)
        assert key == expected
        assert defaulted is False

    def test_resolves_every_key_rule_evaluator_itself_knows_about(self):
        # rule_evaluator's own CC table is the exhaustive list of valid
        # canonical categories -- every one of them must resolve to itself,
        # unflagged, or this module has silently dropped a category.
        for key in CATEGORY_THRESHOLDS_CC:
            resolved_key, defaulted = resolve_category(key)
            assert resolved_key == key
            assert defaulted is False


class TestNormalizeCategory:
    def test_is_a_thin_wrapper_returning_only_the_key(self):
        assert normalize_category("high yield") == resolve_category("high yield")[0]
        assert normalize_category(None) == resolve_category(None)[0]


class TestCategoryLabel:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("high_yield", "High Yield"),
            ("balanced", "Balanced"),
            ("rising_star", "Rising Star"),
            ("aristocrat", "Aristocrat"),
            ("compounder", "Compounder"),
        ],
    )
    def test_title_cases_the_underscore_key(self, key, expected):
        assert category_label(key) == expected


class TestThresholdsFor:
    def test_covered_call_thresholds_match_rule_evaluator_verbatim(self):
        for key, expected in CATEGORY_THRESHOLDS_CC.items():
            assert thresholds_for("covered_call", key) == expected

    def test_cash_secured_put_thresholds_match_rule_evaluator_verbatim(self):
        for key, expected in CATEGORY_THRESHOLDS_CSP.items():
            assert thresholds_for("cash_secured_put", key) == expected

    def test_normalises_the_category_argument_before_lookup(self):
        assert thresholds_for("covered_call", "High Yield") == CATEGORY_THRESHOLDS_CC["high_yield"]
        assert thresholds_for("covered_call", None) == CATEGORY_THRESHOLDS_CC[DEFAULT_CATEGORY]

    def test_returns_a_copy_not_a_shared_mutable_reference(self):
        # Mutating the returned dict must not corrupt rule_evaluator's own
        # source-of-truth table for subsequent callers.
        result = thresholds_for("covered_call", "balanced")
        result["delta_lo"] = -999.0
        assert CATEGORY_THRESHOLDS_CC["balanced"]["delta_lo"] != -999.0

    def test_unknown_strategy_raises_value_error(self):
        with pytest.raises(ValueError):
            thresholds_for("not_a_strategy", "balanced")