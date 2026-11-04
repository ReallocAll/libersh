"""Short-lived SSH connections using only environment-provided credentials."""
from contextlib import asynccontextmanager

import asyncssh

from .config import Target, connection_options


@asynccontextmanager
async def ssh_connection(target: Target):
    last_error = None
    for options in connection_options(target):
        try:
            conn = await asyncssh.connect(**options, connect_timeout=15)
        except asyncssh.PermissionDenied as exc:
            last_error = exc
            continue

        async with conn:
            yield conn
        return

    assert last_error is not None
    raise last_error
