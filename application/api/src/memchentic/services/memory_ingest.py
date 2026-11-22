"""Heuristic (and optional LLM) extraction of memory candidates from text."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from memchentic.models.entities import MemoryKind
from memchentic.settings import get_settings

_HEADING_RE = re.compile(r"^(#{1,6}\s+|\*\*.+\*\*\s*$|[A-Z][^\n]{0,120}:)\s*", re.MULTILINE)


def _strip_heading(line: str) -> str:
    text = line.strip()
    if text.startswith("#"):
        return re.sub(r"^#+\s*", "", text).strip()
    if text.startswith("**") and text.endswith("**"):
        return text.strip("*").strip()
    if text.endswith(":") and len(text) <= 120:
        return text[:-1].strip()
    return text


def suggest_from_text(
    text: str,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Split prose into candidate memory dicts without touching the database.

    Paragraphs split on blank lines. Lines that look like Markdown or plain
    headings start a new candidate with ``kind`` ``guide``; other paragraphs
    become ``entry`` rows with ``entry_type`` ``note``.

    Args:
        text: Source document or transcript.
        messages: Reserved for a future LLM path; ignored by the heuristic.

    Returns:
        Candidate dicts with ``kind``, ``title``, ``body``, and optional
        ``entry_type`` / ``tags``.
    """
    _ = messages
    stripped = text.strip()
    if not stripped:
        return []

    candidates: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", stripped)
    current_title: str | None = None
    body_lines: list[str] = []

    def flush(*, kind: MemoryKind, title: str, body: str) -> None:
        body_clean = body.strip()
        if not title and not body_clean:
            return
        row: dict[str, Any] = {
            "kind": kind.value,
            "title": title or (body_clean.split("\n", 1)[0][:512] if body_clean else "Untitled"),
            "body": body_clean,
        }
        if kind == MemoryKind.entry:
            row["entry_type"] = "note"
        candidates.append(row)

    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        is_heading = bool(_HEADING_RE.match(first)) or (
            first.endswith(":") and len(first) <= 120 and not first.startswith("-")
        )
        if is_heading:
            if body_lines:
                flush(
                    kind=MemoryKind.entry,
                    title=current_title or "Notes",
                    body="\n".join(body_lines),
                )
                body_lines = []
            current_title = _strip_heading(first)
            rest = "\n".join(lines[1:]).strip()
            if rest:
                flush(kind=MemoryKind.guide, title=current_title, body=rest)
                current_title = None
            continue
        if current_title and not body_lines:
            flush(kind=MemoryKind.guide, title=current_title, body="\n".join(lines))
            current_title = None
        else:
            body_lines.extend(lines)

    if body_lines:
        flush(
            kind=MemoryKind.entry,
            title=current_title or "Notes",
            body="\n".join(body_lines),
        )
    return candidates


async def llm_suggest_candidates(
    text: str,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]] | None:
    """Optional OpenAI-compatible chat completion for ingest candidates.

    Returns:
        Parsed candidate list, or ``None`` when ``MEMORY_LLM_BASE_URL`` is unset
        or the request fails.
    """
    settings = get_settings()
    base = settings.memory_llm_base_url.strip()
    if not base:
        return None
    prompt_messages = list(messages or [])
    prompt_messages.append(
        {
            "role": "user",
            "content": (
                "Extract durable memory candidates as JSON array. Each object "
                'has keys kind ("entry"|"guide"), title, body, and optional '
                "entry_type, tags.\n\n"
                f"{text}"
            ),
        }
    )
    url = f"{base.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.memory_llm_api_key.strip():
        headers["Authorization"] = f"Bearer {settings.memory_llm_api_key.strip()}"
    payload = {
        "model": settings.memory_llm_model,
        "messages": prompt_messages,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
    except (httpx.HTTPError, KeyError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


async def suggest_candidates(
    text: str,
    *,
    messages: list[dict[str, str]] | None = None,
    use_llm: bool = False,
) -> list[dict[str, Any]]:
    """Return ingest candidates via LLM when configured, else heuristics."""
    if use_llm:
        llm_rows = await llm_suggest_candidates(text, messages)
        if llm_rows is not None:
            return llm_rows
    return suggest_from_text(text, messages)
