"""Short-lived SSH connections; no Horizon instance-local SSH state."""
from contextlib import asynccontextmanager
import asyncssh
from .config import Target, connect_kwargs


@asynccontextmanager
async def ssh_connection(target: Target):
    async with asyncssh.connect(**connect_kwargs(target), connect_timeout=15) as conn:
        yield conn
