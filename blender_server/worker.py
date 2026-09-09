"""Executed exclusively by Blender's bundled Python interpreter."""

import json
import sys
from pathlib import Path

import bpy


def configure_device(backend):
    if backend == "CPU":
        bpy.context.scene.cycles.device = "CPU"
        return {"backend": "CPU", "devices": ["CPU"]}
    prefs = bpy.context.preferences.addons["cycles"].preferences
    errors = []
    for candidate in ["OPTIX", "CUDA"] if backend == "AUTO" else [backend]:
        try:
            prefs.compute_device_type = candidate
            prefs.get_devices()
            selected = []
            for device in prefs.devices:
                device.use = device.type == candidate
                if device.use:
                    selected.append(device.name)
            if selected:
                bpy.context.scene.cycles.device = "GPU"
                return {"backend": candidate, "devices": selected}
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(f"No GPU available for {backend}: {errors}")


def main():
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    device = configure_device(config["backend"])
    print("BLENDER_SERVER_DEVICE=" + json.dumps(device), flush=True)
    if config.get("probe"):
        return
    scene.cycles.samples = config["samples"]
    scene.render.resolution_x = config["width"]
    scene.render.resolution_y = config["height"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.frame_set(config["frame"])
    scene.render.filepath = config["output"]
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
