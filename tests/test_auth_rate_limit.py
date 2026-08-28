"""Integration tests for rate limiting on /auth/login and /auth/refresh.

Exercises the module-level limiters wired into api/auth/routes.py through a
real TestClient, using the *default* limits (no env var overrides -- the
limiter thresholds are baked in at rate_limit.py's import time, so setting
AUTH_LOGIN_RATE_LIMIT_* via monkeypatch after import would have no effect):
5 attempts per email, 20 per IP for /auth/login; 30 per IP for /auth/refresh.

This file is named to sort alphabetically *before* test_auth_routes.py, so
running the full suite exercises these rate-limit-tripping tests first and
then test_auth_routes.py's plain login/refresh tests -- if those still pass
cleanly afterwards, that's proof conftest.py's autouse `_reset_rate_limits`
fixture actually clears limiter state between tests instead of leaking it.
"""

from __future__ import annotations


def _register(client, email="reader@example.com", password="hunter2pass"):
    return client.post("/auth/register", json={"email": email, "password": password})


def _login(client, email="reader@example.com", password="wrong-password"):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_login_allows_five_wrong_password_attempts_for_the_same_email(client):
    _register(client, email="ratelimited@example.com")

    responses = [_login(client, email="ratelimited@example.com") for _ in range(5)]

    assert all(r.status_code == 401 for r in responses)


def test_login_rejects_the_sixth_attempt_for_the_same_email_with_429(client):
    _register(client, email="ratelimited@example.com")
    for _ in range(5):
        _login(client, email="ratelimited@example.com")

    response = _login(client, email="ratelimited@example.com")

    assert response.status_code == 429


def test_login_429_response_has_a_clear_message_and_leaks_no_internal_details(client):
    _register(client, email="ratelimited@example.com")
    for _ in range(5):
        _login(client, email="ratelimited@example.com")

    response = _login(client, email="ratelimited@example.com")

    assert response.json() == {"detail": "Too many login attempts. Try again later."}
    assert "Traceback" not in response.text
    assert "SlidingWindowLimiter" not in response.text
    assert "_hits" not in response.text


def test_login_rate_limit_is_scoped_per_email_not_shared_across_different_emails(client):
    for _ in range(5):
        _login(client, email="a@example.com")
    assert _login(client, email="a@example.com").status_code == 429

    # A different email, same TestClient (same source IP), is unaffected by
    # a's per-email limit -- it should get the normal 401, not a 429.
    response = _login(client, email="b@example.com")

    assert response.status_code == 401


def test_login_per_ip_limit_trips_even_when_no_single_emails_own_limit_is_exceeded(client):
    # Spread 20 attempts across 5 emails (4 each -- under the 5-per-email
    # cap) so only the per-IP counter, not any per-email counter, explains
    # the 21st request being rejected.
    for i in range(20):
        email = f"user{i % 5}@example.com"
        response = _login(client, email=email)
        assert response.status_code == 401

    response = _login(client, email="brand-new-email@example.com")

    assert response.status_code == 429


def test_refresh_allows_up_to_thirty_calls_from_the_same_ip(client):
    responses = [
        client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        for _ in range(30)
    ]

    assert all(r.status_code == 401 for r in responses)


def test_refresh_rejects_the_call_past_the_per_ip_limit_with_429(client):
    for _ in range(30):
        client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})

    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many refresh attempts. Try again later."}
