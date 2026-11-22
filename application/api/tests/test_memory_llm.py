"""Multi-provider optional memory LLM client."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from memchentic.services import memory_llm
from memchentic.settings import Settings


def _settings(**kwargs: Any) -> Settings:
    base = {
        "MEMORY_LLM_PROVIDER": "openai",
        "MEMORY_LLM_BASE_URL": "https://llm.example.test/v1",
        "MEMORY_LLM_API_KEY": "k",
        "MEMORY_LLM_MODEL": "test-model",
    }
    base.update({k: str(v) for k, v in kwargs.items()})
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_configured_openai_needs_base() -> None:
    assert _settings(MEMORY_LLM_BASE_URL="").memory_llm_configured() is False
    assert _settings(
        MEMORY_LLM_BASE_URL="https://x/v1", MEMORY_LLM_API_KEY=""
    ).memory_llm_configured()


def test_configured_anthropic_google_need_key() -> None:
    assert (
        _settings(
            MEMORY_LLM_PROVIDER="anthropic",
            MEMORY_LLM_BASE_URL="",
            MEMORY_LLM_API_KEY="",
        ).memory_llm_configured()
        is False
    )
    assert _settings(
        MEMORY_LLM_PROVIDER="anthropic",
        MEMORY_LLM_BASE_URL="",
        MEMORY_LLM_API_KEY="k",
    ).memory_llm_configured()
    assert _settings(
        MEMORY_LLM_PROVIDER="google",
        MEMORY_LLM_API_KEY="k",
    ).memory_llm_configured()


@pytest.mark.asyncio
async def test_openai_adapter_parses_array() -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [{"kind": "entry", "title": "T", "body": "Prefer email"}]
                            )
                        }
                    }
                ]
            }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_llm.httpx.AsyncClient", return_value=mock_client):
        rows = await memory_llm.complete_json_array(
            "Extract…",
            settings=_settings(MEMORY_LLM_PROVIDER="openai"),
        )
    assert rows is not None
    assert rows[0]["title"] == "T"
    call_kwargs = mock_client.post.await_args
    assert call_kwargs.args[0].endswith("/chat/completions")


@pytest.mark.asyncio
async def test_anthropic_adapter_parses_array() -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": '```json\n[{"kind":"guide","title":"G","body":"When X do Y"}]\n```',
                    }
                ]
            }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_llm.httpx.AsyncClient", return_value=mock_client):
        rows = await memory_llm.complete_json_array(
            "Extract…",
            settings=_settings(
                MEMORY_LLM_PROVIDER="anthropic",
                MEMORY_LLM_BASE_URL="",
                MEMORY_LLM_API_KEY="k",
                MEMORY_LLM_MODEL="claude-test",
            ),
        )
    assert rows is not None
    assert rows[0]["kind"] == "guide"
    url = mock_client.post.await_args.args[0]
    assert url == "https://api.anthropic.com/v1/messages"
    headers = mock_client.post.await_args.kwargs["headers"]
    assert headers["x-api-key"] == "k"
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_google_adapter_parses_array() -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        [
                                            {
                                                "kind": "entry",
                                                "title": "Port",
                                                "body": "GUI runs on port 18080",
                                                "entry_type": "fact",
                                            }
                                        ]
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_llm.httpx.AsyncClient", return_value=mock_client):
        rows = await memory_llm.complete_json_array(
            "Extract…",
            settings=_settings(
                MEMORY_LLM_PROVIDER="google",
                MEMORY_LLM_BASE_URL="",
                MEMORY_LLM_API_KEY="gkey",
                MEMORY_LLM_MODEL="gemini-2.0-flash",
            ),
        )
    assert rows is not None
    assert rows[0]["entry_type"] == "fact"
    url = mock_client.post.await_args.args[0]
    assert "gemini-2.0-flash:generateContent" in url
    assert mock_client.post.await_args.kwargs["headers"]["x-goog-api-key"] == "gkey"


@pytest.mark.asyncio
async def test_http_error_returns_none() -> None:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_llm.httpx.AsyncClient", return_value=mock_client):
        assert (
            await memory_llm.complete_json_array(
                "x",
                settings=_settings(),
            )
            is None
        )


@pytest.mark.asyncio
async def test_unconfigured_returns_none() -> None:
    assert (
        await memory_llm.complete_json_array(
            "x",
            settings=_settings(MEMORY_LLM_BASE_URL="", MEMORY_LLM_API_KEY=""),
        )
        is None
    )
