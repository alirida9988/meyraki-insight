"""Cookie-session auth with org scoping (M5 security pass).

Self-contained for the pilot: bcrypt password hashes, opaque tokens stored
hashed in the DB (revocable), httpOnly cookie. A managed provider can replace
this later without touching the org-scoping model.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .db import get_session
from .models import AuthSession, User

COOKIE = "meyraki_session"
SESSION_TTL = timedelta(days=14)
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        )
    )
    db.commit()
    return token


def destroy_session(db: Session, token: str) -> None:
    row = db.get(AuthSession, _token_hash(token))
    if row is not None:
        db.delete(row)
        db.commit()


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
        # secure=True belongs to the HTTPS deployment config (pilot: reverse proxy)
    )


def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in to continue")
    row = db.get(AuthSession, _token_hash(token))
    if row is None:
        raise HTTPException(401, "Session expired — sign in again")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(401, "Session expired — sign in again")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user
