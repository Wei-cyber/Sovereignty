"""Authenticated MCP transport. Google OAuth tokens never cross this boundary."""

import asyncio
import base64
import hashlib
import hmac
import json
import time

from backend.config import settings


def issue(user, workspace, capability, provider):
    from backend.providers import MODEL_DEADLINE
    from backend.db import now

    deadline = MODEL_DEADLINE.get()
    ttl = min(60, max(0, (deadline - now()).total_seconds())) if deadline else 60
    if len(settings().mcp_signing_key) < 32:
        raise ValueError("MCP authentication is not configured")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "user": user,
                "workspace": workspace,
                "capability": capability,
                "aud": provider,
                "exp": time.time() + ttl,
            }
        ).encode()
    ).decode()
    signature = hmac.new(settings().mcp_signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def verify(token, provider):
    if len(settings().mcp_signing_key) < 32:
        raise PermissionError("MCP not configured")
    payload, signature = token.split(".")
    expected = hmac.new(settings().mcp_signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("Invalid MCP authentication")
    claims = json.loads(base64.urlsafe_b64decode(payload))
    if claims["exp"] < time.time() or claims["aud"] != provider:
        raise PermissionError("Expired or wrong-audience MCP authentication")
    return claims


async def call_async(user, workspace, tool, arguments):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from backend.providers import MODEL_DEADLINE
    from backend.db import now

    service = "gmail" if tool.startswith("gmail_") else "drive"
    url = settings().gmail_mcp_url if service == "gmail" else settings().drive_mcp_url
    deadline = MODEL_DEADLINE.get()
    remaining = max(0.05, (deadline - now()).total_seconds()) if deadline else 45
    async with asyncio.timeout(remaining):
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + issue(user, workspace, tool, service)}, timeout=remaining
        ) as client:
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=remaining) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    definition = next((item for item in catalog.tools if item.name == tool), None)
                    if not definition:
                        raise PermissionError("MCP tool is unavailable")
                    import jsonschema

                    jsonschema.validate(arguments, definition.input_schema)
                    result = await session.call_tool(tool, arguments)
                    if result.is_error:
                        raise ValueError(
                            "Google tool could not complete. Check connection and source access."
                        )
                    if result.structured_content is not None:
                        return result.structured_content
                    blocks = [item.text for item in result.content if item.type == "text"]
                    return json.loads("".join(blocks))


def call(user, workspace, tool, arguments):
    return asyncio.run(call_async(user, workspace, tool, arguments))
