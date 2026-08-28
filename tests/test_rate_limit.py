"""Unit tests for SlidingWindowLimiter (book2audio.api.auth.rate_limit).

Uses fresh SlidingWindowLimiter instances (not the module-level singletons
enforce_login_rate_limit/enforce_refresh_rate_limit use) so these tests never
touch global limiter state -- no need for the autouse _reset_rate_limits
fixture here. time.monotonic is monkeypatched so window-elapsed behavior is
tested deterministically, without sleeping for a real window.
"""

from __future__ import annotations

import book2audio.api.auth.rate_limit as rate_limit_module
from book2audio.api.auth.rate_limit import SlidingWindowLimiter


def _frozen_clock(monkeypatch, start: float = 1_000.0):
    """Patch time.monotonic to a mutable fake clock; returns a setter."""
    state = {"t": start}
    monkeypatch.setattr(rate_limit_module.time, "monotonic", lambda: state["t"])

    def advance(seconds: float) -> None:
        state["t"] += seconds

    return advance


def test_hit_allows_every_attempt_up_to_max_attempts_within_the_window(monkeypatch):
    _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60)

    assert limiter.hit("key") is True
    assert limiter.hit("key") is True
    assert limiter.hit("key") is True


def test_hit_rejects_the_attempt_immediately_after_max_attempts_within_the_window(monkeypatch):
    _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.hit("key")

    assert limiter.hit("key") is False


def test_hit_keeps_rejecting_further_attempts_still_inside_the_window(monkeypatch):
    advance = _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    limiter.hit("key")
    limiter.hit("key")
    assert limiter.hit("key") is False

    advance(30)  # still inside the 60s window

    assert limiter.hit("key") is False


def test_hit_allows_attempts_again_once_the_window_has_fully_elapsed(monkeypatch):
    advance = _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    limiter.hit("key")
    limiter.hit("key")
    assert limiter.hit("key") is False

    advance(60.001)  # strictly past the window: old hits expire

    assert limiter.hit("key") is True


def test_hit_tracks_different_keys_independently(monkeypatch):
    _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)

    assert limiter.hit("a") is True
    assert limiter.hit("b") is True
    # each key already used its one allowed attempt
    assert limiter.hit("a") is False
    assert limiter.hit("b") is False


def test_reset_clears_this_instances_state_so_attempts_are_allowed_again(monkeypatch):
    _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    limiter.hit("key")
    assert limiter.hit("key") is False

    limiter.reset()

    assert limiter.hit("key") is True


def test_reset_does_not_affect_a_different_limiter_instance(monkeypatch):
    _frozen_clock(monkeypatch)
    limiter_a = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    limiter_b = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    limiter_a.hit("key")
    limiter_b.hit("key")

    limiter_a.reset()

    assert limiter_a.hit("key") is True
    assert limiter_b.hit("key") is False  # untouched by limiter_a.reset()


# --- memory: stale keys must not accumulate in self._hits forever ----------
#
# `email` on /auth/login is attacker-controlled and checked before any DB
# lookup, so an attacker submitting many distinct emails must not be able to
# grow the limiter's internal dict without bound.


def test_a_stale_keys_bucket_is_evicted_from_the_internal_dict_once_its_window_elapses(
    monkeypatch,
):
    advance = _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=60)
    limiter.hit("key")
    assert "key" in limiter._hits

    advance(60.001)  # strictly past the window: "key"'s only hit expires
    # A hit for an unrelated key is enough to trigger the sweep that evicts
    # "key" -- proving eviction isn't just a side effect of re-hitting "key"
    # itself (a fresh hit() for "key" would trivially succeed either way).
    limiter.hit("unrelated-key")

    assert "key" not in limiter._hits, (
        "stale key's now-empty deque was left sitting in the dict forever"
    )


def test_the_internal_dict_stays_bounded_when_many_distinct_keys_are_each_hit_only_once(
    monkeypatch,
):
    advance = _frozen_clock(monkeypatch)
    limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=60)

    for i in range(500):
        limiter.hit(f"attacker-email-{i}@example.com")
    assert len(limiter._hits) == 500  # all still within the window, expected

    advance(60.001)  # every one of those 500 keys' windows has now elapsed
    limiter.hit("one-more-key")  # sweeps stale entries as a side effect

    # Only the key just hit (still within its own fresh window) remains --
    # memory is bounded by "keys active within the last window", not by the
    # total number of distinct keys ever seen.
    assert len(limiter._hits) == 1
    assert list(limiter._hits) == ["one-more-key"]
