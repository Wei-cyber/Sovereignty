"""Narrow Google adapters. No sending, deletion, or arbitrary URL operations."""

import base64
import io
import json
import time
from email.message import EmailMessage
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from pypdf import PdfReader
from sqlalchemy import select, update

from backend.config import settings
from backend.db import session_scope, now
from backend.models import GoogleConnection, Workspace, GmailDraft
from backend.schemas import fingerprint
from backend.security import require_member, audit

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SUPPORTED = {"application/vnd.google-apps.document", "application/pdf", "text/plain", "text/markdown"}


def encrypt(value):
    return Fernet(settings().connection_encryption_key.encode()).encrypt(json.dumps(value).encode()).decode()


def decrypt(value):
    return json.loads(Fernet(settings().connection_encryption_key.encode()).decrypt(value.encode()))


def connection(db, user_id, workspace_id, provider, capability=None):
    require_member(db, user_id, workspace_id)
    if capability and capability not in db.get(Workspace, workspace_id).tool_policy:
        raise PermissionError("This tool was disabled by your administrator")
    item = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.user_id == user_id,
            GoogleConnection.workspace_id == workspace_id,
            GoogleConnection.provider == provider,
        )
    )
    if not item or not item.active or not item.tokens:
        raise PermissionError(f"Connect {provider} in Connections first")
    return item


def access_token(user_id, workspace_id, provider, capability=None):
    with session_scope() as db:
        item = connection(db, user_id, workspace_id, provider, capability)
        db.refresh(item, with_for_update=True)
        if not item.active or not item.tokens:
            raise PermissionError("Reconnect your Google account")
        tokens = decrypt(item.tokens)
        if tokens.get("expires_at", 0) <= time.time() + 60:
            if not tokens.get("refresh_token"):
                raise PermissionError("Reconnect your Google account")
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings().google_client_id,
                    "client_secret": settings().google_client_secret,
                    "refresh_token": tokens["refresh_token"],
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )
            if response.status_code != 200:
                raise PermissionError("Google access expired; reconnect your account")
            updated = response.json()
            tokens.update(updated)
            tokens["expires_at"] = time.time() + updated["expires_in"]
            item.tokens = encrypt(tokens)
        return tokens["access_token"]


def google_request(
    user_id, workspace_id, provider, path, *, capability=None, params=None, body=None, limit=2_000_000
):
    from backend.providers import MODEL_DEADLINE

    deadline = MODEL_DEADLINE.get()
    if deadline and now() >= deadline:
        raise TimeoutError("Tool deadline exceeded")
    timeout = min(15, max(0.05, (deadline - now()).total_seconds())) if deadline else 15
    token = access_token(user_id, workspace_id, provider, capability)
    base = (
        "https://gmail.googleapis.com/gmail/v1/users/me/"
        if provider == "gmail"
        else "https://www.googleapis.com/drive/v3/"
    )
    with httpx.stream(
        "POST" if body is not None else "GET",
        base + path,
        headers={"Authorization": "Bearer " + token},
        params=params,
        json=body,
        timeout=timeout,
        follow_redirects=False,
    ) as response:
        if response.status_code not in {200, 201}:
            raise PermissionError("Google could not provide this item; check access or reconnect")
        data = bytearray()
        for part in response.iter_bytes():
            if deadline and now() >= deadline:
                raise TimeoutError("Tool deadline exceeded")
            data.extend(part)
            if len(data) > limit:
                raise ValueError("This item exceeds the supported size")
    return bytes(data)


def google_json(*args, **kwargs):
    return json.loads(google_request(*args, **kwargs))


def evidence(user, workspace, provider, remote_id, title, text, url, location=""):
    with session_scope() as db:
        item = connection(db, user, workspace, provider)
        cid, generation = item.id, item.generation
    return {
        "chunk_id": fingerprint(cid, generation, remote_id, text),
        "document_id": remote_id,
        "document_name": title,
        "document_version": 1,
        "location": location or provider,
        "text": text[:12000],
        "kind": provider,
        "url": url,
        "remote_id": remote_id,
        "connection_id": cid,
        "connection_generation": generation,
        "fetched_at": now().isoformat() + "Z",
    }


def mail_text(payload):
    kind = payload.get("mimeType", "")
    if payload.get("filename") or kind not in {
        "text/plain",
        "text/html",
        "multipart/alternative",
        "multipart/mixed",
        "multipart/related",
    }:
        return ""
    if kind.startswith("multipart/"):
        parts = payload.get("parts", [])
        if kind == "multipart/alternative":
            parts = [next((p for p in parts if p.get("mimeType") == "text/plain"), parts[0])] if parts else []
        return "\n".join(mail_text(p) for p in parts)
    encoded = payload.get("body", {}).get("data", "")
    text = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True) if kind == "text/html" else text


def gmail_read(user, workspace, remote_id):
    # Thread reads remain bounded and return individual, citable messages.
    if remote_id.startswith("thread:"):
        value = google_json(
            user,
            workspace,
            "gmail",
            "threads/" + quote(remote_id[7:], safe=""),
            capability="gmail_read",
            params={"format": "full"},
        )
        messages = value.get("messages", [])[-5:]
    else:
        messages = [
            google_json(
                user,
                workspace,
                "gmail",
                "messages/" + quote(remote_id, safe=""),
                capability="gmail_read",
                params={"format": "full"},
            )
        ]
    results = []
    for message in messages:
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        text = (
            "\n".join(f"{k.title()}: {headers.get(k, '')}" for k in ["from", "to", "date", "subject"])
            + "\n"
            + mail_text(message.get("payload", {}))
        )
        results.append(
            evidence(
                user,
                workspace,
                "gmail",
                message["id"],
                headers.get("subject", "Email"),
                text,
                "https://mail.google.com/mail/u/0/#all/" + message["id"],
            )
        )
    return results


def gmail_search(user, workspace, query):
    value = google_json(
        user, workspace, "gmail", "messages", capability="gmail_search", params={"q": query, "maxResults": 5}
    )
    results = []
    for item in value.get("messages", []):
        data = google_json(
            user,
            workspace,
            "gmail",
            "messages/" + quote(item["id"], safe=""),
            capability="gmail_search",
            params={"format": "metadata", "metadataHeaders": "Subject"},
        )
        title = next(
            (
                h["value"]
                for h in data.get("payload", {}).get("headers", [])
                if h["name"].lower() == "subject"
            ),
            "Email",
        )
        results.append(
            evidence(
                user,
                workspace,
                "gmail",
                item["id"],
                title,
                data.get("snippet", ""),
                "https://mail.google.com/mail/u/0/#all/" + item["id"],
                "Search excerpt; read the message for full context",
            )
        )
        results[-1]["thread_id"] = data.get("threadId", "")
    return results


def drive_metadata(user, workspace, file_id):
    return google_json(
        user,
        workspace,
        "drive",
        "files/" + quote(file_id, safe=""),
        params={"fields": "id,name,mimeType,modifiedTime,webViewLink,trashed", "supportsAllDrives": "true"},
    )


def drive_read(user, workspace, file_id):
    with session_scope() as db:
        item = connection(db, user, workspace, "drive", "drive_read")
        if file_id not in {f["id"] for f in item.selected_files}:
            raise PermissionError("Choose this file in Connections before reading it")
    info = drive_metadata(user, workspace, file_id)
    if info.get("trashed") or info["mimeType"] not in SUPPORTED:
        raise ValueError("Only selected Google Docs, text PDFs, TXT, and Markdown files are supported")
    path = "files/" + quote(file_id, safe="")
    params = (
        {"mimeType": "text/plain"}
        if info["mimeType"] == "application/vnd.google-apps.document"
        else {"alt": "media"}
    )
    raw = google_request(
        user,
        workspace,
        "drive",
        path + ("/export" if "mimeType" in params else ""),
        capability="drive_read",
        params=params,
        limit=20_000_000,
    )
    text = (
        "\n".join(
            f"Page {i + 1}\n{page.extract_text() or ''}"
            for i, page in enumerate(PdfReader(io.BytesIO(raw)).pages[:50])
        )
        if info["mimeType"] == "application/pdf"
        else raw.decode("utf-8", errors="replace")
    )
    return [
        evidence(
            user,
            workspace,
            "drive",
            file_id,
            info["name"],
            text,
            info.get("webViewLink", "https://drive.google.com/file/d/" + file_id + "/view"),
            info.get("modifiedTime", ""),
        )
    ]


def drive_search(user, workspace, query):
    with session_scope() as db:
        files = connection(db, user, workspace, "drive", "drive_search").selected_files
    # Provider-side search is still constrained to the per-file OAuth grant AND explicit allowlist.
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    value = google_json(
        user,
        workspace,
        "drive",
        "files",
        capability="drive_search",
        params={
            "q": f"trashed = false and (fullText contains '{escaped}')",
            "pageSize": 100,
            "fields": "files(id,name,webViewLink)",
        },
    )
    allowed = {f["id"] for f in files}
    return [
        evidence(
            user,
            workspace,
            "drive",
            f["id"],
            f["name"],
            "Matching selected file: " + f["name"],
            f.get("webViewLink", ""),
            "Search match; read file before answering",
        )
        for f in value.get("files", [])
        if f["id"] in allowed
    ][:5]


def source_available(db, user, workspace, source, remote=True):
    if source.get("kind", "knowledge") not in {"gmail", "drive"}:
        return True
    try:
        policy = db.get(Workspace, workspace).tool_policy
        if not any(t.startswith(source["kind"] + "_") and t != "gmail_save_draft" for t in policy):
            return False
        item = connection(db, user, workspace, source["kind"])
        if item.id != source["connection_id"] or item.generation != source["connection_generation"]:
            return False
        if item.provider == "drive" and source["remote_id"] not in {f["id"] for f in item.selected_files}:
            return False
        if remote:
            if item.provider == "drive":
                return not drive_metadata(user, workspace, source["remote_id"]).get("trashed", False)
            google_json(
                user,
                workspace,
                "gmail",
                "messages/" + quote(source["remote_id"], safe=""),
                params={"format": "minimal"},
            )
        return True
    except (PermissionError, ValueError, httpx.HTTPError, HTTPException):
        return False


def save_gmail_draft(user, workspace, operation_id, body):
    marker = f"relay-{operation_id}@relay.invalid"
    with session_scope() as db:
        conn = connection(db, user, workspace, "gmail", "gmail_save_draft")
        if COMPOSE_SCOPE not in conn.scopes:
            raise PermissionError("Enable Gmail draft saving in Connections first")
        op = db.scalar(select(GmailDraft).where(GmailDraft.operation_id == operation_id).with_for_update())
        if (
            not op
            or op.user_id != user
            or op.workspace_id != workspace
            or op.connection_id != conn.id
            or op.body_hash != fingerprint(body, conn.subject, conn.generation)
        ):
            raise PermissionError("Draft save was not explicitly authorized")
        if op.status == "completed":
            return {"status": "completed", "draft_id": op.remote_id}
        claimed = db.execute(
            update(GmailDraft)
            .where(GmailDraft.id == op.id, GmailDraft.status == "pending")
            .values(status="creating")
        )
        uncertain = not claimed.rowcount
    if uncertain:
        found = google_json(
            user,
            workspace,
            "gmail",
            "drafts",
            capability="gmail_save_draft",
            params={"q": f"in:drafts rfc822msgid:{marker}", "maxResults": 2},
        )
        matches = found.get("drafts", [])
        if len(matches) == 1:
            remote_id = matches[0]["id"]
        else:
            return {
                "status": "uncertain",
                "message": "The earlier save may have succeeded. Check Gmail Drafts; Relay will not create a duplicate.",
            }
    else:
        message = EmailMessage()
        message["To"] = ", ".join(body["to"])
        message["Subject"] = body["subject"]
        message["Message-ID"] = "<" + marker + ">"
        message.set_content(body["body"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        # This is the sole Gmail write endpoint in Relay. There is no send operation.
        value = google_json(
            user, workspace, "gmail", "drafts", capability="gmail_save_draft", body={"message": {"raw": raw}}
        )
        remote_id = value["id"]
    with session_scope() as db:
        op = db.scalar(select(GmailDraft).where(GmailDraft.operation_id == operation_id).with_for_update())
        op.status, op.remote_id = "completed", remote_id
        audit(db, user, "gmail.draft_saved", op.id, workspace)
    return {"status": "completed", "draft_id": remote_id}
