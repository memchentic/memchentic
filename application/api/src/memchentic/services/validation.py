"""Shared input parsing for REST routes and MCP tools.

Every function raises ``ValidationError`` (HTTP 422, MCP prefix ``invalid:``)
and names the offending parameter in the message.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from memchentic.models.entities import SpaceKind
from memchentic.services import limits
from memchentic.services.errors import ValidationError


def parse_uuid(value: str | None, field: str) -> uuid.UUID | None:
    """Parse an optional id.

    Args:
        value: Textual UUID. ``None`` and blank strings mean "not supplied".
        field: Parameter name used in the error message.

    Returns:
        The parsed UUID, or ``None`` when no value was supplied.

    Raises:
        ValidationError: When the value is not a UUID.
    """
    if value is None or not str(value).strip():
        return None
    try:
        return uuid.UUID(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def require_uuid(value: str | None, field: str) -> uuid.UUID:
    """Parse a required id.

    Args:
        value: Textual UUID.
        field: Parameter name used in the error message.

    Returns:
        The parsed UUID.

    Raises:
        ValidationError: When the value is missing or not a UUID.
    """
    parsed = parse_uuid(value, field)
    if parsed is None:
        raise ValidationError(f"{field} is required")
    return parsed


def parse_datetime(value: str | None, field: str) -> datetime | None:
    """Parse an optional ISO 8601 datetime; a value without an offset is UTC.

    Args:
        value: ISO 8601 text. ``None`` and blank strings mean "not supplied".
        field: Parameter name used in the error message.

    Returns:
        A timezone-aware datetime, or ``None`` when no value was supplied.

    Raises:
        ValidationError: When the value is not ISO 8601.
    """
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValidationError(
            f"{field} must be an ISO 8601 datetime, for example 2031-01-01T00:00:00+00:00"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_space_scope(value: str) -> SpaceKind:
    """Parse a ``space_scope`` filter.

    Args:
        value: ``personal`` or ``group``.

    Returns:
        The matching ``SpaceKind``.

    Raises:
        ValidationError: When the value is neither ``personal`` nor ``group``.
    """
    try:
        return SpaceKind(value)
    except ValueError as exc:
        raise ValidationError("space_scope must be 'personal' or 'group'") from exc


def check_text(value: str, field: str, max_len: int) -> str:
    """Reject text the database cannot store.

    Args:
        value: Caller-supplied text.
        field: Parameter name used in the error message.
        max_len: Maximum length in characters.

    Returns:
        The value, unchanged.

    Raises:
        ValidationError: When the value contains a NUL character or is longer
            than ``max_len``.
    """
    if "\x00" in value:
        raise ValidationError(f"{field} must not contain NUL characters")
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    return value


def check_optional_text(value: str | None, field: str, max_len: int) -> str | None:
    """Apply :func:`check_text` when a value was supplied.

    Args:
        value: Caller-supplied text, or ``None``.
        field: Parameter name used in the error message.
        max_len: Maximum length in characters.

    Returns:
        The value, unchanged.

    Raises:
        ValidationError: See :func:`check_text`.
    """
    return None if value is None else check_text(value, field, max_len)


def check_title(title: str) -> str:
    """Trim a memory title; require 1 to ``limits.TITLE_MAX`` characters.

    Args:
        title: Caller-supplied title.

    Returns:
        The trimmed title.

    Raises:
        ValidationError: When the title is empty after trimming or too long.
    """
    cleaned = check_text(title.strip(), "title", limits.TITLE_MAX)
    if not cleaned:
        raise ValidationError("title must not be empty")
    return cleaned


def check_body(body: str) -> str:
    """Require a body with visible content and at most ``limits.BODY_MAX`` characters.

    The body is returned unchanged; leading and trailing whitespace is kept.

    Args:
        body: Caller-supplied body.

    Returns:
        The body, unchanged.

    Raises:
        ValidationError: When the body is empty, whitespace only, or too long.
    """
    check_text(body, "body", limits.BODY_MAX)
    if not body.strip():
        raise ValidationError("body must not be empty")
    return body


def normalise_tags(tags: Sequence[str]) -> list[str]:
    """Trim and lower-case tags, drop empty ones, and remove duplicates.

    Args:
        tags: Tags as supplied.

    Returns:
        Normalised tags in first-seen order.
    """
    seen: dict[str, None] = {}
    for tag in tags:
        cleaned = tag.strip().lower()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def check_tags(tags: Sequence[str] | None) -> list[str]:
    """Enforce the tag bounds, then normalise.

    Args:
        tags: Caller-supplied tags; ``None`` means no tags.

    Returns:
        Normalised tags (see :func:`normalise_tags`).

    Raises:
        ValidationError: When there are more than ``limits.TAG_MAX_COUNT``
            tags or one tag is longer than ``limits.TAG_MAX_LEN``.
    """
    items = list(tags or [])
    if len(items) > limits.TAG_MAX_COUNT:
        raise ValidationError(f"tags: at most {limits.TAG_MAX_COUNT} tags are allowed")
    return normalise_tags([check_text(tag, "tag", limits.TAG_MAX_LEN) for tag in items])


def check_name(name: str, field: str, max_len: int) -> str:
    """Trim a space name; require 1 to ``max_len`` characters.

    Args:
        name: Caller-supplied name.
        field: Parameter name used in the error message.
        max_len: Maximum length in characters.

    Returns:
        The trimmed name.

    Raises:
        ValidationError: When the name is empty after trimming or too long.
    """
    cleaned = check_text(name.strip(), field, max_len)
    if not cleaned:
        raise ValidationError(f"{field} must not be empty")
    return cleaned


def check_query(query: str) -> str:
    """Enforce ``limits.QUERY_MAX`` on a search query.

    Args:
        query: Caller-supplied query text.

    Returns:
        The query, unchanged.

    Raises:
        ValidationError: When the query is too long or contains NUL.
    """
    return check_text(query, "query", limits.QUERY_MAX)


def check_ttl_days(ttl_days: int | None) -> int | None:
    """Enforce ``0 <= ttl_days <= limits.TTL_DAYS_MAX``.

    Args:
        ttl_days: Relative lifetime in days, or ``None``.

    Returns:
        The value, unchanged.

    Raises:
        ValidationError: When ``ttl_days`` is outside the range.
    """
    if ttl_days is not None and not 0 <= ttl_days <= limits.TTL_DAYS_MAX:
        raise ValidationError(f"ttl_days must be between 0 and {limits.TTL_DAYS_MAX}")
    return ttl_days


def check_offset(offset: int) -> int:
    """Enforce ``0 <= offset <= limits.OFFSET_MAX``.

    Args:
        offset: Pagination offset.

    Returns:
        The value, unchanged.

    Raises:
        ValidationError: When ``offset`` is outside the range.
    """
    if not 0 <= offset <= limits.OFFSET_MAX:
        raise ValidationError(f"offset must be between 0 and {limits.OFFSET_MAX}")
    return offset
