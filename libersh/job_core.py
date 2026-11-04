"""Linux job identity, paths and POSIX process wrapper."""
import posixpath
import re
import asyncssh

JOB_BASE = "/var/tmp"
ID_RE = re.compile(r"^[0-9a-f]{32}$")

RUNNER = r'''#!/bin/sh
# Runs inside: setsid flock -x --close <job-dir> sh <runner> <job-dir> <cwd>
J=$1
D=$2
cat /proc/sys/kernel/random/boot_id > "$J/boot_id" 2>/dev/null
printf '%s\n' "$$" > "$J/pid"
ps -o pgid= -p "$$" | tr -d ' ' > "$J/pgid"
awk '{print $22}' "/proc/$$/stat" > "$J/pid_start_ticks"
if [ -n "$D" ] && ! cd -- "$D"; then
    printf 'Cannot change directory to %s\n' "$D" > "$J/stderr"
    rc=125
else
    /bin/sh "$J/command" > "$J/stdout" 2> "$J/stderr"
    rc=$?
fi
printf '%s\n' "$rc" > "$J/exit.tmp.$$" && mv -f -- "$J/exit.tmp.$$" "$J/exit"
'''


def validate_job_id(job_id: str) -> str:
    if not ID_RE.fullmatch(job_id):
        raise ValueError("job_id must be a 32-character lowercase hex string")
    return job_id


def directory(uid: int, job_id: str) -> str:
    validate_job_id(job_id)
    return posixpath.join(JOB_BASE, f"libersh.{uid}.{job_id}")


async def remote_uid(conn) -> int:
    result = await conn.run("id -u", check=True)
    value = result.stdout.strip()
    if not value.isascii() or not value.isdigit():
        raise RuntimeError("SSH host did not return a numeric UID")
    return int(value)


async def _read_text(sftp, path: str, limit: int = 65536) -> str | None:
    try:
        async with sftp.open(path, "rb") as file:
            data = await file.read(limit)
        return data.decode("utf-8")
    except (FileNotFoundError, asyncssh.SFTPNoSuchFile, UnicodeError):
        return None

