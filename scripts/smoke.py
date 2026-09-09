"""Verify MCP handshake, submit a real GPU render through MCP, download via REST."""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:21849")
parser.add_argument(
    "--backend", default="AUTO", choices=["OPTIX", "CUDA", "CPU", "AUTO"]
)
parser.add_argument("--output", default="work/smoke.png")
args = parser.parse_args()
headers = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
if os.getenv("API_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["API_TOKEN"]


def request(path, data=None):
    req = urllib.request.Request(
        args.url.rstrip("/") + path,
        headers=headers,
        data=json.dumps(data).encode() if data is not None else None,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def rpc(method, params=None):
    result = json.loads(
        request(
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )
    )
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["result"]


print(json.loads(request("/health")))
print(
    rpc(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1"},
        },
    )["serverInfo"]
)
print("MCP tools:", [tool["name"] for tool in rpc("tools/list")["tools"]])
result = rpc(
    "tools/call",
    {
        "name": "render",
        "arguments": {
            "request": {
                "backend": args.backend,
                "width": 64,
                "height": 64,
                "samples": 4,
            }
        },
    },
)
if result.get("isError"):
    raise RuntimeError(result)
job_id = json.loads(result["content"][0]["text"])["id"]
deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    job = json.loads(request("/jobs/" + job_id))
    if job["status"] in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(1)
else:
    request("/jobs/" + job_id + "/cancel", {})
    raise TimeoutError("Smoke render timed out")
print(json.dumps(job, indent=2))
if job["status"] != "succeeded":
    raise RuntimeError(request("/jobs/" + job_id + "/logs").decode())
if args.backend != "CPU" and job["device"]["backend"] == "CPU":
    raise RuntimeError("Unexpected CPU fallback")
png = request("/jobs/" + job_id + "/result")
assert png.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG"
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(png)
print("PASS:", output, len(png), "bytes")
