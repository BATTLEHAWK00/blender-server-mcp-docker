import hmac
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from blender_server.jobs import Jobs, RenderRequest


def create_app(root=None, blender=None):
    jobs = Jobs(
        Path(root or os.getenv("WORK_DIR", "./work")),
        blender or os.getenv("BLENDER_BIN", "blender"),
        float(os.getenv("RENDER_TIMEOUT", "1800")),
        int(os.getenv("RENDER_CONCURRENCY", "1")),
    )
    token = os.getenv("API_TOKEN", "")
    allowed_hosts = os.getenv(
        "MCP_ALLOWED_HOSTS", "localhost,localhost:*,127.0.0.1,127.0.0.1:*,[::1],[::1]:*"
    ).split(",")
    mcp = FastMCP(
        "blender-server",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host.strip() for host in allowed_hosts],
            allowed_origins=[
                origin.strip()
                for origin in os.getenv(
                    "MCP_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*"
                ).split(",")
            ],
        ),
    )

    @mcp.tool()
    async def render(request: RenderRequest) -> dict:
        """Queue a Cycles render. Poll get_job; download PNG through the REST result URL."""
        return jobs.submit(request)

    @mcp.tool()
    async def get_job(job_id: str) -> dict:
        """Get render status and selected GPU. Successful PNG: /jobs/{job_id}/result."""
        return jobs.get(job_id)

    @mcp.tool()
    async def list_jobs() -> list[dict]:
        """List the latest 100 rendering jobs."""
        return list(jobs.records.values())[-100:]

    @mcp.tool()
    async def cancel_job(job_id: str) -> dict:
        """Cancel a queued or running render."""
        return await jobs.cancel(job_id)

    @mcp.tool()
    async def get_logs(job_id: str) -> str:
        """Read the last 16 KiB of Blender's render log."""
        return jobs.logs(job_id)

    @mcp.tool()
    async def list_scenes() -> list[str]:
        """List .blend files available in WORK_DIR (up to 1000)."""
        result = []
        for path in jobs.root.rglob("*.blend"):
            if path.resolve().is_relative_to(jobs.root):
                result.append(str(path.relative_to(jobs.root)))
            if len(result) >= 1000:
                break
        return result

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await jobs.close()

    app = FastAPI(title="Blender GPU Server", lifespan=lifespan)
    app.state.jobs = jobs

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if (
            token
            and request.url.path != "/health"
            and not hmac.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {token}"
            )
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Job not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/health")
    async def health():
        available = shutil.which(jobs.blender) is not None
        return JSONResponse(
            {
                "status": "ok" if available else "degraded",
                "blender_available": available,
            },
            status_code=200 if available else 503,
        )

    @app.post("/jobs", status_code=202)
    async def submit(request: RenderRequest):
        return jobs.submit(request)

    app.get("/jobs")(list_jobs)
    app.get("/scenes")(list_scenes)
    app.get("/jobs/{job_id}")(get_job)
    app.post("/jobs/{job_id}/cancel")(cancel_job)
    app.get("/jobs/{job_id}/logs")(get_logs)

    @app.get("/jobs/{job_id}/result")
    async def result(job_id: str):
        if jobs.get(job_id)["status"] != "succeeded":
            raise HTTPException(409, "Render has not succeeded")
        return FileResponse(
            jobs.directory / job_id / "render.png",
            media_type="image/png",
            filename=f"{job_id}.png",
        )

    app.mount("/", mcp_app)
    return app
