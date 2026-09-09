"""Session-owned file directories and idle garbage collection."""

import asyncio
import logging
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SESSION_TTL = 4 * 60 * 60
logger = logging.getLogger(__name__)


class SessionFiles:
    def __init__(self, root):
        self.root = Path(root).resolve() / "sessions"
        if self.root.is_symlink():
            raise ValueError("Session storage must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = {}

    def directory(self, session_id):
        if not session_id or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("A valid initialized MCP session is required")
        directory = self.root / session_id
        if directory.is_symlink():
            raise ValueError("Session directory must not be a symlink")
        return directory

    def files(self, session_id):
        with self.lock:
            directory = self.directory(session_id)
            directory.mkdir(exist_ok=True)
            root = directory / "files"
            if root.is_symlink():
                raise ValueError("Session files directory must not be a symlink")
            root.mkdir(exist_ok=True)
            directory.touch()
            return root

    @contextmanager
    def lease(self, session_id):
        with self.lock:
            directory = self.directory(session_id)
            self.active[session_id] = self.active.get(session_id, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                self.active[session_id] -= 1
                if not self.active[session_id]:
                    del self.active[session_id]
                if directory.exists() and not directory.is_symlink():
                    directory.touch()

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        removed = []
        with self.lock:
            for directory in self.root.iterdir():
                if (
                    not re.fullmatch(r"[0-9a-f]{32}", directory.name)
                    or directory.is_symlink()
                    or not directory.is_dir()
                    or self.active.get(directory.name)
                ):
                    continue
                try:
                    if now - directory.stat().st_mtime >= SESSION_TTL:
                        shutil.rmtree(directory)
                        removed.append(directory.name)
                except OSError:
                    logger.exception(
                        "Failed to clean session directory %s", directory.name
                    )
        return removed

    async def run_cleanup(self):
        while True:
            try:
                await asyncio.to_thread(self.cleanup)
            except OSError:
                logger.exception("Session cleanup scan failed")
            await asyncio.sleep(SESSION_TTL)
