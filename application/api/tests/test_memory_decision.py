"""System One decision gate for ingest candidates."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from memchentic.services import memory_decision, memory_ingest
from memchentic.settings import Settings, get_settings


def _settings(**kwargs: Any) -> Settings:
    base = {
        "MEMORY_DECISION_BASE_URL": "https://decision.example.test",
        "MEMORY_DECISION_API_KEY": "k",
        "MEMORY_DECISION_MODEL": "kev-test",
    }
    base.update({k: str(v) for k, v in kwargs.items()})
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_configured_needs_base() -> None:
    assert _settings(MEMORY_DECISION_BASE_URL="").memory_decision_configured() is False
    assert _settings(
        MEMORY_DECISION_BASE_URL="https://x", MEMORY_DECISION_API_KEY=""
    ).memory_decision_configured()


@pytest.mark.asyncio
async def test_system_one_posts_and_returns_answers() -> None:
    answers = {
        "kind": {
            "type": "choice",
            "choice": "entry",
            "confidence": 0.8,
            "probabilities": {"entry": 0.7, "guide": 0.2, "skip": 0.1},
        },
        "entry_type": {"type": "choice", "choice": "preference", "confidence": 0.6},
        "keep": {"type": "noul", "noul": 0.9},
    }

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"model": "kev-test", "answers": answers}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_decision.httpx.AsyncClient", return_value=mock_client):
        got = await memory_decision.system_one(
            "Prefer dark mode",
            memory_decision._CANDIDATE_QUESTIONS,
            settings=_settings(),
        )
    assert got == answers
    call_kwargs = mock_client.post.await_args
    assert call_kwargs.args[0] == "https://decision.example.test/v1/systemone"
    assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer k"
    assert call_kwargs.kwargs["json"]["model"] == "kev-test"


@pytest.mark.asyncio
async def test_system_one_http_error_returns_none() -> None:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_decision.httpx.AsyncClient", return_value=mock_client):
        assert (
            await memory_decision.system_one(
                "x",
                memory_decision._CANDIDATE_QUESTIONS,
                settings=_settings(),
            )
            is None
        )


@pytest.mark.asyncio
async def test_system_one_unconfigured_returns_none() -> None:
    assert (
        await memory_decision.system_one(
            "x",
            memory_decision._CANDIDATE_QUESTIONS,
            settings=_settings(MEMORY_DECISION_BASE_URL=""),
        )
        is None
    )


@pytest.mark.asyncio
async def test_rescore_overrides_kind_and_drops_skip() -> None:
    rows = [
        {
            "kind": "entry",
            "title": "Pref",
            "body": "Prefer dark mode in the IDE.",
            "confidence": 0.5,
            "reasons": ["preference_cue"],
            "source": "paragraph",
            "entry_type": "note",
        },
        {
            "kind": "entry",
            "title": "Noise",
            "body": "thanks",
            "confidence": 0.5,
            "reasons": [],
            "source": "paragraph",
            "entry_type": "note",
        },
    ]

    async def _fake_system_one(
        state: str,
        questions: dict[str, Any],
        *,
        settings: Settings | None = None,
    ) -> dict[str, Any] | None:
        if "thanks" in state.lower():
            return {
                "kind": {"type": "choice", "choice": "skip", "confidence": 0.9},
                "entry_type": {"type": "choice", "choice": "note", "confidence": 0.5},
                "keep": {"type": "noul", "noul": 0.1},
            }
        return {
            "kind": {"type": "choice", "choice": "entry", "confidence": 0.88},
            "entry_type": {"type": "choice", "choice": "preference", "confidence": 0.7},
            "keep": {"type": "noul", "noul": 0.95},
        }

    with patch(
        "memchentic.services.memory_decision.system_one",
        side_effect=_fake_system_one,
    ):
        out = await memory_decision.rescore_candidates(rows, settings=_settings())
    assert len(out) == 1
    assert out[0]["entry_type"] == "preference"
    assert out[0]["confidence"] == 0.88
    assert "decision_model" in out[0]["reasons"]


@pytest.mark.asyncio
async def test_rescore_http_failure_leaves_rows() -> None:
    rows = [
        {
            "kind": "guide",
            "title": "Policy",
            "body": "When unsure, confirm identity first.",
            "confidence": 0.8,
            "reasons": ["guide_cue"],
            "source": "heading",
        }
    ]
    with patch(
        "memchentic.services.memory_decision.system_one",
        AsyncMock(return_value=None),
    ):
        out = await memory_decision.rescore_candidates(rows, settings=_settings())
    assert out == rows


@pytest.mark.asyncio
async def test_suggest_candidates_applies_decision_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_DECISION_BASE_URL", "https://decision.example.test")
    monkeypatch.setenv("MEMORY_DECISION_API_KEY", "k")
    get_settings.cache_clear()

    async def _fake_rescore(
        rows: list[dict[str, Any]],
        *,
        settings: Settings | None = None,
    ) -> list[dict[str, Any]]:
        assert rows
        return [
            {
                **rows[0],
                "kind": "guide",
                "confidence": 0.91,
                "reasons": [*(rows[0].get("reasons") or []), "decision_model"],
            }
        ]

    with patch(
        "memchentic.services.memory_decision.rescore_candidates",
        side_effect=_fake_rescore,
    ):
        out = await memory_ingest.suggest_candidates(
            "## Policy\n\nWhen identity is unsure, always confirm before changing billing.\n",
            use_llm=False,
        )
    get_settings.cache_clear()
    assert out
    assert out[0]["kind"] == "guide"
    assert "decision_model" in out[0]["reasons"]
