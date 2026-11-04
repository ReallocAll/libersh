"""One-shot remote execution with bounded output and unambiguous status."""
import asyncio
import shlex

from .response import encode_bytes


async def execute(conn, command: str, *, cwd: str | None = None,
                  stdin: str | None = None, pty: bool = False,
                  timeout: float = 60, max_output_bytes: int = 512 * 1024) -> dict:
    if timeout <= 0 or max_output_bytes <= 0 or max_output_bytes > 512 * 1024:
        raise ValueError("timeout must be positive and max_output_bytes must be in (0, 524288]")
    # cwd is a deliberately requested command wrapper, not a hidden change to command.
    actual_command = f"cd -- {shlex.quote(cwd)} && {command}" if cwd is not None else command
    proc = await conn.create_process(actual_command, term_type="xterm" if pty else None, encoding=None)
    data = {"stdout": bytearray(), "stderr": bytearray()}
    lengths = {"stdout": 0, "stderr": 0}
    kept = 0

    async def consume(name: str, reader) -> None:
        nonlocal kept
        while chunk := await reader.read(65536):
            lengths[name] += len(chunk)
            room = max(0, max_output_bytes - kept)
            if room:
                take = chunk[:room]
                data[name].extend(take)
                kept += len(take)

    async def input_and_wait():
        if stdin is not None:
            proc.stdin.write(stdin.encode("utf-8"))
            await proc.stdin.drain()
        proc.stdin.write_eof()
        await proc.wait()

    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(
                consume("stdout", proc.stdout),
                consume("stderr", proc.stderr),
                input_and_wait(),
            )
    except TimeoutError:
        # Closing the SSH channel is NOT proof that the remote process terminated.
        proc.close()
        return {
            "status": "timeout", "remote_process_state": "unknown", "timeout_seconds": timeout,
            "stdout": encode_bytes(bytes(data["stdout"])),
            "stderr": encode_bytes(bytes(data["stderr"])),
            "stdout_bytes_received": lengths["stdout"],
            "stderr_bytes_received": lengths["stderr"],
        }

    return {
        "status": "exited", "exit_code": proc.exit_status,
        "exit_signal": str(proc.exit_signal) if proc.exit_signal else None,
        "stdout": encode_bytes(bytes(data["stdout"])),
        "stderr": encode_bytes(bytes(data["stderr"])),
        "stdout_bytes_received": lengths["stdout"],
        "stderr_bytes_received": lengths["stderr"],
        "stdout_truncated": len(data["stdout"]) < lengths["stdout"],
        "stderr_truncated": len(data["stderr"]) < lengths["stderr"],
        "pty": pty,
    }
