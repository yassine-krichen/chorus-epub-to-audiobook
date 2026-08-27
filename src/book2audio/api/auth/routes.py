"""Auth endpoints: register, login, refresh, logout.

Stays thin — parses/validates the request and delegates to security.py for
hashing/token logic and directly to the ORM for the simple lookups involved.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from book2audio.api.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from book2audio.api.auth.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from book2audio.api.deps import get_db
from book2audio.db.models import RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenPairResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None:
        # Still pay the Argon2 cost so a nonexistent email doesn't return
        # faster than a wrong password for a real account (timing side-channel).
        verify_password(payload.password, DUMMY_PASSWORD_HASH)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _issue_token_pair(user, db)


@router.post("/refresh", response_model=TokenPairResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if stored is None or not _refresh_token_is_valid(stored):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    stored.revoked_at = datetime.now(timezone.utc)
    user = db.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    return _issue_token_pair(user, db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> None:
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        db.commit()


def _refresh_token_is_valid(token: RefreshToken) -> bool:
    if token.revoked_at is not None:
        return False
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _issue_token_pair(user: User, db: Session) -> TokenPairResponse:
    access_token, expires_in = create_access_token(user.id)
    raw_refresh, refresh_hash, refresh_expires_at = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=refresh_expires_at,
        )
    )
    db.commit()
    return TokenPairResponse(
        access_token=access_token, refresh_token=raw_refresh, expires_in=expires_in
    )
