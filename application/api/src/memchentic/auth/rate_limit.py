"""In-process sliding-window rate limiter for auth surfaces."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException

# bucket -> timestamps of recent hits (monotonic seconds)
_HITS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(bucket: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 when ``bucket`` has exceeded ``limit`` hits in ``window_seconds``.

    Args:
        bucket: Isolation key (e.g. ``login:{ip}:{email}``).
        limit: Maximum allowed hits inside the window.
        window_seconds: Sliding window length in seconds.

    Raises:
        HTTPException: 429 when the limit is exceeded.
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    hits = _HITS[bucket]
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= limit:
        raise HTTPException(429, "Too many requests")
    hits.append(now)


def reset_rate_limits() -> None:
    """Clear all buckets (tests only)."""
    _HITS.clear()
