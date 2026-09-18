"""Personal Google authorization, selected files, and explicit draft writes."""

import base64
import hashlib
import secrets
import re
import time
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db import get_db, now
from backend.google_services import (
    READ_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    SUPPORTED,
    access_token,
    connection,
    drive_metadata,
    encrypt,
    decrypt,
)
from backend.models import GoogleConnection, GmailDraft, OAuthState, User, Workspace
from backend.schemas import StrictModel, EmailDraft, fingerprint
from backend.security import current_user, require_member, audit, hash_token
from backend import mcp_bridge

router = APIRouter(prefix="/api/v1", tags=["Connections"])
W = "/workspaces/{workspace_id}"


class ConnectInput(StrictModel):
    provider: Literal["gmail", "drive"]
    drafts: bool = False


class SelectedFiles(StrictModel):
    file_ids: list[str] = Field(max_length=100)


class DraftInput(StrictModel):
    operation_id: UUID
    draft: EmailDraft


class ToolPolicy(StrictModel):
    tools: list[str]


class SelectedFileContract(StrictModel):
    id: str
    name: str
    mime_type: str = ""


class ConnectionContract(StrictModel):
    id: str
    provider: Literal["gmail", "drive"]
    email: str
    active: bool
    drafts_enabled: bool
    selected_files: list[SelectedFileContract]


class ConnectionsContract(StrictModel):
    configured: bool
    picker_configured: bool
    callback_url: str
    allowed_tools: list[str]
    connections: list[ConnectionContract]


class DraftResultContract(StrictModel):
    status: Literal["completed", "uncertain"]
    draft_id: str | None = None
    message: str = ""


def configured():
    cfg = settings()
    return bool(
        cfg.google_client_id
        and cfg.google_client_secret
        and cfg.connection_encryption_key
        and len(cfg.mcp_signing_key) >= 32
    )


@router.get(W + "/connections", response_model=ConnectionsContract)
def listing(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id)
    items = db.scalars(
        select(GoogleConnection).where(
            GoogleConnection.user_id == user.id, GoogleConnection.workspace_id == workspace_id
        )
    ).all()
    return {
        "configured": configured(),
        "picker_configured": bool(settings().google_picker_api_key and settings().google_project_number),
        "callback_url": settings().app_origin + "/api/v1/connections/google/callback",
        "allowed_tools": db.get(Workspace, workspace_id).tool_policy,
        "connections": [
            {
                "id": c.id,
                "provider": c.provider,
                "email": c.email,
                "active": c.active,
                "drafts_enabled": COMPOSE_SCOPE in c.scopes,
                "selected_files": c.selected_files if c.active else [],
            }
            for c in items
        ],
    }


@router.put(W + "/tool-policy")
def policy(
    workspace_id: str, body: ToolPolicy, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from backend.agent_tools import TOOL_NAMES

    require_member(db, user.id, workspace_id, True)
    if set(body.tools) - (set(TOOL_NAMES) | {"gmail_save_draft"}):
        raise HTTPException(400, "Unknown tool")
    db.get(Workspace, workspace_id).tool_policy = list(dict.fromkeys(body.tools))
    audit(db, user.id, "tools.policy_updated", workspace_id, workspace_id)
    return {"tools": body.tools}


@router.post(W + "/connections/authorize")
def authorize(
    workspace_id: str, body: ConnectInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_member(db, user.id, workspace_id)
    if not configured():
        raise HTTPException(409, "Your operator must configure Google sign-in first")
    allowed = db.get(Workspace, workspace_id).tool_policy
    if not any(t.startswith(body.provider + "_") for t in allowed) or (
        body.drafts and "gmail_save_draft" not in allowed
    ):
        raise HTTPException(403, "This integration is disabled by your administrator")
    scopes = ["openid", "email", DRIVE_SCOPE if body.provider == "drive" else READ_SCOPE]
    if body.provider == "gmail" and body.drafts:
        scopes.append(COMPOSE_SCOPE)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    db.add(
        OAuthState(
            user_id=user.id,
            workspace_id=workspace_id,
            provider=body.provider,
            state_hash=hash_token(state),
            verifier=encrypt(verifier),
            scopes=scopes,
            expires_at=now() + timedelta(minutes=10),
        )
    )
    query = {
        "client_id": settings().google_client_id,
        "redirect_uri": settings().app_origin + "/api/v1/connections/google/callback",
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
        "code_challenge_method": "S256",
    }
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)}


@router.get("/connections/google/callback")
def callback(
    state: str,
    code: str = "",
    error: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    pending = db.scalar(
        select(OAuthState).where(OAuthState.state_hash == hash_token(state)).with_for_update()
    )
    if not pending or pending.used or pending.expires_at < now() or pending.user_id != user.id:
        raise HTTPException(400, "Sign-in expired or invalid; start again from Connections")
    require_member(db, user.id, pending.workspace_id)
    pending.used = True
    db.commit()  # One-time callback, even when the token exchange fails.
    if error or not code:
        return RedirectResponse(settings().app_origin + "/#connections", status_code=303)
    try:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings().google_client_id,
                "client_secret": settings().google_client_secret,
                "redirect_uri": settings().app_origin + "/api/v1/connections/google/callback",
                "code": code,
                "code_verifier": decrypt(pending.verifier),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        response.raise_for_status()
        tokens = response.json()
        identity = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
            timeout=15,
        )
        identity.raise_for_status()
        info = identity.json()
        granted = tokens.get("scope", "").split()
        if not all(scope in granted for scope in pending.scopes if scope.startswith("https://")):
            raise ValueError("Missing requested permissions")
        item = db.scalar(
            select(GoogleConnection)
            .where(
                GoogleConnection.user_id == user.id,
                GoogleConnection.workspace_id == pending.workspace_id,
                GoogleConnection.provider == pending.provider,
            )
            .with_for_update()
        )
        if not item:
            item = GoogleConnection(
                user_id=user.id, workspace_id=pending.workspace_id, provider=pending.provider
            )
            db.add(item)
        else:
            if not tokens.get("refresh_token") and item.subject == info["sub"] and item.tokens:
                tokens["refresh_token"] = decrypt(item.tokens).get("refresh_token")
            if item.subject != info["sub"]:
                item.selected_files = []
            item.generation += 1
        tokens["expires_at"] = time.time() + tokens["expires_in"]
        item.tokens, item.email, item.subject, item.scopes, item.active = (
            encrypt(tokens),
            info["email"],
            info["sub"],
            granted,
            True,
        )
        audit(db, user.id, "connection.authorized", item.id, pending.workspace_id, provider=pending.provider)
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(
            400, "Google sign-in could not finish. Reconnect and grant the requested permissions."
        ) from None
    return RedirectResponse(settings().app_origin + "/#connections", status_code=303)


@router.delete(W + "/connections/{provider}")
def disconnect(
    workspace_id: str,
    provider: Literal["gmail", "drive"],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = connection(db, user.id, workspace_id, provider)
    item.active, item.tokens, item.selected_files = False, "", []
    item.generation += 1
    audit(db, user.id, "connection.disconnected", item.id, workspace_id, provider=provider)
    return {"disconnected": True}


@router.post(W + "/connections/{provider}/test")
def test(
    workspace_id: str,
    provider: Literal["gmail", "drive"],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from backend.google_services import google_json

    connection(db, user.id, workspace_id, provider)
    try:
        google_json(
            user.id,
            workspace_id,
            provider,
            "profile" if provider == "gmail" else "about",
            params=None if provider == "gmail" else {"fields": "user(emailAddress)"},
        )
    except (PermissionError, httpx.HTTPError):
        raise HTTPException(409, "Connection needs attention. Reconnect your Google account.") from None
    return {"status": "connected"}


@router.post(W + "/connections/drive/picker")
def picker(workspace_id: str, user: User = Depends(current_user)):
    return {
        "access_token": access_token(user.id, workspace_id, "drive", "drive_read"),
        "api_key": settings().google_picker_api_key,
        "app_id": settings().google_project_number,
    }


@router.put(W + "/connections/drive/files")
def select_files(
    workspace_id: str, body: SelectedFiles, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = connection(db, user.id, workspace_id, "drive", "drive_read")
    files = []
    for file_id in dict.fromkeys(body.file_ids):
        try:
            info = drive_metadata(user.id, workspace_id, file_id)
        except (PermissionError, httpx.HTTPError):
            raise HTTPException(400, "A selected file is inaccessible") from None
        if info.get("trashed") or info["mimeType"] not in SUPPORTED:
            raise HTTPException(400, "Choose Google Docs, text PDFs, TXT, or Markdown files")
        files.append({"id": info["id"], "name": info["name"], "mime_type": info["mimeType"]})
    if item.selected_files != files:
        item.generation += 1
    item.selected_files = files
    audit(db, user.id, "drive.selection_updated", item.id, workspace_id, count=len(files))
    return {"selected_files": files}


@router.post(W + "/gmail/drafts", response_model=DraftResultContract)
def save_draft(
    workspace_id: str, body: DraftInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    conn = connection(db, user.id, workspace_id, "gmail", "gmail_save_draft")
    if COMPOSE_SCOPE not in conn.scopes:
        raise HTTPException(409, "Enable Gmail draft saving in Connections first")
    draft = body.draft.model_dump()
    if any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address) for address in draft["to"]):
        raise HTTPException(400, "Use email addresses only, separated by commas")
    if any("\n" in value or "\r" in value for value in draft["to"] + [draft["subject"]]):
        raise HTTPException(400, "Invalid email headers")
    op = db.scalar(
        select(GmailDraft).where(GmailDraft.operation_id == str(body.operation_id)).with_for_update()
    )
    if op and (
        op.user_id != user.id
        or op.workspace_id != workspace_id
        or op.body_hash != fingerprint(draft, conn.subject, conn.generation)
    ):
        raise HTTPException(409, "This save identifier belongs to another draft")
    if not op:
        op = GmailDraft(
            user_id=user.id,
            workspace_id=workspace_id,
            connection_id=conn.id,
            operation_id=str(body.operation_id),
            body_hash=fingerprint(draft, conn.subject, conn.generation),
        )
        db.add(op)
        audit(db, user.id, "gmail.draft_authorized", op.id, workspace_id)
    db.commit()
    try:
        return mcp_bridge.call(
            user.id, workspace_id, "gmail_save_draft", {"operation_id": str(body.operation_id), "body": draft}
        )
    except Exception:
        raise HTTPException(
            409,
            "Draft save could not be confirmed. Retry the same save to check its status; do not create another copy.",
        ) from None
