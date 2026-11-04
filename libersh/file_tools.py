"""Libersh SFTP MCP tool registration."""
from .server import mcp, _with_connection, _error, HostArg
from fastmcp.tools import ToolResult
from .files import (attrs_dict,decode_payload,file_operation,list_sftp_directory,read_sftp_page,write_sftp)
from .transfer import OpenAIFile,upload_reference

@mcp.tool
async def stat(host: HostArg, path: str,
               follow_symlinks: bool = True) -> ToolResult:
    """Get remote path metadata via SFTP (not a Shell command)."""
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            attrs = await (sftp.stat(path) if follow_symlinks else sftp.lstat(path))
            return {"path": path, **attrs_dict(attrs)}
    return await _with_connection(host, action)


@mcp.tool
async def list_dir(host: HostArg, path: str,
                   offset: int = 0, limit: int = 100) -> ToolResult:
    """List directory entries using SFTP; offsets are not snapshot-stable."""
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            return await list_sftp_directory(sftp, path, offset=offset, limit=limit)
    return await _with_connection(host, action)


@mcp.tool
async def read_file(host: HostArg, path: str,
                    offset: int = 0, limit_bytes: int = 65536) -> ToolResult:
    """Read a byte-addressed file page via SFTP; invalid UTF-8 is Base64."""
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            return await read_sftp_page(sftp, path, offset=offset, limit=limit_bytes)
    return await _with_connection(host, action)


@mcp.tool
async def write_file(host: HostArg, path: str, data: str,
                     encoding: str = "utf-8", mode: str = "overwrite",
                     offset: int = 0) -> ToolResult:
    """Write bytes through SFTP. mode: overwrite, append, offset, atomic_replace."""
    try:
        payload = decode_payload(data, encoding)
    except Exception as exc:
        return _error(exc)
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            return await write_sftp(sftp, path, payload, mode=mode, offset=offset)
    return await _with_connection(host, action)


@mcp.tool
async def file_op(host: HostArg, operation: str, path: str, destination: str | None = None,
                  permissions: int | None = None) -> ToolResult:
    """SFTP mkdir/remove/rmdir/rename/posix_rename/chmod, per OS permissions."""
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            return await file_operation(sftp, operation, path,
                                        destination=destination, permissions=permissions)
    return await _with_connection(host, action)


@mcp.tool(meta={"openai/fileParams": ["file"]})
async def upload_file(host: HostArg, path: str, file: OpenAIFile, atomic_replace: bool = False,
                      expected_sha256: str | None = None) -> ToolResult:
    """Stream a ChatGPT-authorized attachment directly to a remote SFTP file.

    Max source attachment size is 512 MB, no Base64 through model context.
    Direct mode can leave a partial file on failure. Atomic mode uses the
    server's POSIX rename extension and leaves the target unchanged on failure.
    """
    async def action(conn):
        async with conn.start_sftp_client() as sftp:
            return await upload_reference(sftp, file, path, atomic_replace=atomic_replace,
                                          expected_sha256=expected_sha256)
    return await _with_connection(host, action)


