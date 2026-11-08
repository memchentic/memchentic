"""Unit tests for ILIKE wildcard escaping."""

from __future__ import annotations

from memchentic.search.hybrid import escape_ilike


def test_escape_ilike_escapes_percent_underscore_backslash() -> None:
    assert escape_ilike(r"a%b_c\d") == r"a\%b\_c\\d"
    assert escape_ilike("plain") == "plain"
