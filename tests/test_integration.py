"""Real SSH protocol + SFTP over loopback, against ephemeral AsyncSSH sshd."""
import asyncio
import base64
import json
import os
import sys

import asyncssh
import pytest
from fastmcp import Client

from libersh.config import connection_options, resolve_target
from libersh.connection import ssh_connection
from libersh.execute import execute
from libersh.files import read_sftp_page, write_sftp
from libersh.jobs import start_job, status_job, output_job, stop_job
from libersh.server import mcp


class PasswordServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "tester" and password == "test-pass"


async def run_shell(process):
    """Test-only SSH exec handler running the supplied command in subprocess."""
    child = await asyncio.create_subprocess_shell(
        process.command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def feed_stdin():
        try:
            while chunk := await process.stdin.read(65536):
                child.stdin.write(chunk.encode() if isinstance(chunk, str) else chunk)
                await child.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            child.stdin.close()

    stdin_task = asyncio.create_task(feed_stdin())
    stdout_task = asyncio.create_task(child.stdout.read())
    stderr_task = asyncio.create_task(child.stderr.read())
    await child.wait()
    stdin_task.cancel()
    await asyncio.gather(stdin_task, return_exceptions=True)
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    process.stdout.write(stdout.decode("utf-8", "replace"))
    process.stderr.write(stderr.decode("utf-8", "replace"))
    process.exit(child.returncode)


@pytest.fixture
async def sshd(monkeypatch, tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    listener = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=PasswordServer,
        server_host_keys=[key], process_factory=run_shell, sftp_factory=True,
    )
    port = listener.get_port()
    monkeypatch.setenv("HOST_LOCAL", json.dumps({
        "host": "127.0.0.1", "username": "tester",
        "password": "test-pass", "port": port,
    }))
    monkeypatch.delenv("SSH_KEYS", raising=False)
    try:
        yield tmp_path
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_exec_and_sftp(sshd):
    async with ssh_connection(resolve_target("HOST_LOCAL")) as conn:
        result = await execute(conn, "printf 'hello'; printf ' bad' >&2; exit 6")
        assert result["status"] == "exited"
        assert result["exit_code"] == 6
        assert result["stdout"]["data"] == "hello"
        assert result["stderr"]["data"] == " bad"
        path = str(sshd / "data.bin")
        async with conn.start_sftp_client() as sftp:
            binary = bytes(range(256)) * 400
            result = await write_sftp(sftp, path, binary)
            assert result["bytes_written"] == len(binary)
            page = await read_sftp_page(sftp, path, offset=500, limit=700)
            assert page["encoding"] == "base64"
            import base64
            assert base64.b64decode(page["data"]) == binary[500:1200]
            assert page["has_more"]
            # Atomic replace is a POSIX OpenSSH extension; server may lack it.


@pytest.mark.asyncio
async def test_remote_job_survives_ssh_disconnect(sshd):
    async with ssh_connection(resolve_target("HOST_LOCAL")) as conn:
        started = await start_job(conn, "echo begin; sleep 1; echo done; exit 3")
        assert started["status"] == "started", started
        job_id = started["job_id"]
    # A brand-new connection must rediscover the same remote task.
    async with ssh_connection(resolve_target("HOST_LOCAL")) as conn:
        first = await status_job(conn, job_id)
        assert first["status"] in ("running", "exited"), first
        for _ in range(40):
            current = await status_job(conn, job_id)
            if current["status"] == "exited":
                break
            await asyncio.sleep(0.1)
        assert current["status"] == "exited", current
        assert current["exit_code"] == 3
        out = await output_job(conn, job_id)
        assert "done" in out["data"]
        duplicate = await start_job(conn, "echo should-not-run", job_id=job_id)
        assert duplicate["already_exists"]


@pytest.mark.asyncio
async def test_mcp_tools_and_native_file_metadata(sshd):
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert {"list_hosts", "exec", "read_file", "write_file", "upload_file",
                "job_start", "job_status", "job_output", "job_stop", "job_list"} <= set(tools)
        for name in ("exec", "stat", "list_dir", "read_file", "write_file",
                     "file_op", "upload_file", "job_start", "job_status",
                     "job_output", "job_stop", "job_list"):
            schema = tools[name].input_schema
            description = schema["properties"]["host"]["description"]
            assert "ssh://username@hostname[:port]" in description, name
            assert "SSH_KEYS" in description, name
            assert "HOST_<NAME>" in description, name
        definition = tools["upload_file"].model_dump(by_alias=True)
        assert (definition.get("_meta") or definition.get("meta"))["openai/fileParams"] == ["file"]
        called = await client.call_tool("exec", {"host": "HOST_LOCAL", "command": "printf hello"})
        data = json.loads(called.content[0].text)
        assert data["status"] == "exited"
        assert data["stdout"]["data"] == "hello"
        invalid = await client.call_tool("exec", {
            "host": "45.32.51.15", "command": "whoami",
        })
        error = json.loads(invalid.content[0].text)
        assert error["error"] == "ConfigurationError"
        assert "ssh://username@hostname[:port]" in error["detail"]
        assert "SSH_KEYS" in error["detail"]

@pytest.mark.asyncio
async def test_json_host_profile_over_ssh(sshd):
    target = resolve_target("HOST_LOCAL")
    assert target.username == "tester"
    assert next(connection_options(target))["known_hosts"] is None
    async with ssh_connection(target) as conn:
        result = await execute(conn, "printf simple-profile")
        assert result["exit_code"] == 0
        assert result["stdout"]["data"] == "simple-profile"
    async with Client(mcp) as client:
        response = await client.call_tool("exec", {
            "host": "HOST_LOCAL", "command": "printf via-mcp",
        })
        data = json.loads(response.content[0].text)
        assert data["stdout"]["data"] == "via-mcp"
        assert "test-pass" not in (await client.call_tool("list_hosts", {})).content[0].text
        keys_tool = next(t for t in await client.list_tools() if t.name == "get_public_keys")
        assert "host" in keys_tool.input_schema["properties"]
        tool = next(t for t in await client.list_tools() if t.name == "exec")
        assert not {"username", "port", "identity"} & set(tool.input_schema["properties"])


class OrderedAuthServer(asyncssh.SSHServer):
    """Record which credentials reach the real SSH server."""
    def __init__(self, public_key, accepted_password, events):
        self.public_key = public_key
        self.accepted_password = accepted_password
        self.events = events

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        public = key.export_public_key().decode().strip()
        self.events.append(("key", public))
        return username == "tester" and public == self.public_key

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        self.events.append(("password", password))
        return username == "tester" and password == self.accepted_password


@pytest.mark.asyncio
async def test_auth_falls_back_host_key_then_password_then_global_keys(monkeypatch):
    specific = asyncssh.generate_private_key("ssh-ed25519")
    wrong_global = asyncssh.generate_private_key("ssh-ed25519")
    good_global = asyncssh.generate_private_key("ssh-ed25519")
    good_public = good_global.export_public_key().decode().strip()
    events = []
    listener = await asyncssh.listen(
        "127.0.0.1", 0,
        server_factory=lambda: OrderedAuthServer(good_public, "never-accepted", events),
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=run_shell,
    )
    try:
        monkeypatch.setenv("SSH_KEYS", json.dumps({
            "wrong": wrong_global.export_private_key().decode(),
            "right": good_global.export_private_key().decode(),
        }))
        monkeypatch.setenv("HOST_ORDERED", json.dumps({
            "host": "127.0.0.1", "username": "tester", "port": listener.get_port(),
            "private_key": specific.export_private_key().decode(),
            "password": "invalid-host-password",
        }))
        async with ssh_connection(resolve_target("HOST_ORDERED")) as conn:
            outcome = await execute(conn, "printf ordered")
        assert outcome["stdout"]["data"] == "ordered"

        tagged = [
            "host-key" if kind == "key" and value == specific.export_public_key().decode().strip()
            else "global-wrong" if kind == "key" and value == wrong_global.export_public_key().decode().strip()
            else "global-right" if kind == "key" and value == good_public
            else "host-password" if kind == "password" else "unknown"
            for kind, value in events
        ]
        assert "host-key" in tagged and "host-password" in tagged
        assert "global-wrong" in tagged and "global-right" in tagged
        assert tagged.index("host-key") < tagged.index("host-password")
        assert tagged.index("host-password") < tagged.index("global-wrong")
        assert tagged.index("global-wrong") < tagged.index("global-right")
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_host_password_wins_before_global_key(monkeypatch):
    good_global = asyncssh.generate_private_key("ssh-ed25519")
    events = []
    listener = await asyncssh.listen(
        "127.0.0.1", 0,
        server_factory=lambda: OrderedAuthServer(
            good_global.export_public_key().decode().strip(), "correct-password", events),
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=run_shell,
    )
    try:
        monkeypatch.setenv("SSH_KEYS", json.dumps({
            "global": good_global.export_private_key().decode(),
        }))
        monkeypatch.setenv("HOST_PASSWORD_FIRST", json.dumps({
            "host": "127.0.0.1", "username": "tester", "port": listener.get_port(),
            "password": "correct-password",
        }))
        async with ssh_connection(resolve_target("HOST_PASSWORD_FIRST")) as conn:
            outcome = await execute(conn, "printf password")
        assert outcome["stdout"]["data"] == "password"
        assert events == [("password", "correct-password")]
        async with Client(mcp) as client:
            result = await client.call_tool("get_public_keys", {"host": "HOST_PASSWORD_FIRST"})
            data = json.loads(result.content[0].text)
            assert len(data["keys"]) == 1
            assert data["keys"][0]["public_key"] == good_global.export_public_key().decode().strip()
            assert "PRIVATE KEY" not in result.content[0].text
        # A broken global vault must not block a successful host password.
        monkeypatch.setenv("SSH_KEYS", "broken-unneeded-configuration")
        async with ssh_connection(resolve_target("HOST_PASSWORD_FIRST")) as conn:
            confirm = await execute(conn, "printf no-global-used")
        assert confirm["stdout"]["data"] == "no-global-used"
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_dynamic_ssh_url_real_key_authentication_and_mcp(monkeypatch, tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    events = []
    listener = await asyncssh.listen(
        "127.0.0.1", 0,
        server_factory=lambda: OrderedAuthServer(
            key.export_public_key().decode().strip(), "not-the-password", events),
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=run_shell, sftp_factory=True,
    )
    try:
        # The real SSH handshake must accept a bare single-line OpenSSH body.
        key_body = "".join(key.export_private_key().decode().splitlines()[1:-1])
        monkeypatch.setenv("SSH_KEYS", json.dumps({"vultr": key_body}))
        # A dynamic host must not use credentials from a matching stored host.
        monkeypatch.setenv("HOST_WRONG", json.dumps({
            "host": "127.0.0.1", "username": "tester",
            "password": "unwanted-password", "port": listener.get_port(),
        }))
        url = f"ssh://tester@127.0.0.1:{listener.get_port()}"
        async with ssh_connection(resolve_target(url)) as conn:
            outcome = await execute(conn, "printf dynamic-ssh")
        assert outcome["stdout"]["data"] == "dynamic-ssh"
        assert events
        assert all(event == ("key", key.export_public_key().decode().strip())
                   for event in events)
        async with Client(mcp) as client:
            command = await client.call_tool("exec", {
                "host": url, "command": "printf dynamic-mcp",
            })
            assert json.loads(command.content[0].text)["stdout"]["data"] == "dynamic-mcp"
            file_path = str(tmp_path / "dynamic.txt")
            written = await client.call_tool("write_file", {
                "host": url, "path": file_path, "data": "from-dynamic-sftp",
            })
            assert json.loads(written.content[0].text)["bytes_written"] == 17
            read = await client.call_tool("read_file", {
                "host": url, "path": file_path,
            })
            assert json.loads(read.content[0].text)["data"] == "from-dynamic-sftp"
        # Public-key authentication may probe the key before verifying its signature.
        assert len(events) >= 4
        assert all(event == ("key", key.export_public_key().decode().strip())
                   for event in events)
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_dynamic_ssh_pkcs8_der_global_key(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    body = base64.b64encode(
        key.export_private_key("pkcs8-der")
    ).decode("ascii")
    events = []
    listener = await asyncssh.listen(
        "127.0.0.1", 0,
        server_factory=lambda: OrderedAuthServer(
            key.export_public_key().decode().strip(), "never", events),
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=run_shell,
    )
    try:
        monkeypatch.setenv("SSH_KEYS", json.dumps({"pkcs8": body}))
        url = f"ssh://tester@127.0.0.1:{listener.get_port()}"
        async with Client(mcp) as client:
            result = await client.call_tool("exec", {
                "host": url, "command": "printf pkcs8-authenticated",
            })
            assert json.loads(result.content[0].text)["stdout"]["data"] == "pkcs8-authenticated"
        assert events
        assert all(event == ("key", key.export_public_key().decode().strip())
                   for event in events)
    finally:
        listener.close()
        await listener.wait_closed()
