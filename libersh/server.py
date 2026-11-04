"""Libersh MCP: authorized Linux SSH operations without command policy filters."""
import logging
from typing import Annotated

import asyncssh
import httpx
from fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .config import (ConfigurationError, get_public_keys as configured_public_keys,
                     list_hosts as configured_hosts, resolve_target)
from .connection import ssh_connection
from .execute import execute
from .files import (attrs_dict, decode_payload, file_operation,
                    list_sftp_directory, read_sftp_page, write_sftp)
from .jobs import list_jobs, output_job, start_job, status_job, stop_job
from .response import format_result
from .transfer import OpenAIFile, upload_reference

mcp = FastMCP("Libersh", version=__version__)
LOGGER = logging.getLogger("libersh")

HostArg = Annotated[str, Field(description=(
    "Use a configured HOST_<NAME> alias (e.g. HOST_PROD), or a dynamic "
    "ssh://username@hostname[:port] URL (e.g. ssh://root@45.32.51.15). "
    "Dynamic URLs use the global SSH_KEYS private keys; bare IP addresses "
    "are not valid host arguments."
))]


def _error(exc: Exception) -> str:
    # A signed ChatGPT download URL can occur in httpx exception text.
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"Attachment server returned HTTP {exc.response.status_code}"
    elif isinstance(exc, httpx.HTTPError):
        detail = "Attachment transport failed (URL and signature omitted)"
    elif isinstance(exc, ConfigurationError):
        detail = str(exc)
    elif isinstance(exc, ValueError):
        detail = str(exc)
    elif isinstance(exc, (asyncssh.Error, OSError)):
        detail = str(exc)
    else:
        detail = type(exc).__name__
    return format_result({"error": type(exc).__name__, "detail": detail})


async def _with_connection(host, action):
    try:
        target = resolve_target(host)
        async with ssh_connection(target) as conn:
            return format_result(await action(conn))
    except (Exception) as exc:
        return _error(exc)


@mcp.tool
def list_hosts() -> str:
    """List configured HOST_* profiles, not dynamic SSH URL targets; no secrets."""
    try:
        return format_result(configured_hosts())
    except Exception as exc:
        return _error(exc)


@mcp.tool
def get_public_keys(host: str | None = None) -> str:
    """Return plaintext OpenSSH public keys for all SSH_KEYS entries and an optional host key.

    Use host="HOST_PROD" to include that host's private_key-derived public key.
    Private keys, passwords and passphrases are never returned.
    """
    try:
        return format_result(configured_public_keys(host))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="exec")
async def exec_command(host: HostArg, command: str,
                       cwd: str | None = None, stdin: str | None = None,
                       pty: bool = False, timeout: float = 60,
                       max_output_bytes: int = 524288) -> str:
    """Execute a command via HOST_<NAME> or ssh://username@hostname[:port].

    Dynamic SSH URLs require SSH_KEYS, not a preconfigured host profile.
    No command allowlist. Nonzero exit is a normal command result.
    Timeout does not prove the remote process stopped. Use job_start for
    durable execution or large logs.
    """
    return await _with_connection(host,
        lambda conn: execute(conn, command, cwd=cwd, stdin=stdin,
                             pty=pty, timeout=timeout,
                             max_output_bytes=max_output_bytes))



# Import tool modules after mcp and the connection helper have been defined.
from . import file_tools, job_tools  # noqa: E402,F401
