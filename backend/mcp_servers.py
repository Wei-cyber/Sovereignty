"""The two bundled Google MCP servers. Run only on the private service network."""

import argparse
import asyncio
import time
from datetime import timedelta
from contextvars import ContextVar

from mcp.server import MCPServer
from starlette.responses import JSONResponse

from backend import google_services as google
from backend.mcp_bridge import verify

claims_context = ContextVar("mcp_claims")


def identity(tool):
    claims = claims_context.get()
    if claims["capability"] != tool:
        raise PermissionError("Tool is outside this request's capability")
    return claims["user"], claims["workspace"]


def create_app(provider):
    server = MCPServer("Relay " + provider, version="1.0.0")
    if provider == "gmail":

        @server.tool()
        async def gmail_search(query: str) -> dict:
            """Search the connected user's Gmail messages; returns cited excerpts."""
            return {"sources": await asyncio.to_thread(google.gmail_search, *identity("gmail_search"), query)}

        @server.tool()
        async def gmail_read(remote_id: str) -> dict:
            """Read a Gmail message or thread:ID, without attachments."""
            return {"sources": await asyncio.to_thread(google.gmail_read, *identity("gmail_read"), remote_id)}

        @server.tool()
        async def gmail_save_draft(operation_id: str, body: dict) -> dict:
            """Save an explicitly authorized unsent draft. Cannot send messages."""
            return await asyncio.to_thread(
                google.save_gmail_draft, *identity("gmail_save_draft"), operation_id, body
            )
    else:

        @server.tool()
        async def drive_search(query: str) -> dict:
            """Search only Google Drive files explicitly selected by this user."""
            return {"sources": await asyncio.to_thread(google.drive_search, *identity("drive_search"), query)}

        @server.tool()
        async def drive_read(remote_id: str) -> dict:
            """Read one explicitly selected Google Doc, text PDF, TXT or Markdown file."""
            return {"sources": await asyncio.to_thread(google.drive_read, *identity("drive_read"), remote_id)}

    app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")

    class Authentication:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            if scope["path"] == "/health":
                return await JSONResponse({"status": "ok"})(scope, receive, send)
            try:
                header = dict(scope["headers"]).get(b"authorization", b"").decode()
                if not header.startswith("Bearer "):
                    raise PermissionError()
                claims = verify(header[7:], provider)
            except Exception:
                return await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
            token = claims_context.set(claims)
            from backend.providers import MODEL_DEADLINE
            from backend.db import now

            deadline_token = MODEL_DEADLINE.set(
                now() + timedelta(seconds=max(0, claims["exp"] - time.time()))
            )
            try:
                await self.app(scope, receive, send)
            finally:
                claims_context.reset(token)
                MODEL_DEADLINE.reset(deadline_token)

    return Authentication(app)


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["gmail", "drive"])
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.provider),
        host="0.0.0.0",
        port=8101 if args.provider == "gmail" else 8102,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
