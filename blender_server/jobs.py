import asyncio
import json
import math
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RenderRequest(BaseModel):
    blend_file: str | None = Field(
        default=None,
        description="Path relative to WORK_DIR; null renders Blender's default cube",
    )
    backend: Literal["AUTO", "OPTIX", "CUDA", "HIP", "ONEAPI", "METAL", "CPU"] = "AUTO"
    frame: int = Field(default=1, ge=0, le=1000000)
    samples: int = Field(default=64, ge=1, le=4096)
    width: int = Field(default=512, ge=16, le=8192)
    height: int = Field(default=512, ge=16, le=8192)


class Jobs:
    def __init__(
        self, root: Path, blender: str, timeout: float = 1800, concurrency: int = 1
    ):
        if concurrency < 1 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Concurrency and timeout must be positive")
        self.root = root.resolve()
        self.directory = self.root / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.blender = blender
        self.timeout = timeout
        self.limit = asyncio.Semaphore(concurrency)
        self.tasks = {}
        self.records = {}
        for path in self.directory.glob("*/job.json"):
            record = json.loads(path.read_text())
            self.records[record["id"]] = record
            if record["status"] in ("queued", "running"):
                record.update(
                    status="failed", error="Server restarted", finished_at=time.time()
                )
                self.save(record)

    def save(self, record):
        path = self.directory / record["id"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False))
        temp.replace(path)

    def source(self, value):
        path = (self.root / value).resolve()
        if (
            not path.is_relative_to(self.root)
            or not path.is_file()
            or path.suffix.lower() != ".blend"
        ):
            raise ValueError(
                "blend_file must be an existing .blend file inside WORK_DIR"
            )
        return path

    def submit(self, request: RenderRequest):
        source = str(self.source(request.blend_file)) if request.blend_file else None
        if (
            sum(r["status"] in ("queued", "running") for r in self.records.values())
            >= 100
        ):
            raise ValueError("Render queue is full (100 jobs)")
        job_id = uuid.uuid4().hex
        folder = self.directory / job_id
        folder.mkdir()
        config = request.model_dump() | {"output": str(folder / "render.png")}
        (folder / "request.json").write_text(json.dumps(config))
        record = dict(
            id=job_id,
            status="queued",
            created_at=time.time(),
            request=request.model_dump(),
            error=None,
            device=None,
        )
        self.records[job_id] = record
        self.save(record)
        task = asyncio.create_task(self.run(record, source))
        self.tasks[job_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job_id, None))
        return dict(record)

    def get(self, job_id):
        if job_id not in self.records:
            raise KeyError("Job not found")
        return dict(self.records[job_id])

    async def stop(self, process):
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()

    async def run(self, record, source):
        folder = self.directory / record["id"]
        process = None
        try:
            async with self.limit:
                record.update(status="running", started_at=time.time())
                self.save(record)
                command = [
                    self.blender,
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                ]
                if source:
                    command.append(source)
                command += [
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(Path(__file__).with_name("worker.py")),
                    "--",
                    str(folder / "request.json"),
                ]
                with (folder / "render.log").open("wb") as log:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=log,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        await asyncio.wait_for(process.wait(), self.timeout)
                    finally:
                        # Reap the process before releasing its concurrency slot.
                        await self.stop(process)
                if process.returncode != 0:
                    raise RuntimeError(
                        f"Blender exited with code {process.returncode}; see render.log"
                    )
                if not (folder / "render.png").is_file():
                    raise RuntimeError("Blender produced no image")
                record["status"] = "succeeded"
        except asyncio.CancelledError:
            record["status"] = "cancelled"
        except TimeoutError:
            record.update(status="failed", error=f"Render exceeded {self.timeout}s")
        except Exception as exc:
            record.update(status="failed", error=str(exc))
        finally:
            if process is not None:
                await self.stop(process)
            log_path = folder / "render.log"
            if log_path.exists():
                with log_path.open(errors="replace") as stream:
                    for line in stream:
                        if line.startswith("BLENDER_SERVER_DEVICE="):
                            record["device"] = json.loads(line.split("=", 1)[1])
            record["finished_at"] = time.time()
            self.save(record)

    async def cancel(self, job_id):
        record = self.get(job_id)
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first execution never enters its finally block.
            if self.records[job_id]["status"] in ("queued", "running"):
                self.records[job_id].update(status="cancelled", finished_at=time.time())
                self.save(self.records[job_id])
        return self.get(record["id"])

    async def close(self):
        await asyncio.gather(*(self.cancel(job_id) for job_id in list(self.tasks)))

    def logs(self, job_id):
        self.get(job_id)
        path = self.directory / job_id / "render.log"
        if not path.exists():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16384))
            return stream.read().decode(errors="replace")
