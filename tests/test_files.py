import base64
import json
import sys
from contextlib import asynccontextmanager
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP

from blender_server.native import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    upstream = ModuleType("blender_mcp.server")
    upstream.mcp = FastMCP("test-native")

    @asynccontextmanager
    async def lifespan(server):
        yield {}

    upstream.server_lifespan = lifespan
    package = ModuleType("blender_mcp")
    monkeypatch.setitem(sys.modules, "blender_mcp", package)
    monkeypatch.setitem(sys.modules, "blender_mcp.server", upstream)
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("API_TOKEN", "test-token")
    with TestClient(create_app(), base_url="http://localhost") as client:
        initialize(client)
        yield client


def initialize(client):
    client.headers.pop("mcp-session-id", None)
    response = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )
    assert response.status_code == 200, response.text
    client.headers["mcp-session-id"] = response.headers["mcp-session-id"]
    client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer test-token",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return response.headers["mcp-session-id"]


def session_root(client, tmp_path):
    return tmp_path / "work" / "sessions" / client.headers["mcp-session-id"] / "files"


def rpc(client, method, params=None, auth=True):
    headers = {"Accept": "application/json, text/event-stream"}
    if auth:
        headers["Authorization"] = "Bearer test-token"
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def call(client, name, **arguments):
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    if not result.get("isError"):
        result["structuredContent"] = json.loads(result["content"][0]["text"])
    return result


def test_roundtrip(client, tmp_path):
    assert rpc(client, "tools/list", auth=False).status_code == 401
    names = {t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert {"upload_file", "download_file", "list_files"} <= names
    data = bytes(range(256)) * 600
    path = "assets/test.blend"
    for offset in range(0, len(data), 65536):
        result = call(
            client,
            "upload_file",
            path=path,
            offset=offset,
            data_base64=base64.b64encode(data[offset : offset + 65536]).decode(),
        )
        assert not result.get("isError"), result
    assert (session_root(client, tmp_path) / path).read_bytes() == data
    received = bytearray()
    while True:
        result = call(client, "download_file", path=path, offset=len(received))
        assert not result.get("isError"), result
        chunk = result["structuredContent"]
        received.extend(base64.b64decode(chunk["data_base64"]))
        assert chunk["next_offset"] == len(received)
        if chunk["eof"]:
            break
    assert received == data
    listing = call(client, "list_files", path="assets")["structuredContent"]
    assert listing["entries"] == [{"path": path, "type": "file", "size": len(data)}]
    assert call(client, "upload_file", path=path, data_base64="").get("isError")
    assert call(client, "upload_file", path=path, offset=1, data_base64="YQ==").get(
        "isError"
    )
    assert (session_root(client, tmp_path) / path).read_bytes() == data
    assert not call(
        client, "upload_file", path=path, data_base64="", overwrite=True
    ).get("isError")
    assert call(client, "download_file", path=path)["structuredContent"]["eof"]


def test_invalid_paths_and_chunks(client, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    call(client, "list_files")
    (session_root(client, tmp_path) / "escape").symlink_to(outside)
    for path in ["../outside", str(outside), "escape"]:
        assert call(client, "download_file", path=path).get("isError")
        assert call(
            client, "upload_file", path=path, data_base64="YQ==", overwrite=True
        ).get("isError")
    assert outside.read_bytes() == b"private"
    for args in [
        {"data_base64": "!"},
        {"data_base64": "YQ==", "offset": -1},
        {"data_base64": "A" * (1400000)},
    ]:
        assert call(client, "upload_file", path="bad", **args).get("isError")
    assert not (session_root(client, tmp_path) / "bad").exists()
    for args in [{"length": 0}, {"length": 1048577}, {"offset": -1}]:
        assert call(client, "download_file", path="missing", **args).get("isError")
    assert call(client, "download_file", path="missing").get("isError")
    assert call(client, "list_files")["structuredContent"]["entries"] == []


def test_session_isolation(client, tmp_path):
    first = client.headers["mcp-session-id"]
    result = call(client, "upload_file", path="same.bin", data_base64="YQ==")
    first_path = result["structuredContent"]["absolute_path"]
    second = initialize(client)
    assert second != first
    assert call(client, "list_files")["structuredContent"]["entries"] == []
    assert call(client, "download_file", path=first_path).get("isError")
    assert call(
        client, "upload_file", path=first_path, data_base64="Yg==", overwrite=True
    ).get("isError")
    assert not call(client, "upload_file", path="same.bin", data_base64="Yg==").get(
        "isError"
    )
    client.headers["mcp-session-id"] = first
    assert (
        call(client, "download_file", path="same.bin")["structuredContent"][
            "data_base64"
        ]
        == "YQ=="
    )
    client.headers["mcp-session-id"] = "a" * 32
    assert rpc(client, "tools/list").status_code == 404
    client.headers.pop("mcp-session-id")
    assert rpc(client, "tools/list").status_code == 400


def test_cleanup(tmp_path):
    import os
    import time
    from blender_server.sessions import SessionFiles, SESSION_TTL

    store = SessionFiles(tmp_path)
    stale, active, fresh = "a" * 32, "b" * 32, "c" * 32
    now = time.time()
    for sid in (stale, active, fresh):
        (store.files(sid) / "file").write_text("data")
    for sid in (stale, active):
        os.utime(store.directory(sid), (now - SESSION_TTL - 1,) * 2)
    outside = tmp_path / "unmanaged"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (store.root / ("d" * 32)).symlink_to(outside, target_is_directory=True)
    with store.lease(active):
        assert store.cleanup(now) == [stale]
        assert store.directory(active).exists()
    assert store.directory(fresh).exists()
    assert (outside / "keep").exists()
    assert store.cleanup(now) == []
    # Persisted timestamps remain usable across process restarts.
    assert set(SessionFiles(tmp_path).cleanup(now + SESSION_TTL + 2)) == {active, fresh}
