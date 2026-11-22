"""Heuristic (and optional LLM) extraction of memory candidates from text.

Pipeline: normalize → segment → classify → filter (noise/secrets/merge/atomic)
→ shape. Both heuristic and LLM paths share the post-process gate. Suggest
never writes to the database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from memchentic.models.entities import EntryType, MemoryKind
from memchentic.services import limits
from memchentic.services.errors import ValidationError
from memchentic.settings import get_settings

# --- constants (module knobs; not env sprawl) ---------------------------------

MAX_MESSAGES = 200
MAX_SEGMENTS = 40
MAX_CANDIDATES = 25
MIN_BODY_CHARS = 40
MIN_ONE_LINER_CHARS = 8
ATOMIC_SPLIT_CHARS = 900
TITLE_SOFT_MAX = 80
RAW_SEGMENT_CAP = 40

_ENTRY_TYPES = {e.value for e in EntryType}
_KINDS = {k.value for k in MemoryKind}

# --- regex libraries ---------------------------------------------------------

_BOM_ZW = re.compile(r"[\ufeff\u200b\u200c\u200d\ufeff]")
_MULTI_BLANK = re.compile(r"\n{3,}")
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_BOLD_TITLE = re.compile(r"^\*\*(.+?)\*\*\s*$")
_LABEL_LINE = re.compile(r"^([A-Z][\w][\w \-/]{0,78}):\s*$")
_CHAT_LINE = re.compile(
    r"^(?:\[\s*)?(User|Assistant|System|Human|Agent|Support|Customer|Operator|"
    r"Admin|Dev|Bot)(?:\s*\])?\s*:\s*(.*)$",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^```")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_PATH_HINT = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?(?:\.{0,2}/)?(?:[\w.-]+/){1,}\w[\w./-]*\.\w{1,12})"
)
_REPO_HINT = re.compile(r"\b(?P<repo>[\w-]+/[\w.-]+)\b")
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"(?i)\b(password|passwd|api[_-]?key|secret|token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)authorization:\s*bearer\s+\S+"),
    re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b"),  # long base64-ish
]
_NOISE_EXACT = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "ty",
    "lgtm",
    "np",
    "cool",
    "sure",
    "yep",
    "yeah",
    "hi",
    "hello",
    "hey",
}
_NOISE_RE = re.compile(
    r"(?i)^(thanks[!.,]*|thank you[!.,]*|as an ai\b|i('m| am) (just )?an ai\b|"
    r"sent from my (iphone|ipad|android)|get outlook for)\b"
)
_PREF_RE = re.compile(
    r"(?i)\b(prefer|preference|don'?t want|do not want|always want|please (always|never))\b"
)
_DECISION_RE = re.compile(
    r"(?i)\b(we (decided|chose|picked)|decided to|going with|decision:|shipped as)\b"
)
_INSTRUCTION_RE = re.compile(
    r"(?i)\b(always|never|must not|do not|don't|must)\b.{0,80}\b"
    r"(commit|push|store|log|reveal|run|call|use|ask)\b"
)
_GUIDE_RE = re.compile(
    r"(?i)\b("
    r"when [^.\n]{5,80} (do|run|use|avoid|prefer)\b|"
    r"if [^.\n]{5,80} then\b|"
    r"runbook|playbook|escalation policy|on-call"
    r")"
)
_FACT_RE = re.compile(
    r"(?i)\b(runs? on|listens? on|published? (on|at)|port \d{2,5}|base_url|"
    r"defaults? to|is set to)\b"
)
_INSIGHT_RE = re.compile(r"(?i)\b(lesson|root cause|turns out|non-obvious|gotcha|pitfall)\b")
_FEEDBACK_RE = re.compile(
    r"(?i)\b(instead of|that was wrong|correction:|do not .+ anymore|"
    r"stop (doing|using))\b"
)
_GENERIC_TITLES = {"notes", "untitled", "note", "summary", "text", "chat"}


@dataclass
class Segment:
    """Raw text unit before classification."""

    title: str | None
    body: str
    source: str
    role: str | None = None


@dataclass
class Candidate:
    """Scored memory draft before shaping to a response dict."""

    kind: str
    title: str
    body: str
    entry_type: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)
    source: str = "paragraph"
    path_hint: str | None = None
    repo_hint: str | None = None


# --- normalize / messages ----------------------------------------------------


def _normalize_text(text: str) -> str:
    cleaned = _BOM_ZW.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = _MULTI_BLANK.sub("\n\n", cleaned)
    lines = []
    for line in cleaned.split("\n"):
        # Strip light email quoting when the line is a pure quote prefix.
        if line.startswith(">"):
            line = re.sub(r"^(?:>\s*)+", "", line)
        lines.append(line.rstrip())
    text_out = "\n".join(lines).strip()
    # Drop common signature starters and everything after.
    for marker in ("\n-- \n", "\nSent from my ", "\nGet Outlook for "):
        idx = text_out.find(marker)
        if idx != -1:
            text_out = text_out[:idx].rstrip()
    return text_out


def _validate_messages(
    messages: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    if not messages:
        return []
    if len(messages) > MAX_MESSAGES:
        raise ValidationError(f"messages must have at most {MAX_MESSAGES} items")
    out: list[dict[str, str]] = []
    total = 0
    for i, raw in enumerate(messages):
        if not isinstance(raw, dict):
            raise ValidationError(f"messages[{i}] must be an object")
        role = raw.get("role")
        content = raw.get("content")
        if not isinstance(role, str) or not role.strip():
            raise ValidationError(f"messages[{i}].role must be a non-empty string")
        if not isinstance(content, str):
            raise ValidationError(f"messages[{i}].content must be a string")
        total += len(content)
        if total > limits.BODY_MAX:
            raise ValidationError("messages content exceeds maximum size")
        out.append({"role": role.strip(), "content": content})
    return out


def _transcript_from_messages(messages: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"{m['role']}: {m['content'].strip()}" for m in messages if m["content"].strip()
    )


# --- segment -----------------------------------------------------------------


def _carve_fences(text: str) -> list[tuple[str, str]]:
    """Split into (kind, text) where kind is 'prose' or 'fence'."""
    parts: list[tuple[str, str]] = []
    lines = text.split("\n")
    i = 0
    buf: list[str] = []
    while i < len(lines):
        if _FENCE.match(lines[i].strip()):
            if buf:
                parts.append(("prose", "\n".join(buf)))
                buf = []
            fence_lines = [lines[i]]
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i].strip()):
                fence_lines.append(lines[i])
                i += 1
            if i < len(lines):
                fence_lines.append(lines[i])
                i += 1
            parts.append(("fence", "\n".join(fence_lines)))
            continue
        buf.append(lines[i])
        i += 1
    if buf:
        parts.append(("prose", "\n".join(buf)))
    return parts


def _is_heading_line(line: str) -> tuple[bool, str | None]:
    s = line.strip()
    m = _ATX_HEADING.match(s)
    if m:
        return True, m.group(2).strip()
    m = _BOLD_TITLE.match(s)
    if m:
        return True, m.group(1).strip()
    m = _LABEL_LINE.match(s)
    if m:
        label = m.group(1).strip()
        # Avoid mid-prose false positives: require short title-like label.
        if len(label.split()) <= 8 and not label.endswith((".", "!", "?")):
            return True, label
    return False, None


def _segment_prose(text: str) -> list[Segment]:
    text = text.strip()
    if not text:
        return []

    # Prefer chat-turn mode when enough speaker lines exist.
    lines = list(text.split("\n"))
    chat_hits = sum(1 for ln in lines if _CHAT_LINE.match(ln.strip()))
    if chat_hits >= 2:
        return _segment_chat(lines)

    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text)
    current_title: str | None = None
    body_lines: list[str] = []

    def flush_entry() -> None:
        nonlocal body_lines
        body = "\n".join(body_lines).strip()
        if not body:
            body_lines = []
            return
        # List cluster detection
        list_lines = [ln for ln in body.split("\n") if _LIST_ITEM.match(ln.strip())]
        if len(list_lines) >= 2 and len(list_lines) >= max(1, len(body.split("\n")) // 2):
            segments.append(
                Segment(title=current_title, body=body, source="list"),
            )
        else:
            segments.append(
                Segment(title=current_title, body=body, source="paragraph"),
            )
        body_lines = []

    for block in blocks:
        blines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not blines:
            continue
        first = blines[0]
        is_h, title = _is_heading_line(first)
        if is_h and title is not None:
            if body_lines:
                flush_entry()
            current_title = title
            rest = "\n".join(blines[1:]).strip()
            if rest:
                segments.append(Segment(title=current_title, body=rest, source="heading"))
                current_title = None
            continue
        if current_title and not body_lines:
            segments.append(
                Segment(title=current_title, body="\n".join(blines), source="heading"),
            )
            current_title = None
        else:
            body_lines.extend(blines)

    if body_lines:
        flush_entry()
    return segments[:RAW_SEGMENT_CAP]


def _segment_chat(lines: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    role: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, role
        body = "\n".join(buf).strip()
        if body:
            segments.append(
                Segment(
                    title=None,
                    body=body,
                    source="turn",
                    role=role,
                ),
            )
        buf = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _CHAT_LINE.match(line.strip())
        if m:
            flush()
            role = m.group(1)
            rest = m.group(2).strip()
            buf = [rest] if rest else []
            continue
        buf.append(line.strip())
    flush()
    return segments[:RAW_SEGMENT_CAP]


def _segment_fence(fence_text: str) -> list[Segment]:
    inner = fence_text.strip()
    if inner.startswith("```"):
        inner_lines = inner.split("\n")
        # drop opening/closing fence lines
        if inner_lines and _FENCE.match(inner_lines[0].strip()):
            inner_lines = inner_lines[1:]
        if inner_lines and _FENCE.match(inner_lines[-1].strip()):
            inner_lines = inner_lines[:-1]
        inner = "\n".join(inner_lines).strip()
    if not inner:
        return []
    # KV-ish fences may be durable; pure code usually dropped later.
    return [Segment(title=None, body=inner, source="code")]


def _try_kv_segments(text: str) -> list[Segment] | None:
    stripped = text.strip()
    if not stripped:
        return None
    # JSON object
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data:
            segs: list[Segment] = []
            for key, val in list(data.items())[:MAX_SEGMENTS]:
                if isinstance(val, (dict, list)):
                    continue
                segs.append(
                    Segment(
                        title=str(key),
                        body=f"{key}: {val}",
                        source="kv",
                    ),
                )
            return segs or None
    # Simple key: value lines (majority)
    lines = [ln.strip() for ln in stripped.split("\n") if ln.strip()]
    kv_lines = [
        ln for ln in lines if re.match(r"^[\w.-]+\s*:\s*.+$", ln) and not ln.startswith("#")
    ]
    if len(lines) >= 2 and len(kv_lines) >= max(2, int(0.7 * len(lines))):
        return [
            Segment(title=ln.split(":", 1)[0].strip(), body=ln, source="kv") for ln in kv_lines
        ][:MAX_SEGMENTS]
    return None


def segment_text(text: str, messages: list[dict[str, str]] | None = None) -> list[Segment]:
    """Split normalized input into segments."""
    msgs = _validate_messages(messages)
    if msgs:
        # Turn-native segmentation from structured messages.
        segs = [
            Segment(
                title=None,
                body=m["content"].strip(),
                source="turn",
                role=m["role"],
            )
            for m in msgs
            if m["content"].strip()
        ]
        return segs[:RAW_SEGMENT_CAP]

    normalized = _normalize_text(text)
    if not normalized:
        return []

    kv = _try_kv_segments(normalized)
    if kv is not None:
        return kv

    segments: list[Segment] = []
    for kind, chunk in _carve_fences(normalized):
        if kind == "fence":
            segments.extend(_segment_fence(chunk))
        else:
            segments.extend(_segment_prose(chunk))
    return segments[:RAW_SEGMENT_CAP]


# --- classify ----------------------------------------------------------------


def _looks_like_guide(title: str | None, body: str) -> bool:
    blob = f"{title or ''}\n{body}"
    if _GUIDE_RE.search(blob):
        return True
    if title and re.search(
        r"(?i)\b(policy|runbook|playbook|escalation|rollback|backup|restore)\b",
        title,
    ):
        return True
    # Headed how-to: imperative opener under a section title.
    imperative = r"(?i)^(run|if|when|restore|keep|use|avoid|do not|don't|always|never)\b"
    return bool(title and re.match(imperative, body.strip()))


def classify_segment(seg: Segment) -> Candidate:
    """Assign kind, entry_type, confidence, and reasons."""
    body = seg.body.strip()
    title = (seg.title or "").strip() or None
    reasons: list[str] = []
    confidence = 0.45
    tags: list[str] = []

    if title:
        low = title.lower()
        for tag in ("billing", "ops", "escalation", "security", "onboarding"):
            if tag in low:
                tags.append(tag)
                reasons.append(f"tag:{tag}")

    if _looks_like_guide(title, body):
        reasons.append("guide_cues")
        confidence = 0.82
        path_hint = _extract_path_hint(body)
        repo_hint = _extract_repo_hint(body)
        if path_hint:
            reasons.append("path_hint")
        if repo_hint:
            reasons.append("repo_hint")
        return Candidate(
            kind=MemoryKind.guide.value,
            title=_good_title(title, body),
            body=body,
            confidence=confidence,
            reasons=reasons,
            source=seg.source,
            tags=tags,
            path_hint=path_hint,
            repo_hint=repo_hint,
        )

    entry_type = EntryType.note.value
    if _PREF_RE.search(body) or (title and _PREF_RE.search(title)):
        entry_type = EntryType.preference.value
        reasons.append("preference_cues")
        confidence = 0.8
    elif _DECISION_RE.search(body):
        entry_type = EntryType.decision.value
        reasons.append("decision_cues")
        confidence = 0.78
    elif _INSTRUCTION_RE.search(body) and not _GUIDE_RE.search(body):
        entry_type = EntryType.instruction.value
        reasons.append("instruction_cues")
        confidence = 0.8
    elif _FEEDBACK_RE.search(body):
        entry_type = EntryType.feedback.value
        reasons.append("feedback_cues")
        confidence = 0.75
    elif _INSIGHT_RE.search(body):
        entry_type = EntryType.insight.value
        reasons.append("insight_cues")
        confidence = 0.72
    elif _FACT_RE.search(body) or seg.source == "kv":
        entry_type = EntryType.fact.value
        reasons.append("fact_cues")
        confidence = 0.7 if seg.source != "kv" else 0.75
    else:
        reasons.append("default_note")
        if seg.source == "heading":
            confidence = 0.55
        elif seg.source == "turn":
            confidence = 0.4
        else:
            confidence = 0.42

    if seg.source == "heading":
        confidence = min(1.0, confidence + 0.05)
        reasons.append("headed_section")

    path_hint = _extract_path_hint(body)
    repo_hint = _extract_repo_hint(body)
    if path_hint:
        reasons.append("path_hint")
    if repo_hint:
        reasons.append("repo_hint")

    return Candidate(
        kind=MemoryKind.entry.value,
        title=_good_title(title, body),
        body=body,
        entry_type=entry_type,
        tags=tags,
        confidence=confidence,
        reasons=reasons,
        source=seg.source,
        path_hint=path_hint,
        repo_hint=repo_hint,
    )


def _good_title(title: str | None, body: str) -> str:
    if title:
        t = title.strip()
        if t.lower() not in _GENERIC_TITLES and len(t) <= limits.TITLE_MAX:
            return t[: limits.TITLE_MAX]
    first = body.strip().split("\n", 1)[0].strip()
    # Prefer first clause.
    clause = re.split(r"[.!?\n]", first, maxsplit=1)[0].strip() or first
    if len(clause) > TITLE_SOFT_MAX:
        clause = clause[: TITLE_SOFT_MAX - 1].rstrip() + "…"
    if not clause or clause.lower() in _GENERIC_TITLES:
        clause = "Memory candidate"
    return clause[: limits.TITLE_MAX]


def _extract_path_hint(body: str) -> str | None:
    m = _PATH_HINT.search(body)
    if not m:
        return None
    path = m.group("path")
    if len(path) > limits.PATH_HINT_MAX:
        return None
    return path


def _extract_repo_hint(body: str) -> str | None:
    m = _REPO_HINT.search(body)
    if not m:
        return None
    repo = m.group("repo")
    # Avoid matching ordinary paths already captured; require no extension.
    if "." in repo.split("/")[-1]:
        return None
    if len(repo) > limits.REPO_HINT_MAX:
        return None
    return repo


# --- filter / secrets / atomic -----------------------------------------------


def _secret_like(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _is_noise(body: str) -> bool:
    s = body.strip()
    if not s:
        return True
    low = s.lower()
    if low in _NOISE_EXACT:
        return True
    if _NOISE_RE.search(s):
        return True
    return bool(len(s) <= 3 and not s.isalnum())


def _should_keep(c: Candidate) -> bool:
    if _secret_like(c.body) or _secret_like(c.title):
        return False
    if _is_noise(c.body):
        return False
    # Pure code fences without KV shape: drop.
    if (
        c.source == "code"
        and c.entry_type
        not in {
            EntryType.fact.value,
            EntryType.preference.value,
            EntryType.instruction.value,
        }
        and not re.search(r"(?m)^[\w.-]+\s*[:=]\s*\S+", c.body)
    ):
        return False
    n = len(c.body.strip())
    if n < MIN_BODY_CHARS:
        if c.kind == MemoryKind.guide.value and n >= MIN_ONE_LINER_CHARS:
            return True
        if (
            c.entry_type
            in {
                EntryType.preference.value,
                EntryType.instruction.value,
                EntryType.decision.value,
                EntryType.fact.value,
            }
            and n >= MIN_ONE_LINER_CHARS
        ):
            return True
        return bool(c.source in {"kv", "atomic"} and n >= MIN_ONE_LINER_CHARS)
    return True


def _norm_dup_key(c: Candidate) -> str:
    return re.sub(r"\s+", " ", f"{c.kind}|{c.title}|{c.body}".lower()).strip()


def _atomic_split(c: Candidate) -> list[Candidate]:
    if len(c.body) < ATOMIC_SPLIT_CHARS or c.kind == MemoryKind.guide.value:
        return [c]
    if c.source in {"list", "kv", "code"}:
        return [c]
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(c.body) if s.strip()]
    if len(sentences) < 3:
        return [c]
    out: list[Candidate] = []
    for sent in sentences:
        seg = Segment(title=None, body=sent, source="atomic")
        child = classify_segment(seg)
        child.source = "atomic"
        child.reasons = [*child.reasons, "atomic_split"]
        # Keep only cue-bearing atoms from a large blob.
        keep_atom = child.entry_type != EntryType.note.value or _looks_like_guide(None, sent)
        if not keep_atom:
            continue
        # Reclassify oversize guide sentences as entries unless clearly playbook.
        if child.kind == MemoryKind.guide.value and not _GUIDE_RE.search(sent):
            child.kind = MemoryKind.entry.value
            if child.entry_type is None:
                child.entry_type = EntryType.note.value
        out.append(child)
    return out or [c]


def filter_candidates(raw: list[Candidate]) -> list[Candidate]:
    """Drop noise/secrets, atomic-split, dedupe, rank, cap."""
    expanded: list[Candidate] = []
    for c in raw:
        expanded.extend(_atomic_split(c))

    kept = [c for c in expanded if _should_keep(c)]

    # Merge tiny same-title orphans already handled by keep rules; dedupe next.
    seen: dict[str, Candidate] = {}
    for c in kept:
        key = _norm_dup_key(c)
        prev = seen.get(key)
        if prev is None or c.confidence > prev.confidence:
            seen[key] = c
    unique = list(seen.values())
    unique.sort(key=lambda c: (-c.confidence, c.title.lower(), c.body.lower()))
    return unique[:MAX_CANDIDATES]


def shape_candidate(c: Candidate) -> dict[str, Any]:
    """Public response dict (confirm-compatible + triage fields)."""
    row: dict[str, Any] = {
        "kind": c.kind,
        "title": c.title[: limits.TITLE_MAX],
        "body": c.body[: limits.BODY_MAX],
        "confidence": round(max(0.0, min(1.0, c.confidence)), 3),
        "reasons": c.reasons[:8],
        "source": c.source,
    }
    if c.kind == MemoryKind.entry.value:
        row["entry_type"] = c.entry_type or EntryType.note.value
    if c.tags:
        row["tags"] = c.tags[:8]
    if c.path_hint:
        row["path_hint"] = c.path_hint
    if c.repo_hint:
        row["repo_hint"] = c.repo_hint
    return row


def _normalize_llm_row(raw: dict[str, Any]) -> Candidate | None:
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in _KINDS:
        kind = MemoryKind.entry.value
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not body and not title:
        return None
    # Re-run heuristic classify on the draft so bad model kinds/types still get cues.
    seeded = classify_segment(
        Segment(title=title or None, body=body or title, source="paragraph"),
    )
    seeded.reasons = ["llm", *seeded.reasons]
    seeded.confidence = max(0.55, seeded.confidence)
    honor_guide = (
        kind == MemoryKind.guide.value
        and seeded.kind == MemoryKind.entry.value
        and _looks_like_guide(title or None, body or title)
    )
    if honor_guide:
        seeded.kind = MemoryKind.guide.value
        seeded.entry_type = None
    tags_raw = raw.get("tags") or []
    if isinstance(tags_raw, list):
        extra = [str(t).strip() for t in tags_raw if str(t).strip()][:8]
        seeded.tags = list(dict.fromkeys([*seeded.tags, *extra]))[:8]
    if raw.get("path_hint") and not seeded.path_hint:
        seeded.path_hint = str(raw["path_hint"])[: limits.PATH_HINT_MAX]
    if raw.get("repo_hint") and not seeded.repo_hint:
        seeded.repo_hint = str(raw["repo_hint"])[: limits.REPO_HINT_MAX]
    return seeded


def post_process_candidates(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """Shared gate: filter + shape."""
    return [shape_candidate(c) for c in filter_candidates(candidates)]


# --- public API --------------------------------------------------------------


def suggest_from_text(
    text: str,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Split prose into candidate memory dicts without touching the database.

    Args:
        text: Source document or transcript.
        messages: Optional role/content turns; when set, preferred over ``text``.

    Returns:
        Candidate dicts with kind, title, body, triage fields, and optional
        entry_type / tags / path_hint / repo_hint.
    """
    segments = segment_text(text, messages)
    classified = [classify_segment(s) for s in segments]
    return post_process_candidates(classified)


async def llm_suggest_candidates(
    text: str,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]] | None:
    """Optional multi-provider chat completion for ingest candidates.

    Returns:
        Post-filtered candidate list, or ``None`` when the LLM path is not
        configured or the request fails. Providers: openai-compatible,
        anthropic, google (see ``MEMORY_LLM_PROVIDER``).
    """
    from memchentic.services.memory_llm import complete_json_array

    if not get_settings().memory_llm_configured():
        return None
    try:
        msgs = _validate_messages(messages)
    except ValidationError:
        return None
    payload_text = text if not msgs else _transcript_from_messages(msgs)
    prompt = (
        "Extract durable memory candidates as JSON array. Each object "
        'has keys kind ("entry"|"guide"), title, body, and optional '
        "entry_type, tags.\n\n"
        f"{payload_text}"
    )
    parsed = await complete_json_array(prompt, messages=msgs or None)
    if not parsed:
        return None
    normalized: list[Candidate] = []
    for item in parsed:
        row = _normalize_llm_row(item)
        if row is not None:
            normalized.append(row)
    if not normalized:
        return None
    return post_process_candidates(normalized)


async def suggest_candidates(
    text: str,
    *,
    messages: list[dict[str, str]] | None = None,
    use_llm: bool = False,
) -> list[dict[str, Any]]:
    """Return ingest candidates via LLM when configured, else heuristics.

    When ``MEMORY_DECISION_BASE_URL`` is set, shaped candidates are re-scored
    by the System One decision gate (Jev / Kev) before return.
    """
    rows: list[dict[str, Any]]
    if use_llm:
        llm_rows = await llm_suggest_candidates(text, messages)
        rows = llm_rows if llm_rows is not None else suggest_from_text(text, messages)
    else:
        rows = suggest_from_text(text, messages)

    if get_settings().memory_decision_configured():
        from memchentic.services.memory_decision import rescore_candidates

        rows = await rescore_candidates(rows)
    return rows
