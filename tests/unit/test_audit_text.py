"""The audit line says what changed (spec E3), without secrets."""

from app.observability.audit_text import AUDIT_MAX_CHANGES, AUDIT_SUMMARY_CHARS, change_summary
from app.observability.masking import MASK


def test_a_mode_change_shows_both_values() -> None:
    assert change_summary("paper", "semi_auto") == "paper \u2192 semi_auto"


def test_two_settings_show_only_the_changed_keys() -> None:
    before = {"profile": "normal", "settings": {"risk_per_trade_pct": 0.5, "max_open": 3}}
    after = {"profile": "custom", "settings": {"risk_per_trade_pct": 1.0, "max_open": 3}}
    assert change_summary(before, after) == (
        "profile normal \u2192 custom, settings.risk_per_trade_pct 0.5 \u2192 1.0"
    )
    assert change_summary(before, before) == "no change"
    assert change_summary({"symbols": ["A", "B"]}, {"symbols": ["A"]}) == (
        "symbols [A, B] \u2192 [A]"
    )
    assert change_summary({}, {"new": 1}) == "new none \u2192 1"


def test_one_sided_values_and_lists() -> None:
    assert change_summary(None, {"ticket": 7}) == "ticket=7"
    assert change_summary({"halted": "daily loss"}, None) == "halted=daily loss"
    assert change_summary(None, None) == ""
    assert change_summary(["EURUSD"], ["EURUSD", "USDJPY"]) == "EURUSD \u2192 EURUSD, USDJPY"
    assert change_summary(None, {"b", "a"}) == "a, b"


def test_secrets_never_reach_the_line() -> None:
    before = {"cloud": {"url": "https://a.example", "api_key": "old-secret-value"}}
    after = {"cloud": {"url": "https://b.example", "api_key": "new-secret-value"}}
    text = change_summary(before, after)
    assert "secret-value" not in text
    assert f"cloud.api_key {MASK} \u2192 {MASK}" in text
    assert "secret" not in change_summary(None, {"password": "secret"})


def test_long_changes_are_cut() -> None:
    many = change_summary({f"k{i}": i for i in range(12)}, {f"k{i}": i + 1 for i in range(12)})
    assert many.endswith(f", {12 - AUDIT_MAX_CHANGES} more")
    assert many.count("\u2192") == AUDIT_MAX_CHANGES
    long = change_summary("x" * 500, "y")
    assert len(long) == AUDIT_SUMMARY_CHARS and long.endswith("...")
