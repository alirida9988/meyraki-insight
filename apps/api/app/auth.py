"""Cookie-session auth with org scoping (M5 security pass).

Self-contained for the pilot: bcrypt password hashes, opaque tokens stored
hashed in the DB (revocable), httpOnly cookie. A managed provider can replace
this later without touching the org-scoping model.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .db import get_session
from .models import AuthSession, User

COOKIE = "meyraki_session"
SESSION_TTL = timedelta(days=14)
MIN_PASSWORD_LEN = 8
BCRYPT_MAX_BYTES = 72  # bcrypt's hard limit; longer inputs raise on bcrypt>=5

# Constant-time login: verified against this when the account doesn't exist, so
# the bcrypt cost is paid either way and response time can't confirm an email
# (review M2). Precomputed to keep it off the import path.
DUMMY_HASH = "$2b$12$XN8CBjxZ4OMrPzGiBFDrLeqgZsJYblWkPO6GiPmpySvIw8NneYSjG"


def _clamp(password: str) -> bytes:
    """bcrypt hashes at most 72 bytes — truncate symmetrically instead of
    raising, so a ~37-character Arabic passphrase is registrable (review M3)."""
    return password.encode()[:BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_clamp(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_clamp(password), password_hash.encode())
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    now = datetime.now(timezone.utc)
    # Opportunistic reaping so the table can't grow forever (review M16).
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(token_hash=_token_hash(token), user_id=user.id, expires_at=now + SESSION_TTL)
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
        # Set MEYRAKI_HTTPS=1 in any TLS deployment — a terminating proxy does
        # NOT add Secure on its own (review m12).
        secure=os.environ.get("MEYRAKI_HTTPS") == "1",
        max_age=int(SESSION_TTL.total_seconds()),
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
