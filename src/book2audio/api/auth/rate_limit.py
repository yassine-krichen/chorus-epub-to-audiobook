"""In-memory sliding-window rate limiting for login/refresh.

No new dependency — this codebase already hand-rolls REST clients elsewhere
instead of reaching for SDKs for modest needs (see ``images/freellmapi.py``);
a hand-rolled in-process limiter follows the same precedent. This is
per-process state: fine for a single Render worker, and reset between tests
via :func:`reset_all`.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    """Tracks attempt timestamps per key over a rolling window."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # FastAPI dispatches sync path operations to a threadpool, so hit()
        # can run concurrently across threads. A single instance-level lock
        # makes the read-modify-write below atomic explicitly, rather than
        # relying on the GIL serializing the individual deque/dict ops --
        # this is a lightweight in-memory check, not a hot path that needs
        # finer-grained (e.g. per-key) locking.
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record an attempt for ``key``. Returns False if now over the limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._evict_stale_keys(cutoff)
            bucket = self._hits[key]
            bucket.append(now)
            return len(bucket) <= self.max_attempts

    def _evict_stale_keys(self, cutoff: float) -> None:
        """Prune every key's bucket down to timestamps still inside the
        window, then drop dict entries that are now empty.

        ``key`` on e.g. /auth/login is attacker-controlled (any syntactically
        valid string, checked before any DB lookup) and typically hit only
        once per distinct value during an attack. Pruning only the current
        key's bucket -- as the old code did -- never shrinks self._hits for
        keys that go dormant or are never reused, so it grows without bound.
        Sweeping every key on each call instead bounds memory to "keys
        active within the last window", not "every key ever seen".
        """
        stale_keys = []
        for key, bucket in self._hits.items():
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                stale_keys.append(key)
        for key in stale_keys:
            del self._hits[key]

    def reset(self) -> None:
        """Test-only: clears this instance's state."""
        with self._lock:
            self._hits.clear()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


_login_window_seconds = _float_env("AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 60)
_refresh_window_seconds = _float_env("AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS", 60)

_login_per_email_limiter = SlidingWindowLimiter(
    max_attempts=_int_env("AUTH_LOGIN_RATE_LIMIT_PER_EMAIL", 5),
    window_seconds=_login_window_seconds,
)
_login_per_ip_limiter = SlidingWindowLimiter(
    max_attempts=_int_env("AUTH_LOGIN_RATE_LIMIT_PER_IP", 20),
    window_seconds=_login_window_seconds,
)
_refresh_per_ip_limiter = SlidingWindowLimiter(
    max_attempts=_int_env("AUTH_REFRESH_RATE_LIMIT_PER_IP", 30),
    window_seconds=_refresh_window_seconds,
)


def _client_ip(request: Request) -> str:
    # As seen by the Render process. Per the architecture notes, for browser
    # traffic that's Vercel's egress IP, not the real visitor IP — so this is
    # a coarse secondary defense, not the primary one; per-email is.
    return request.client.host if request.client else "unknown"


def enforce_login_rate_limit(request: Request, email: str) -> None:
    key = email.strip().lower()
    ip = _client_ip(request)
    email_ok = _login_per_email_limiter.hit(key)
    ip_ok = _login_per_ip_limiter.hit(ip)
    if not email_ok or not ip_ok:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again later."
        )


def enforce_refresh_rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    if not _refresh_per_ip_limiter.hit(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many refresh attempts. Try again later."
        )


def reset_all() -> None:
    """Test-only: resets every module-level limiter instance."""
    _login_per_email_limiter.reset()
    _login_per_ip_limiter.reset()
    _refresh_per_ip_limiter.reset()
