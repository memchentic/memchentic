"""Optional multi-provider chat client for memory ingest (and future callers).

Supports OpenAI-compatible ``/chat/completions`` (OpenAI, OpenRouter, local
gateways), Anthropic Messages, and Google Gemini ``generateContent``. Returns
parsed JSON arrays or ``None`` on misconfig / HTTP / parse failure. Never logs
API keys.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from memchentic.settings import Settings, get_settings

_DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
_DEFAULT_GOOGLE_BASE = "https://generativelanguage.googleapis.com"
_ANTHROPIC_VERSION = "2023-06-01"


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_array(content: str) -> list[dict[str, Any]] | None:
    try:
        parsed = json.loads(_strip_json_fences(content))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [item for item in parsed if isinstance(item, dict)]


def _resolved_base(settings: Settings) -> str:
    base = settings.memory_llm_base_url.strip().rstrip("/")
    if base:
        return base
    if settings.memory_llm_provider == "anthropic":
        return _DEFAULT_ANTHROPIC_BASE
    if settings.memory_llm_provider == "google":
        return _DEFAULT_GOOGLE_BASE
    return ""


async def _openai_complete(
    settings: Settings,
    messages: list[dict[str, str]],
) -> str | None:
    base = _resolved_base(settings)
    if not base:
        return None
    url = f"{base}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = settings.memory_llm_api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": settings.memory_llm_model,
        "messages": messages,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"]["content"])


async def _anthropic_complete(
    settings: Settings,
    messages: list[dict[str, str]],
) -> str | None:
    key = settings.memory_llm_api_key.strip()
    if not key:
        return None
    base = _resolved_base(settings)
    url = f"{base}/v1/messages"
    system_parts: list[str] = []
    api_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_parts.append(content)
            continue
        mapped = "assistant" if role == "assistant" else "user"
        api_messages.append({"role": mapped, "content": content})
    if not api_messages:
        return None
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    payload: dict[str, Any] = {
        "model": settings.memory_llm_model,
        "max_tokens": 4096,
        "temperature": 0.2,
        "messages": api_messages,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    blocks = data.get("content") or []
    texts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    joined = "\n".join(t for t in texts if t).strip()
    return joined or None


async def _google_complete(
    settings: Settings,
    messages: list[dict[str, str]],
) -> str | None:
    key = settings.memory_llm_api_key.strip()
    if not key:
        return None
    base = _resolved_base(settings)
    model = settings.memory_llm_model.strip() or "gemini-2.0-flash"
    url = f"{base}/v1beta/models/{model}:generateContent"
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_parts.append(content)
            continue
        # Gemini: user → user, assistant → model
        gem_role = "model" if role == "assistant" else "user"
        contents.append({"role": gem_role, "parts": [{"text": content}]})
    if not contents:
        return None
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": 0.2},
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
    joined = "\n".join(t for t in texts if t).strip()
    return joined or None


async def complete_json_array(
    prompt: str,
    *,
    messages: list[dict[str, str]] | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]] | None:
    """Call the configured provider and parse a JSON array of objects.

    Args:
        prompt: Final user prompt (appended after optional prior messages).
        messages: Optional prior role/content turns.
        settings: Injected settings for tests; defaults to ``get_settings()``.

    Returns:
        List of dicts, or ``None`` when the LLM path is not configured or fails.
    """
    cfg = settings or get_settings()
    if not cfg.memory_llm_configured():
        return None
    chat: list[dict[str, str]] = list(messages or [])
    chat.append({"role": "user", "content": prompt})
    provider = cfg.memory_llm_provider
    try:
        if provider == "openai":
            content = await _openai_complete(cfg, chat)
        elif provider == "anthropic":
            content = await _anthropic_complete(cfg, chat)
        elif provider == "google":
            content = await _google_complete(cfg, chat)
        else:
            return None
        if not content:
            return None
        return _parse_json_array(content)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
        return None
