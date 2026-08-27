"""Integration tests for /auth/* against a real (SQLite-backed) DB session."""

from __future__ import annotations


def _register(client, email="reader@example.com", password="hunter2pass"):
    return client.post("/auth/register", json={"email": email, "password": password})


def _login(client, email="reader@example.com", password="hunter2pass"):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_register_creates_a_user_and_returns_its_id_and_email(client):
    response = _register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "reader@example.com"
    assert "id" in body


def test_register_never_returns_the_password_hash(client):
    body = _register(client).json()
    assert "password" not in body
    assert "password_hash" not in body


def test_register_with_a_duplicate_email_fails_with_409(client):
    _register(client)
    response = _register(client)
    assert response.status_code == 409


def test_register_rejects_a_password_shorter_than_8_characters(client):
    response = _register(client, password="short")
    assert response.status_code == 422


def test_login_with_correct_credentials_returns_a_token_pair(client):
    _register(client)
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == 15 * 60


def test_login_with_wrong_password_fails_with_401(client):
    _register(client)
    response = _login(client, password="totally-wrong-password")
    assert response.status_code == 401


def test_login_with_unregistered_email_fails_with_401(client):
    response = _login(client, email="nobody@example.com")
    assert response.status_code == 401


def test_refresh_issues_a_new_token_pair(client):
    _register(client)
    refresh_token = _login(client).json()["refresh_token"]
    response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert response.json()["refresh_token"] != refresh_token


def test_reusing_a_rotated_refresh_token_fails_with_401(client):
    _register(client)
    original_refresh_token = _login(client).json()["refresh_token"]
    first_refresh = client.post("/auth/refresh", json={"refresh_token": original_refresh_token})
    assert first_refresh.status_code == 200

    replay = client.post("/auth/refresh", json={"refresh_token": original_refresh_token})
    assert replay.status_code == 401


def test_refresh_with_an_unknown_token_fails_with_401(client):
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401


def test_logout_revokes_the_refresh_token_so_it_can_no_longer_be_used(client):
    _register(client)
    refresh_token = _login(client).json()["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    reuse_response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert reuse_response.status_code == 401


def test_logout_with_an_unknown_token_still_succeeds(client):
    """Logout is idempotent/best-effort: an already-invalid token isn't an error."""
    response = client.post("/auth/logout", json={"refresh_token": "never-issued"})
    assert response.status_code == 204
