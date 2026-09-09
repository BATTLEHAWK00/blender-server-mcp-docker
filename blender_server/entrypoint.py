"""Supervise Xvfb, persistent Blender and the HTTP MCP server as one container."""

import os
import signal
import socket
import subprocess
import sys
import time


def main():
    processes = []

    def interrupted(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)

    def start(command, env=None):
        process = subprocess.Popen(command, env=env, start_new_session=True)
        processes.append(process)
        return process

    try:
        start(["Xvfb", ":99", "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-ac"])
        deadline = time.monotonic() + 20
        while not os.path.exists("/tmp/.X11-unix/X99"):
            if processes[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Xvfb failed to start")
            time.sleep(0.1)
        env = os.environ.copy()
        env.update(DISPLAY=":99", LIBGL_ALWAYS_SOFTWARE="1")
        # The addon can execute Python; keep HTTP credentials out of its environment.
        env.pop("API_TOKEN", None)
        blender = start(
            [
                os.getenv("BLENDER_BIN", "blender"),
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                "/app/blender_server/bootstrap.py",
            ],
            env,
        )
        deadline = time.monotonic() + 90
        while True:
            if blender.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Blender addon failed to start")
            try:
                with socket.create_connection(("127.0.0.1", 9876), timeout=1):
                    break
            except OSError:
                time.sleep(0.25)
        start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "blender_server.native:create_app",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "10849",
            ]
        )
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        raise RuntimeError("A required child process exited; restarting container")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    main()
