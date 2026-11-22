"""Optional System One decision gate for ingest candidates (Jev / Kev).

Posts to ``POST {base}/v1/systemone`` with typed choice/noul questions. Soft-fails
to the incoming rows on misconfig, HTTP, or parse errors. Never logs API keys.
"""

from __future__ import annotations

from typing import Any

import httpx

from memchentic.models.entities import EntryType, MemoryKind
from memchentic.settings import Settings, get_settings

_KEEP_NOUL_FLOOR = 0.40
_ENTRY_TYPES = {e.value for e in EntryType}
_KINDS = {MemoryKind.entry.value, MemoryKind.guide.value, "skip"}

_CANDIDATE_QUESTIONS: dict[str, dict[str, Any]] = {
    "kind": {
        "type": "choice",
        "instructions": (
            "Classify this memory candidate. Use skip when it is noise, a greeting, "
            "a secret, or not durable enough to store."
        ),
        "criteria": {
            "entry": "A durable fact, preference, decision, or other entry",
            "guide": "Reusable standing advice (when X, do Y)",
            "skip": "Not worth storing",
        },
    },
    "entry_type": {
        "type": "choice",
        "instructions": "If this is an entry, pick the best entry type.",
        "criteria": {
            "fact": "Stable factual statement",
            "preference": "User or team preference",
            "decision": "A decision that was made",
            "instruction": "An instruction or rule to follow",
            "feedback": "Feedback about prior work",
            "insight": "A reusable insight",
            "note": "Catch-all note",
        },
    },
    "keep": {
        "type": "noul",
        "instructions": "Is this a durable memory worth storing?",
    },
}


async def system_one(
    state: str | dict[str, Any],
    questions: dict[str, dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Call System One and return the ``answers`` map, or ``None`` on failure.

    Args:
        state: Text or JSON state evaluated by the decision model.
        questions: Named System One questions (choice / noul / score).
        settings: Injected settings for tests; defaults to ``get_settings()``.
    """
    cfg = settings or get_settings()
    if not cfg.memory_decision_configured():
        return None
    base = cfg.memory_decision_base_url.strip().rstrip("/")
    url = f"{base}/v1/systemone"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = cfg.memory_decision_api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload: dict[str, Any] = {
        "state": state,
        "model": cfg.memory_decision_model.strip() or "kev-latest",
        "questions": questions,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        answers = data.get("answers")
        if not isinstance(answers, dict):
            return None
        return answers
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        return None


def _state_for_row(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    body = str(row.get("body") or "").strip()
    if title and body:
        return f"{title}\n\n{body}"
    return body or title


def _apply_answers(row: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any] | None:
    """Mutate a shaped candidate from System One answers, or return None to drop."""
    kind_raw = answers.get("kind")
    keep_raw = answers.get("keep")
    type_raw = answers.get("entry_type")
    kind_ans: dict[str, Any] = kind_raw if isinstance(kind_raw, dict) else {}
    keep_ans: dict[str, Any] = keep_raw if isinstance(keep_raw, dict) else {}
    type_ans: dict[str, Any] = type_raw if isinstance(type_raw, dict) else {}

    kind_choice = str(kind_ans.get("choice") or "").strip().lower()
    keep_noul = keep_ans.get("noul")
    try:
        keep_prob = float(keep_noul) if keep_noul is not None else 1.0
    except (TypeError, ValueError):
        keep_prob = 1.0

    if kind_choice == "skip" or keep_prob < _KEEP_NOUL_FLOOR:
        return None
    if kind_choice not in _KINDS or kind_choice == "skip":
        # Unknown kind answer: leave row as proposed.
        out = dict(row)
        reasons = list(out.get("reasons") or [])
        if "decision_model" not in reasons:
            reasons.append("decision_model")
        out["reasons"] = reasons[:8]
        return out

    out = dict(row)
    out["kind"] = kind_choice
    if kind_choice == MemoryKind.guide.value:
        out.pop("entry_type", None)
    else:
        entry_choice = str(type_ans.get("choice") or "").strip().lower()
        if entry_choice in _ENTRY_TYPES:
            out["entry_type"] = entry_choice
        elif "entry_type" not in out:
            out["entry_type"] = EntryType.note.value

    conf_raw = kind_ans.get("confidence")
    try:
        conf = float(conf_raw) if conf_raw is not None else keep_prob
    except (TypeError, ValueError):
        conf = keep_prob
    out["confidence"] = round(max(0.0, min(1.0, conf)), 3)

    reasons = list(out.get("reasons") or [])
    if "decision_model" not in reasons:
        reasons.append("decision_model")
    out["reasons"] = reasons[:8]
    return out


async def rescore_candidates(
    rows: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Re-score shaped ingest candidates via System One; soft-fail per row.

    Args:
        rows: Shaped candidate dicts from heuristics or LLM post-process.
        settings: Injected settings for tests.

    Returns:
        Filtered/relabeled candidates. Unchanged when the gate is not configured.
    """
    cfg = settings or get_settings()
    if not cfg.memory_decision_configured() or not rows:
        return rows

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        answers = await system_one(_state_for_row(row), _CANDIDATE_QUESTIONS, settings=cfg)
        if answers is None:
            out.append(row)
            continue
        try:
            scored = _apply_answers(row, answers)
        except (TypeError, ValueError, KeyError):
            out.append(row)
            continue
        if scored is not None:
            out.append(scored)
    return out
