"""SFTP primitives: offset reads, explicit write modes, and bounded memory."""
import base64
import binascii
import os
import posixpath
import uuid

from .response import DEFAULT_PAGE, MAX_RAW_PAGE, encode_bytes, limit_bytes, page_payload, format_result


async def read_sftp_page(sftp, path: str, *, offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
    limit_bytes(limit)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be nonnegative")
    attrs = await sftp.stat(path)
    size = attrs.size or 0
    if offset >= size:
        return {"path": path, **page_payload(b"", offset, size)}
    data = bytearray()
    async with sftp.open(path, "rb", block_size=65536) as file:
        await file.seek(offset)
        while len(data) < min(limit, size - offset):
            chunk = await file.read(min(65536, limit - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    # JSON string escaping can be much bigger than the raw bytes. Fit the actual MCP envelope.
    raw = bytes(data)
    while True:
        result = {"path": path, **page_payload(raw, offset, size)}
        try:
            format_result(result)
            return result
        except ValueError:
            if len(raw) <= 1:
                raise
            raw = raw[:len(raw) // 2]


def decode_payload(data: str, encoding: str) -> bytes:
    if encoding == "utf-8":
        return data.encode("utf-8")
    if encoding == "base64":
        try:
            return base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid base64 data") from exc
    raise ValueError("encoding must be utf-8 or base64")


async def write_sftp(sftp, path: str, data: bytes, *, mode: str = "overwrite",
                     offset: int = 0) -> dict:
    if mode not in ("overwrite", "append", "offset", "atomic_replace"):
        raise ValueError("mode must be overwrite, append, offset or atomic_replace")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be nonnegative")
    temporary = None
    if mode == "atomic_replace":
        parent = posixpath.dirname(path) or "."
        temporary = posixpath.join(parent, f".libersh-{uuid.uuid4().hex}.tmp")
    target = temporary or path
    open_mode = {"overwrite": "wb", "append": "ab", "offset": "r+b", "atomic_replace": "xb"}[mode]
    try:
        async with sftp.open(target, open_mode, block_size=65536) as file:
            if mode == "offset":
                await file.seek(offset)
            for i in range(0, len(data), 65536):
                await file.write(data[i:i + 65536])
        if temporary:
            await sftp.posix_rename(temporary, path)
    except BaseException:
        if temporary:
            try:
                await sftp.remove(temporary)
            except OSError:
                pass
        raise
    return {"path": path, "mode": mode, "bytes_written": len(data)}


def attrs_dict(attrs) -> dict:
    return {
        "size": attrs.size,
        "permissions": attrs.permissions,
        "uid": attrs.uid,
        "gid": attrs.gid,
        "mtime": attrs.mtime,
        "atime": attrs.atime,
    }


async def list_sftp_directory(sftp, path: str, *, offset: int = 0, limit: int = 100) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not (1 <= limit <= 500):
        raise ValueError("offset must be >=0 and limit must be 1..500")
    entries = []
    i = 0
    # The SFTP protocol doesn't guarantee stable directory ordering across calls.
    async for entry in sftp.scandir(path):
        if i >= offset and len(entries) < limit:
            entries.append({"name": entry.filename, **attrs_dict(entry.attrs)})
        i += 1
    return {
        "path": path, "offset": offset, "entries": entries,
        "next_offset": offset + len(entries) if offset + len(entries) < i else None,
        "total_entries": i,
        "order": "server-dependent; not a stable snapshot",
    }


async def file_operation(sftp, operation: str, path: str, *,
                         destination: str | None = None, permissions: int | None = None) -> dict:
    if operation == "mkdir":
        await sftp.mkdir(path)
    elif operation == "remove":
        await sftp.remove(path)
    elif operation == "rmdir":
        await sftp.rmdir(path)
    elif operation == "rename":
        if destination is None:
            raise ValueError("destination required for rename")
        await sftp.rename(path, destination)
    elif operation == "posix_rename":
        if destination is None:
            raise ValueError("destination required for posix_rename")
        await sftp.posix_rename(path, destination)
    elif operation == "chmod":
        if permissions is None:
            raise ValueError("permissions required for chmod")
        await sftp.chmod(path, permissions)
    else:
        raise ValueError("Unknown file operation")
    return {"operation": operation, "path": path, "destination": destination}
