"""Reviewed tool catalog and bounded public web access."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit, urljoin

import httpx
from bs4 import BeautifulSoup
from pydantic import Field

from backend.config import settings
from backend.db import now, session_scope
from backend.models import Workspace
from backend.schemas import StrictModel
from backend.security import require_member

TOOL_NAMES = [
    "knowledge_search",
    "read_source",
    "web_search",
    "web_read",
    "gmail_search",
    "gmail_read",
    "drive_search",
    "drive_read",
]
TOOL_REVISION = "relay-tools-v1"
DESCRIPTIONS = {
    "knowledge_search": "Search authorized uploaded workspace documents.",
    "read_source": "Read a previously retrieved workspace chunk.",
    "web_search": "Search the public web for the user's question. Private evidence is never forwarded.",
    "web_read": "Read a public URL returned by web search or supplied by the user.",
    "gmail_search": "Search the executing user's connected Gmail messages.",
    "gmail_read": "Read a discovered Gmail message by remote_id, or its thread using thread: followed by thread_id.",
    "drive_search": "Search Google Drive files explicitly selected by the executing user.",
    "drive_read": "Read one of the executing user's selected Google Drive files.",
}


class Query(StrictModel):
    query: str = Field(min_length=1, max_length=2000)


class Remote(StrictModel):
    remote_id: str = Field(min_length=1, max_length=2000)


class ChunkArg(StrictModel):
    chunk_id: str = Field(min_length=1, max_length=2000)


def schema(name):
    return Query if name.endswith("search") else ChunkArg if name == "read_source" else Remote


def definitions(names):
    return [
        {
            "type": "function",
            "name": name,
            "description": DESCRIPTIONS[name],
            "strict": True,
            "parameters": schema(name).model_json_schema(),
        }
        for name in dict.fromkeys(names)
        if name in TOOL_NAMES
    ]


def permitted(user, workspace, tools):
    with session_scope() as db:
        require_member(db, user, workspace)
        return [name for name in tools if name in db.get(Workspace, workspace).tool_policy]


def public_target(url):
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("Only public HTTP/HTTPS pages are supported")
    addresses = {
        info[4][0]
        for info in socket.getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    }
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise PermissionError("Private network pages are not accessible")
    return parts, sorted(addresses)[0]


def web_source(url, title, text, location="Public page"):
    digest = hashlib.sha256((url + text).encode()).hexdigest()
    return {
        "chunk_id": digest,
        "document_id": url,
        "document_name": title,
        "document_version": 1,
        "location": location,
        "text": text[:12000],
        "kind": "web",
        "url": url,
        "remote_id": url,
        "fetched_at": now().isoformat() + "Z",
    }


def web_read(url):
    from backend.providers import MODEL_DEADLINE

    deadline = MODEL_DEADLINE.get()
    for _ in range(4):
        parts, address = public_target(url)
        timeout = min(10, max(0.05, (deadline - now()).total_seconds())) if deadline else 10
        port = parts.port or (443 if parts.scheme == "https" else 80)
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)

        def pinned_connect():
            sock = socket.create_connection((address, port), timeout=timeout)
            conn.sock = (
                ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
                if parts.scheme == "https"
                else sock
            )

        conn.connect = pinned_connect
        try:
            conn.request(
                "GET",
                parts.path + ("?" + parts.query if parts.query else "") or "/",
                headers={
                    "User-Agent": "RelayResearch/1.0",
                    "Accept": "text/html,text/plain",
                    "Accept-Encoding": "identity",
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if (
                response.status != 200
                or response.getheader("Content-Type", "").split(";")[0] not in {"text/html", "text/plain"}
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("This page cannot be read as public text")
            parts_read = []
            total = 0
            while total <= 2_000_000:
                if deadline and now() >= deadline:
                    raise TimeoutError("Page read exceeded the run deadline")
                part = response.read1(min(16384, 2_000_001-total))
                if not part:
                    break
                parts_read.append(part)
                total += len(part)
            data = b"".join(parts_read)
            if len(data) > 2_000_000:
                raise ValueError("Page is too large")
            soup = BeautifulSoup(data, "html.parser")
            title = soup.title.get_text() if soup.title else parts.hostname
            for tag in soup(["script", "style", "nav", "footer", "iframe", "form"]):
                tag.decompose()
            return [web_source(url, title, soup.get_text(" ", strip=True))]
        finally:
            conn.close()
    raise ValueError("Too many page redirects")


def web_search(question):
    if not settings().searxng_url:
        raise ValueError("Web search is not configured")
    response = httpx.get(
        settings().searxng_url.rstrip("/") + "/search",
        params={"q": question[:2000], "format": "json", "language": "en", "safesearch": 1},
        timeout=10,
    )
    response.raise_for_status()
    results = []
    for item in response.json().get("results", [])[:10]:
        try:
            public_target(item["url"])
            results.append(
                web_source(
                    item["url"], item.get("title", item["url"]), item.get("content", ""), "Search excerpt"
                )
            )
        except (ValueError, OSError, PermissionError):
            continue
    return results[:5]


def external_call(run, name, arguments, sources):
    arguments = schema(name).model_validate(arguments).model_dump()
    if name == "web_search":
        # Only the explicit user question leaves Relay, never a model query derived from private data.
        return web_search(run.question)
    if name == "web_read":
        url = arguments["remote_id"]
        allowed = {s["url"] for s in sources if s.get("kind") == "web"} | set(
            re.findall(r"https?://[^\s<>]+", run.question)
        )
        if url not in allowed:
            raise PermissionError("Only discovered public URLs or user-supplied URLs can be read")
        return web_read(url)
    if name == "gmail_read" and arguments["remote_id"] not in (
        {s.get("remote_id") for s in sources if s.get("kind") == "gmail"}
        | {"thread:" + s["thread_id"] for s in sources if s.get("kind") == "gmail" and s.get("thread_id")}
    ):
        raise PermissionError("Read only previously discovered Gmail messages")
    from backend.mcp_bridge import call

    return call(run.user_id, run.workspace_id, name, arguments)["sources"]
