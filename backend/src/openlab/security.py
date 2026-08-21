import hashlib
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from argon2 import PasswordHasher
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import SessionToken, User

hasher = PasswordHasher()


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, str]:
    settings = get_settings()
    raw = token_urlsafe(32)
    csrf = token_urlsafe(24)
    token = SessionToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_hours),
    )
    db.add(token)
    return raw, csrf


def current_user(
    openlab_session: str | None = Cookie(default=None), db: Session = Depends(get_db)
) -> User:
    if not openlab_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    token = db.scalar(
        select(SessionToken).where(SessionToken.token_hash == hash_token(openlab_session))
    )
    if not token or token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown session user")
    return user


def require_csrf(
    csrf_cookie: str | None = Cookie(default=None, alias="openlab_csrf"),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
