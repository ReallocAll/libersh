"""Execute the actual Linux runner without an SSHD. Useful for fast protocol checks."""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from libersh.jobs import RUNNER, directory, validate_job_id


@pytest.mark.skipif(shutil.which("flock") is None or shutil.which("setsid") is None,
                    reason="Linux util-linux required")
def test_runner_lock_and_exit(tmp_path, monkeypatch):
    root = tmp_path / "job"
    root.mkdir(mode=0o700)
    (root / "command").write_text("echo begin; sleep 0.5; echo err >&2; exit 7\n")
    (root / "runner.sh").write_text(RUNNER)
    with (root / "internal.log").open("wb") as internal:
        proc = subprocess.Popen(
            ["setsid", "flock", "-x", "-o", str(root), "sh", str(root / "runner.sh"), str(root), ""],
            start_new_session=False, stdin=subprocess.DEVNULL, stdout=internal, stderr=internal,
        )
        for _ in range(50):
            if (root / "pid").exists() and (root / "stdout").exists():
                break
            time.sleep(0.02)
        assert (root / "pid").exists(), (root / "internal.log").read_text()
        locked = subprocess.run(["flock", "-n", "-E", "75", "-o", str(root), "true"])
        assert locked.returncode == 75
        proc.wait(timeout=4)
    assert (root / "exit").read_text().strip() == "7"
    assert "begin" in (root / "stdout").read_text()
    assert "err" in (root / "stderr").read_text()
    assert subprocess.run(["flock", "-n", "-E", "75", "-o", str(root), "true"]).returncode == 0


def test_job_id_and_default_directory(monkeypatch):
    assert validate_job_id("a" * 32) == "a" * 32
    with pytest.raises(ValueError):
        validate_job_id("../etc")
    monkeypatch.setenv("LIBERSH_JOB_BASE", "/var/tmp")
    assert directory(1000, "b" * 32) == "/var/tmp/libersh.1000." + "b" * 32
