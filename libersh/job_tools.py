"""Libersh persistent Linux jobs MCP tool registration."""
from .server import mcp, _with_connection
from .jobs import list_jobs, output_job, start_job, status_job, stop_job

@mcp.tool
async def job_start(host: str, command: str,
                    cwd: str | None = None, job_id: str | None = None) -> str:
    """Start detached Linux job with durable filesystem state; caller can reuse job_id.

    A failed or interrupted launch may have executed: always query the same job
    ID before resubmitting. Jobs run in their own POSIX session and process group.
    """
    return await _with_connection(host,
        lambda conn: start_job(conn, command, cwd=cwd, job_id=job_id))


@mcp.tool
async def job_status(host: str, job_id: str) -> str:
    """Read job terminal record and lock state from the remote Linux host."""
    return await _with_connection(host,
        lambda conn: status_job(conn, job_id))


@mcp.tool
async def job_output(host: str, job_id: str,
                     stream: str = "stdout", offset: int = 0,
                     limit_bytes: int = 65536) -> str:
    """Read stdout, stderr or internal launcher logs by byte offset via SFTP."""
    return await _with_connection(host,
        lambda conn: output_job(conn, job_id, stream, offset=offset, limit=limit_bytes))


@mcp.tool
async def job_stop(host: str, job_id: str,
                   grace_seconds: int = 3) -> str:
    """Request TERM then KILL for a verified Linux job process group."""
    return await _with_connection(host,
        lambda conn: stop_job(conn, job_id, grace_seconds=grace_seconds))


@mcp.tool
async def job_list(host: str,
                   limit: int = 50) -> str:
    """Find recent Libersh job IDs in the SSH user's remote temporary directory."""
    return await _with_connection(host,
        lambda conn: list_jobs(conn, limit=limit))
