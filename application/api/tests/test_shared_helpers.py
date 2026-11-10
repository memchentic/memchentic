"""Unit tests for small helpers shared by the REST, OAuth and MCP entry points."""

from __future__ import annotations

import base64
import hashlib

import pytest

from memchentic.auth.claims import email_verified_from_claims
from memchentic.auth.principal import bearer_token_from_header
from memchentic.auth.providers import pkce_s256_challenge
from memchentic.auth.web_oauth import pkce_pair


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("Basic abc", None),
        ("Bearer ", None),
        ("Bearer abc", "abc"),
        ("bearer   abc  ", "abc"),
        ("BEARER abc", "abc"),
    ],
)
def test_bearer_token_from_header(value: str | None, expected: str | None) -> None:
    assert bearer_token_from_header(value) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("", False),
        (None, False),
    ],
)
def test_email_verified_from_claims(raw: object, expected: bool) -> None:
    assert email_verified_from_claims({"email_verified": raw}) is expected


def test_email_verified_from_claims_missing_is_false() -> None:
    assert email_verified_from_claims({}) is False


def test_pkce_s256_challenge_matches_rfc7636_example() -> None:
    """RFC 7636 Appendix B test vector."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_s256_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_pair_is_consistent() -> None:
    verifier, challenge = pkce_pair()
    assert pkce_s256_challenge(verifier) == challenge


def test_pkce_s256_challenge_accepts_non_ascii() -> None:
    digest = hashlib.sha256("é".encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert pkce_s256_challenge("é") == expected
