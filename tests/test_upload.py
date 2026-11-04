"""Verify ChatGPT attachment bytes reach a real SFTP server without Base64."""
import hashlib

import asyncssh
import httpx
import pytest

from libersh.transfer import OpenAIFile, upload_reference


class TestSSHServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "tester" and password == "test-pass"


@pytest.mark.asyncio
async def test_stream_attachment_to_real_sftp(tmp_path):
    listener = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=TestSSHServer,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        sftp_factory=True,
    )
    content = bytes(range(256)) * 1024
    requested = []

    async def fetch(request):
        requested.append(str(request.url))
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=httpx.ByteStream(content),
                              headers={"Content-Length": str(len(content))})

    reference = OpenAIFile(file_id="test-file", download_url="https://files.example.test/download")
    dest = tmp_path / "uploaded.bin"
    try:
        async with asyncssh.connect(
            "127.0.0.1", port=listener.get_port(), username="tester",
            password="test-pass", known_hosts=None,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                report = await upload_reference(
                    sftp, reference, str(dest), transport=httpx.MockTransport(fetch))
                assert report["bytes_written"] == len(content)
                assert report["sha256"] == hashlib.sha256(content).hexdigest()
                async with sftp.open(str(dest), "rb") as reader:
                    assert await reader.read() == content
                with pytest.raises(ValueError, match="SHA-256"):
                    await upload_reference(
                        sftp, reference, str(dest), atomic_replace=True,
                        expected_sha256="0" * 64, transport=httpx.MockTransport(fetch))
                async with sftp.open(str(dest), "rb") as reader:
                    assert await reader.read() == content
    finally:
        listener.close()
        await listener.wait_closed()
    assert requested == ["https://files.example.test/download"] * 2