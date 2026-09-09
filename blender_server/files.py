"""File transfer tools shared by MCP entrypoints."""

import base64
import binascii
import os

from mcp.server.fastmcp import Context

from blender_server.sessions import SessionFiles

CHUNK_SIZE = 1024 * 1024


def register_file_tools(mcp, root=None):
    sessions = SessionFiles(root or os.getenv("WORK_DIR", "./work"))
    lock = sessions.lock

    def session_root(ctx):
        request = ctx.request_context.request
        session_id = request.headers.get("mcp-session-id") if request else None
        return sessions.files(session_id)

    def resolve(root, path):
        candidate = root / path
        target = candidate.resolve()
        if not target.is_relative_to(root):
            raise ValueError("Path must stay inside the current session directory")
        # Reject symlinks within the session too, including dangling links.
        for part in (candidate, *candidate.parents):
            if part == root:
                break
            if part.is_symlink():
                raise ValueError("Symlinks are not allowed in file paths")
        return target

    @mcp.tool()
    def upload_file(
        path: str,
        data_base64: str,
        ctx: Context,
        offset: int = 0,
        overwrite: bool = False,
    ) -> dict:
        """Upload binary data into the current MCP session directory. Base64-encode at most 1 MiB per call.

        Start at offset=0 (existing files require overwrite=true). Continue with
        offset=next_offset; it must equal the current file size. Calls for a file
        must be sequential. Each chunk is immediately visible, so use the file in
        Blender only after all chunks succeed. An empty payload creates an empty
        file. Returns the absolute server path for execute_blender_code.
        """
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        if len(data_base64) > 4 * ((CHUNK_SIZE + 2) // 3):
            raise ValueError("Chunk exceeds 1 MiB; split the file into chunks")
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("data_base64 must be valid Base64") from exc
        if len(data) > CHUNK_SIZE:
            raise ValueError("Chunk exceeds 1 MiB; split the file into chunks")
        with lock:
            root = session_root(ctx)
            target = resolve(root, path)
            if target == root:
                raise ValueError("path must name a file")
            if offset == 0:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb" if overwrite else "xb") as stream:
                    stream.write(data)
            else:
                with target.open("r+b") as stream:
                    stream.seek(0, 2)
                    if stream.tell() != offset:
                        raise ValueError("offset must equal the current file size")
                    stream.write(data)
            return {
                "path": str(target.relative_to(root)),
                "absolute_path": str(target),
                "bytes_written": len(data),
                "next_offset": offset + len(data),
                "size": target.stat().st_size,
            }

    @mcp.tool()
    def download_file(
        path: str, ctx: Context, offset: int = 0, length: int = 65536
    ) -> dict:
        """Download a current-session file as Base64, without a separate HTTP endpoint.

        Decode data_base64 on the client. Repeat with offset=next_offset until
        eof=true. length is bytes, 1..1048576 (default 64 KiB). Keep the source
        unchanged while downloading. Paths may be relative or absolute within this session.
        """
        if offset < 0 or not 1 <= length <= CHUNK_SIZE:
            raise ValueError("offset must be nonnegative; length must be 1..1048576")
        with lock:
            root = session_root(ctx)
            target = resolve(root, path)
            with target.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                if offset > size:
                    raise ValueError("offset exceeds file size")
                stream.seek(offset)
                data = stream.read(length)
            return {
                "path": str(target.relative_to(root)),
                "data_base64": base64.b64encode(data).decode("ascii"),
                "size": size,
                "offset": offset,
                "next_offset": offset + len(data),
                "eof": offset + len(data) >= size,
            }

    @mcp.tool()
    def list_files(
        ctx: Context, path: str = ".", offset: int = 0, limit: int = 100
    ) -> dict:
        """List the current session directory to find uploaded assets and Blender outputs.

        Nonrecursive, sorted by name. Continue with next_offset when not null.
        """
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("offset must be nonnegative; limit must be 1..1000")
        with lock:
            root = session_root(ctx)
            directory = resolve(root, path)
            entries = sorted(
                (
                    p
                    for p in directory.iterdir()
                    if not p.is_symlink() and p.resolve().is_relative_to(root)
                ),
                key=lambda p: p.name,
            )
            return {
                "path": str(directory.relative_to(root)),
                "absolute_path": str(directory),
                "entries": [
                    {
                        "path": str(p.relative_to(root)),
                        "type": "directory" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None,
                    }
                    for p in entries[offset : offset + limit]
                ],
                "next_offset": offset + limit
                if offset + limit < len(entries)
                else None,
            }

    return sessions
