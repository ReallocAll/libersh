"""Remote job status, output, stop and listing; files are source of truth."""
import json
import posixpath
import shlex
import time
from .config import job_base
from .files import read_sftp_page
from .job_core import RUNNER, ID_RE, validate_job_id, directory, remote_uid, _read_text
from .job_start import start_job

async def status_job(conn, job_id: str) -> dict:
    uid = await remote_uid(conn)
    root = directory(uid, job_id)
    async with conn.start_sftp_client() as sftp:
        if not await sftp.isdir(root):
            return {"job_id": job_id, "status": "not_found"}
        meta_str = await _read_text(sftp, posixpath.join(root, "meta.json"))
        meta = json.loads(meta_str) if meta_str else {}
        code_str = await _read_text(sftp, posixpath.join(root, "exit"), 64)
        if code_str is not None:
            try:
                code = int(code_str.strip())
            except ValueError:
                return {"job_id": job_id, "status": "unknown", "detail": "Invalid exit record"}
            return {"job_id": job_id, "status": "exited", "exit_code": code,
                    "path": root, "created_at": meta.get("created_at")}
        initial_boot = await _read_text(sftp, posixpath.join(root, "boot_id"), 128)
        stopped = await sftp.exists(posixpath.join(root, "stop_requested"))
        stdout = await sftp.stat(posixpath.join(root, "stdout")) if await sftp.exists(posixpath.join(root, "stdout")) else None
        stderr = await sftp.stat(posixpath.join(root, "stderr")) if await sftp.exists(posixpath.join(root, "stderr")) else None

    # This command only acquires (then releases) a lock if no process owns it.
    check = await conn.run("flock -n -E 75 -o " + shlex.quote(root) + " true", check=False)
    if check.exit_status == 75:
        status = "running"
    elif check.exit_status == 0:
        boot = await conn.run("cat /proc/sys/kernel/random/boot_id", check=False)
        if initial_boot and boot.exit_status == 0 and initial_boot.strip() != boot.stdout.strip():
            status = "interrupted"
        elif stopped:
            status = "stop_requested"
        elif time.time() - float(meta.get("created_at", 0)) < 5:
            status = "starting"
        else:
            status = "unknown"
    else:
        status = "unknown"
    # Exit may have been committed while querying the lock; terminal record wins.
    async with conn.start_sftp_client() as sftp:
        code_str = await _read_text(sftp, posixpath.join(root, "exit"), 64)
    if code_str is not None:
        try:
            return {"job_id": job_id, "status": "exited", "exit_code": int(code_str.strip()),
                    "path": root, "created_at": meta.get("created_at")}
        except ValueError:
            status = "unknown"
    return {"job_id": job_id, "status": status, "path": root,
            "created_at": meta.get("created_at"),
            "stdout_bytes": stdout.size if stdout is not None else 0,
            "stderr_bytes": stderr.size if stderr is not None else 0}


async def output_job(conn, job_id: str, stream: str = "stdout", *,
                     offset: int = 0, limit: int = 65536) -> dict:
    if stream not in ("stdout", "stderr", "internal.log"):
        raise ValueError("stream must be stdout, stderr or internal.log")
    root = directory(await remote_uid(conn), job_id)
    async with conn.start_sftp_client() as sftp:
        page = await read_sftp_page(sftp, posixpath.join(root, stream), offset=offset, limit=limit)
    return {"job_id": job_id, "stream": stream, **page}


async def stop_job(conn, job_id: str, grace_seconds: int = 3) -> dict:
    if type(grace_seconds) is not int or not 0 <= grace_seconds <= 30:
        raise ValueError("grace_seconds must be between 0 and 30")
    root = directory(await remote_uid(conn), job_id)
    q = shlex.quote(root)
    # Before signaling a negative PGID, verify the wrapper identity (boot ID,
    # PID and /proc start ticks). Never signal a recycled process group.
    script = (
        f'J={q}; test -d "$J" || {{ echo not_found; exit 0; }}; '
        'test ! -f "$J/exit" || { echo already_exited; exit 0; }; '
        'test -s "$J/pid" && test -s "$J/pgid" && test -s "$J/pid_start_ticks" && '
        'test -s "$J/boot_id" || { echo unknown; exit 0; }; '
        'pid=$(cat "$J/pid"); pgid=$(cat "$J/pgid"); old=$(cat "$J/pid_start_ticks"); '
        'boot=$(cat /proc/sys/kernel/random/boot_id); '
        'test "$boot" = "$(cat "$J/boot_id")" || { echo interrupted; exit 0; }; '
        'case "$pid:$pgid:$old" in *[!0-9:]*|::*|:*) echo unknown; exit 0;; esac; '
        'test -r "/proc/$pid/stat" || { echo unknown; exit 0; }; '
        'now=$(awk \'{print $22}\' "/proc/$pid/stat"); '
        'test "$now" = "$old" || { echo unknown; exit 0; }; '
        'actual=$(ps -o pgid= -p "$pid" | tr -d " "); '
        'test "$actual" = "$pgid" || { echo unknown; exit 0; }; '
        'date +%s > "$J/stop_requested"; '
        'kill -TERM -- "-$pgid" 2>/dev/null || { echo signal_failed; exit 0; }; '
        f'i=0; while [ "$i" -lt {grace_seconds} ]; do '
        'flock -n -E 75 -o "$J" true 2>/dev/null && break; '
        'sleep 1; i=$((i+1)); done; '
        'if flock -n -E 75 -o "$J" true 2>/dev/null; then echo stop_requested; '
        'else kill -KILL -- "-$pgid" 2>/dev/null; echo force_stop_requested; fi'
    )
    result = await conn.run("/bin/sh -c " + shlex.quote(script), check=False)
    if result.exit_status != 0:
        return {"job_id": job_id, "status": "unknown", "detail": result.stderr[:1000]}
    return {"job_id": job_id, "status": result.stdout.strip() or "unknown"}


async def list_jobs(conn, limit: int = 50) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    uid = await remote_uid(conn)
    prefix = f"libersh.{uid}."
    names = []
    async with conn.start_sftp_client() as sftp:
        async for entry in sftp.scandir(job_base()):
            name = entry.filename
            if name.startswith(prefix) and ID_RE.fullmatch(name[len(prefix):]):
                names.append((entry.attrs.mtime or 0, name[len(prefix):]))
    names.sort(reverse=True)
    return {"jobs": [name for _, name in names[:limit]], "total": len(names)}
