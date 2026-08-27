import time
import uuid

import jwt
import pytest

from book2audio.api.auth.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_verify_password_accepts_the_original_password():
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash) is True


def test_verify_password_rejects_a_wrong_password():
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("wrong password", password_hash) is False


def test_hash_password_salts_so_identical_passwords_hash_differently():
    assert hash_password("same-password") != hash_password("same-password")


def test_decode_access_token_recovers_the_user_id_that_was_encoded():
    user_id = uuid.uuid4()
    token, _ = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_create_access_token_reports_its_ttl_in_seconds():
    _, expires_in = create_access_token(uuid.uuid4())
    assert expires_in == 15 * 60


def test_decode_access_token_rejects_an_expired_token(monkeypatch):
    import book2audio.api.auth.security as security

    monkeypatch.setattr(security, "ACCESS_TOKEN_TTL", __import__("datetime").timedelta(seconds=-1))
    token, _ = create_access_token(uuid.uuid4())
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token)


def test_decode_access_token_rejects_a_tampered_token():
    token, _ = create_access_token(uuid.uuid4())
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


def test_decode_access_token_rejects_a_token_signed_with_a_different_secret():
    payload = {"sub": str(uuid.uuid4())}
    foreign_token = jwt.encode(payload, "some-other-secret-of-at-least-32-bytes", algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(foreign_token)


def test_generate_refresh_token_returns_a_hash_that_matches_the_raw_token():
    raw, token_hash, _expires_at = generate_refresh_token()
    assert hash_refresh_token(raw) == token_hash


def test_generate_refresh_token_produces_unique_tokens():
    raw_a, _, _ = generate_refresh_token()
    raw_b, _, _ = generate_refresh_token()
    assert raw_a != raw_b


def test_hash_refresh_token_is_deterministic():
    assert hash_refresh_token("some-raw-token") == hash_refresh_token("some-raw-token")
