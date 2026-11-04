"""Launch exactly one detached remote job for each unique ID."""
import json
import posixpath
import shlex
import time
import uuid
from .job_core import validate_job_id, directory, remote_uid, RUNNER

async def start_job(conn, command: str, *, cwd: str | None = None,
                    job_id: str | None = None) -> dict:
    job_id = validate_job_id(job_id) if job_id is not None else uuid.uuid4().hex
    uid = await remote_uid(conn)
    root = directory(uid, job_id)
    # mkdir is the linearization point: the same job ID is never launched twice.
    created = await conn.run("umask 077; mkdir -- " + shlex.quote(root), check=False)
    if created.exit_status != 0:
        async with conn.start_sftp_client() as sftp:
            if await sftp.isdir(root):
                return {"job_id": job_id, "path": root, "already_exists": True,
                        "status": "existing", "next_tool": "job_status"}
        return {"job_id": job_id, "path": root, "status": "launch_failed",
                "detail": created.stderr}

    meta = {"job_id": job_id, "created_at": time.time(), "cwd": cwd,
            "format_version": 1}
    try:
        async with conn.start_sftp_client() as sftp:
            for name, payload in (
                ("meta.json", json.dumps(meta).encode("utf-8")),
                ("command", command.encode("utf-8")),
                ("runner.sh", RUNNER.encode("utf-8")),
            ):
                async with sftp.open(posixpath.join(root, name), "xb") as file:
                    await file.write(payload)
        quoted = shlex.quote(root)
        cwd_quoted = shlex.quote(cwd or "")
        launcher = (
            "command -v setsid >/dev/null && command -v flock >/dev/null && "
            "command -v nohup >/dev/null || exit 127; "
            f"nohup setsid flock -x -o {quoted} /bin/sh {quoted}/runner.sh {quoted} {cwd_quoted} "
            f"< /dev/null > {quoted}/internal.log 2>&1 & "
            "i=0; while [ ! -s " + quoted + "/pid ] && [ \"$i\" -lt 30 ]; do "
            "sleep 0.1; i=$((i+1)); done; "
            "test -s " + quoted + "/pid"
        )
        # A connection break here may mean the job started. Keep the same job_id
        # in any error so the caller can query it before attempting a new start.
        result = await conn.run("/bin/sh -c " + shlex.quote(launcher), check=False)
        return {"job_id": job_id, "path": root,
                "status": "started" if result.exit_status == 0 else "unknown",
                "already_exists": False, "next_tool": "job_status",
                **({"detail": "Remote launch was not confirmed; query this job ID."}
                   if result.exit_status != 0 else {})}
    except Exception as exc:
        return {"job_id": job_id, "path": root, "status": "unknown",
                "detail": "Remote job setup or launch was interrupted; query this job ID before retrying.",
                "error_type": type(exc).__name__}

