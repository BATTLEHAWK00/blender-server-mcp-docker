"""Run inside GUI Blender on Xvfb, then return to Blender's event loop."""

import importlib.util
import os
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).parent))
from worker import configure_device

addon_path = Path("/app/vendor/blender-mcp/addon.py")
spec = importlib.util.spec_from_file_location("blender_mcp_addon", addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 64
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
try:
    device = configure_device(os.getenv("CYCLES_BACKEND", "AUTO"))
    print("Native Blender GPU:", device, flush=True)
except RuntimeError as exc:
    # Keep scene inspection available; do not silently switch rendering to CPU.
    scene.cycles.device = "GPU"
    print("GPU initialization failed:", exc, flush=True)
print("Blender MCP addon ready", flush=True)
