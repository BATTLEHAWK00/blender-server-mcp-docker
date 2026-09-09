"""Exercise upstream tools over HTTP, including addon, scene, code, screenshot and GPU."""

import argparse
import base64
import json
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="mcp-client.local.json")
parser.add_argument("--output", default="work/native-viewport.png")
args = parser.parse_args()
config = next(iter(json.loads(Path(args.config).read_text())["mcpServers"].values()))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers = config.get("headers", {}) | {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def rpc(method, params):
    req = urllib.request.Request(
        config["url"],
        headers=headers,
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode(),
    )
    with opener.open(req, timeout=200) as response:
        if response.headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
        data = json.load(response)
    assert "error" not in data, data
    return data["result"]


def call(name, arguments=None):
    result = rpc(
        "tools/call",
        {
            "name": name,
            "arguments": {
                "user_prompt": "能不能直接把原生的mcp暴露出去？",
                **(arguments or {}),
            },
        },
    )
    assert not result.get("isError"), result
    for content in result.get("content", []):
        if content["type"] == "text":
            assert not content["text"].startswith(("Error ", "Error:", "Rejected")), (
                content["text"]
            )
    return result


def text(result):
    return "\n".join(c["text"] for c in result["content"] if c["type"] == "text")


print(
    "Server:",
    rpc(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "native-e2e", "version": "1"},
        },
    )["serverInfo"],
)
with opener.open(
    urllib.request.Request(
        config["url"],
        headers=headers,
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ).encode(),
    ),
    timeout=200,
) as response:
    assert response.status == 202
names = {tool["name"] for tool in rpc("tools/list", {})["tools"]}
assert {
    "get_addon_status",
    "get_scene_info",
    "get_object_info",
    "execute_blender_code",
    "get_viewport_screenshot",
} <= names
print("Native tools:", len(names))
status = text(call("get_addon_status"))
assert json.JSONDecoder().raw_decode(status)[0]["up_to_date"], status
print("Addon: matched")
version = text(
    call(
        "execute_blender_code",
        {
            "code": "import bpy; assert bpy.app.version == (5, 2, 1), bpy.app.version_string; print(bpy.app.version_string)"
        },
    )
)
print("Blender:", version)
scene = text(call("get_scene_info"))
assert "Cube" in scene, scene
code = """
import bpy
obj = bpy.data.objects.get('MCP_E2E_Cube')
if obj is None:
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(2, 0, 0))
    obj = bpy.context.object
    obj.name = 'MCP_E2E_Cube'
print('NATIVE_CODE_OK', obj.name)
"""
assert "NATIVE_CODE_OK" in text(call("execute_blender_code", {"code": code}))
assert "MCP_E2E_Cube" in text(call("get_object_info", {"object_name": "MCP_E2E_Cube"}))
image = call("get_viewport_screenshot", {"max_size": 256})
png = base64.b64decode(
    next(c["data"] for c in image["content"] if c["type"] == "image")
)
assert png.startswith(b"\x89PNG\r\n\x1a\n")
Path(args.output).write_bytes(png)
code = """
import bpy
s = bpy.context.scene
assert s.cycles.device == 'GPU', 'CPU fallback'
p = bpy.context.preferences.addons['cycles'].preferences
assert any(d.use and d.type != 'CPU' for d in p.devices), 'No active GPU'
s.render.resolution_x = 64
s.render.resolution_y = 64
s.cycles.samples = 4
s.render.filepath = '/work/native-render.png'
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath='/work/native-e2e.blend')
print('NATIVE_GPU_OK', p.compute_device_type)
"""
render = text(call("execute_blender_code", {"code": code}))
assert "NATIVE_GPU_OK" in render, render
print(render)
print(
    "PASS: addon, scene inspection, object creation, Python execution, viewport screenshot, GPU render and .blend save"
)
