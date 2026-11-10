"""IdP directory group listing: provider support, credential and error handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from memchentic.auth import idp_groups
from memchentic.auth.idp_groups import IdPGroupOption


def _settings(**overrides: Any) -> Any:
    base = {
        "oauth_provider": "okta",
        "oauth_client_id": "client-id",
        "oauth_client_secret": "client-secret",
        "oauth_issuer": "https://dev-1.okta.com/oauth2/default",
    }
    return SimpleNamespace(**{**base, **overrides})


async def test_unsupported_provider_reports_no_group_support() -> None:
    result = await idp_groups.list_idp_groups(_settings(oauth_provider="google"))
    assert result.supports_groups is False
    assert result.groups == []
    assert result.error is None


async def test_missing_client_secret_returns_error_result() -> None:
    result = await idp_groups.list_idp_groups(_settings(oauth_client_secret="  "))
    assert result.supports_groups is True
    assert result.groups == []
    assert result.error is not None
    assert "OAUTH_CLIENT_SECRET" in result.error


async def test_adapter_failure_is_returned_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = AsyncMock(side_effect=ValueError("directory unavailable"))
    monkeypatch.setitem(idp_groups._LISTERS, "okta", failing)
    result = await idp_groups.list_idp_groups(_settings())
    assert result.supports_groups is True
    assert result.groups == []
    assert result.error == "directory unavailable"


async def test_adapter_success_returns_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    options = [IdPGroupOption(id="00g1", name="Everyone")]
    monkeypatch.setitem(idp_groups._LISTERS, "okta", AsyncMock(return_value=options))
    result = await idp_groups.list_idp_groups(_settings())
    assert result.groups == options
    assert result.error is None


def test_okta_org_base_strips_authorization_server_path() -> None:
    assert (
        idp_groups._okta_org_base("https://dev-1.okta.com/oauth2/default")
        == "https://dev-1.okta.com"
    )
    with pytest.raises(ValueError, match="OAUTH_ISSUER"):
        idp_groups._okta_org_base("")


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [
        (
            "https://dev-1.okta.com/api/v1/groups?after=x",
            "https://dev-1.okta.com/api/v1/groups?after=x",
        ),
        ("http://dev-1.okta.com/api/v1/groups?after=x", None),
        ("https://evil.example/api/v1/groups?after=x", None),
        ("", None),
        (None, None),
    ],
)
def test_pagination_link_must_stay_on_the_same_https_host(
    next_url: str | None, expected: str | None
) -> None:
    base = "https://dev-1.okta.com/api/v1/groups?limit=200"
    assert idp_groups._same_host_https(base, next_url) == expected
