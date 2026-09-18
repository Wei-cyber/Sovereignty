import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import get_db, now
from backend.models import AuditEvent, AuthSession, Membership, User

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("timing-only-non-login-password")


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(password, encoded):
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("workspace_session", "")
    login = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token), AuthSession.expires_at > now())
    )
    user = db.get(User, login.user_id) if login else None
    if not user or not user.active:
        raise HTTPException(401, "Please sign in")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), login.csrf):
            raise HTTPException(403, "Invalid CSRF token")
    request.state.auth_session = login
    return user


def require_member(db, user_id, workspace_id, admin=False):
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(403, "Account access has been revoked")
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user_id, Membership.workspace_id == workspace_id)
    )
    if not membership:
        raise HTTPException(404, "Workspace not found")
    if admin and membership.role != "admin":
        raise HTTPException(403, "Workspace administrator access required")
    return membership


def require_system_admin(user):
    if not user.is_system_admin:
        raise HTTPException(403, "System administrator access required")


def audit(db, actor_id, action, target_id=None, workspace_id=None, **detail):
    db.add(
        AuditEvent(
            actor_id=actor_id, action=action, target_id=target_id, workspace_id=workspace_id, detail=detail
        )
    )


def new_session(db, user_id, hours):
    token = secrets.token_urlsafe(48)
    login = AuthSession(
        user_id=user_id,
        token_hash=hash_token(token),
        csrf=secrets.token_hex(32),
        expires_at=now() + timedelta(hours=hours),
    )
    db.add(login)
    return token, login
