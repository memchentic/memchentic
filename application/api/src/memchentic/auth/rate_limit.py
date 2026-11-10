"""In-process sliding-window rate limiter for auth surfaces."""

from __future__ import annotations

import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

# Upper bound on tracked buckets. When the map is full the least recently
# used bucket is dropped, so memory stays bounded when callers rotate keys.
_MAX_BUCKETS = 10_000

# bucket -> timestamps of recent hits (monotonic seconds); least recently
# used bucket first.
_HITS: OrderedDict[str, deque[float]] = OrderedDict()


def check_rate_limit(bucket: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 when ``bucket`` has exceeded ``limit`` hits in ``window_seconds``.

    Args:
        bucket: Isolation key (e.g. ``login-ip:{ip}``). Keep it short: hash
            any caller-supplied text before putting it in the key.
        limit: Maximum allowed hits inside the window.
        window_seconds: Sliding window length in seconds.

    Raises:
        HTTPException: 429 when the limit is exceeded.
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    hits = _HITS.get(bucket)
    if hits is None:
        hits = deque()
        _HITS[bucket] = hits
        while len(_HITS) > _MAX_BUCKETS:
            _HITS.popitem(last=False)
    else:
        _HITS.move_to_end(bucket)
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= limit:
        raise HTTPException(429, "Too many requests")
    hits.append(now)


def reset_rate_limits() -> None:
    """Clear all buckets (tests only)."""
    _HITS.clear()


def client_ip(request: Request) -> str:
    """Client address for per-client rate-limit buckets.

    ``request.client`` is the TCP peer unless uvicorn trusts that peer to set
    ``X-Forwarded-For`` (environment variable ``FORWARDED_ALLOW_IPS``). Never
    read ``X-Forwarded-For`` here: a client can send any value in it.

    Args:
        request: Incoming request.

    Returns:
        The client host, or ``"unknown"`` when the server did not provide one.
    """
    return request.client.host if request.client else "unknown"
