import asyncio
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from blender_server.app import create_app
from blender_server.jobs import Jobs, RenderRequest


@pytest.fixture
def fake_blender(tmp_path):
    path = tmp_path / "blender"
    path.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json, pathlib, sys, time
config = json.loads(pathlib.Path(sys.argv[-1]).read_text())
if config['samples'] == 2:
    time.sleep(60)
if config['samples'] == 3:
    sys.exit(7)
print('BLENDER_SERVER_DEVICE=' + json.dumps({'backend': 'OPTIX', 'devices': ['test GPU']}))
pathlib.Path(config['output']).write_bytes(b'\\x89PNG\\r\\n\\x1a\\n')
"""
    )
    path.chmod(0o755)
    return str(path)


def wait_job(client, job_id):
    for _ in range(200):
        record = client.get(f"/jobs/{job_id}").json()
        if record["status"] not in ("running", "queued"):
            return record
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def test_render_and_failure(tmp_path, fake_blender):
    with TestClient(create_app(tmp_path / "work", fake_blender)) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/jobs", json={"samples": 1})
        assert response.status_code == 202
        job_id = response.json()["id"]
        result = wait_job(client, job_id)
        assert result["status"] == "succeeded"
        assert result["device"]["backend"] == "OPTIX"
        assert client.get(f"/jobs/{job_id}/result").content.startswith(b"\x89PNG")
        failed_id = client.post("/jobs", json={"samples": 3}).json()["id"]
        assert wait_job(client, failed_id)["status"] == "failed"
        assert client.get(f"/jobs/{failed_id}/result").status_code == 409
        assert client.get("/jobs/missing").status_code == 404
        assert client.post("/jobs", json={"width": 0}).status_code == 422
        assert (
            client.post("/jobs", json={"blend_file": "../outside.blend"}).status_code
            == 400
        )
    recovered = Jobs(tmp_path / "work", fake_blender)
    assert recovered.get(job_id)["status"] == "succeeded"


def test_cancel_and_timeout(tmp_path, fake_blender):
    async def scenario():
        jobs = Jobs(tmp_path, fake_blender, timeout=0.1)
        running = jobs.submit(RenderRequest(samples=2))["id"]
        queued = jobs.submit(RenderRequest(samples=2))["id"]
        await asyncio.sleep(0.03)
        assert (await jobs.cancel(queued))["status"] == "cancelled"
        await asyncio.gather(*list(jobs.tasks.values()))
        assert jobs.get(running)["status"] == "failed"
        assert "exceeded" in jobs.get(running)["error"]
        cancelled = jobs.submit(RenderRequest(samples=2))["id"]
        await asyncio.sleep(0.03)
        assert (await jobs.cancel(cancelled))["status"] == "cancelled"
        immediate = jobs.submit(RenderRequest())["id"]
        assert (await jobs.cancel(immediate))["status"] == "cancelled"
        await jobs.close()

    asyncio.run(scenario())


def test_path_symlink_and_restart(tmp_path, fake_blender):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside.blend"
    outside.write_bytes(b"BLENDER")
    (root / "escape.blend").symlink_to(outside)
    jobs = Jobs(root, fake_blender)
    with pytest.raises(ValueError):
        jobs.source("escape.blend")
    folder = root / "jobs" / ("a" * 32)
    folder.mkdir()
    (folder / "job.json").write_text(
        json.dumps({"id": folder.name, "status": "running"})
    )
    assert Jobs(root, fake_blender).get(folder.name)["error"] == "Server restarted"


def test_auth_and_mcp(tmp_path, fake_blender, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    with TestClient(
        create_app(tmp_path, fake_blender), base_url="http://localhost"
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/jobs").status_code == 401
        assert client.post("/mcp", json={}).status_code == 401
        headers = {
            "Authorization": "Bearer test-token",
            "Accept": "application/json, text/event-stream",
        }
        init = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200, init.text
        assert "serverInfo" in init.json()["result"]
        tools = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        names = {tool["name"] for tool in tools.json()["result"]["tools"]}
        assert names == {
            "render",
            "get_job",
            "list_jobs",
            "cancel_job",
            "get_logs",
            "list_scenes",
        }
        call = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "render", "arguments": {"request": {"samples": 1}}},
            },
        )
        assert not call.json()["result"].get("isError"), call.text
        job_id = json.loads(call.json()["result"]["content"][0]["text"])["id"]
        client.headers.update(headers)
        assert wait_job(client, job_id)["status"] == "succeeded"


def test_invalid_runtime_config(tmp_path, fake_blender):
    with pytest.raises(ValueError):
        Jobs(tmp_path, fake_blender, concurrency=0)
    with pytest.raises(ValueError):
        Jobs(tmp_path, fake_blender, timeout=0)
