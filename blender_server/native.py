"""HTTP transport and authentication around the unmodified upstream MCP server."""

import asyncio
import hmac
import os
import socket
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from blender_server.files import register_file_tools
from blender_server.sessions import SESSION_TTL


class OriginHandledByCORS:
    """Delegate Origin policy to outer CORS; retain SDK Host validation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["headers"] = [
                (key, value)
                for key, value in scope["headers"]
                if key.lower() != b"origin"
            ]
        await self.app(scope, receive, send)


def addon_available():
    try:
        with socket.create_connection(("127.0.0.1", 9876), timeout=1):
            return True
    except OSError:
        return False


def create_app():
    os.environ["BLENDER_MCP_DISABLE_TELEMETRY"] = "1"
    os.environ["BLENDER_HOST"] = "127.0.0.1"
    os.environ["BLENDER_PORT"] = "9876"
    from blender_mcp.server import mcp, server_lifespan

    sessions = register_file_tools(mcp)
    mcp.settings.stateless_http = False
    mcp.settings.session_idle_timeout = SESSION_TTL
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=os.getenv(
            "MCP_ALLOWED_HOSTS", "localhost,localhost:*,127.0.0.1,127.0.0.1:*"
        ).split(","),
        allowed_origins=os.getenv(
            "MCP_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*"
        ).split(","),
    )

    @asynccontextmanager
    async def request_lifespan(server):
        yield {}

    # Upstream owns one global addon socket. Keep its lifecycle at process scope;
    # individual HTTP sessions must not disconnect another session's socket.
    mcp._mcp_server.lifespan = request_lifespan
    mcp_app = mcp.streamable_http_app()
    token = os.getenv("API_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        async with server_lifespan(mcp):
            async with mcp.session_manager.run():
                cleanup_task = asyncio.create_task(sessions.run_cleanup())
                try:
                    yield
                finally:
                    cleanup_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await cleanup_task

    app = FastAPI(title="Native Blender MCP", lifespan=lifespan)

    app.state.session_files = sessions

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if (
            token
            and request.url.path != "/health"
            and not hmac.compare_digest(
                request.headers.get("authorization", ""), "Bearer " + token
            )
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        session_id = request.headers.get("mcp-session-id")
        if session_id:
            try:
                sessions.directory(session_id)
            except ValueError:
                return JSONResponse({"detail": "Invalid session ID"}, status_code=400)
            with sessions.lease(session_id):
                return await call_next(request)
        return await call_next(request)

    @app.get("/health")
    async def health():
        ready = await asyncio.to_thread(addon_available)
        return JSONResponse(
            {"status": "ok" if ready else "degraded", "addon_available": ready},
            status_code=200 if ready else 503,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version"],
        allow_credentials=False,
    )
    app.mount("/", OriginHandledByCORS(mcp_app))
    return app
