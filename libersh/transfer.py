"""Native ChatGPT attachment ingress, streamed into remote SFTP files."""
import hashlib
import posixpath
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field

MAX_UPLOAD_BYTES = 512_000_000


class OpenAIFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_url: str
    file_id: str = Field(min_length=1)
    file_name: str | None = None
    mime_type: str | None = None


async def upload_reference(sftp, reference: OpenAIFile, remote_path: str, *,
                           atomic_replace: bool = False,
                           expected_sha256: str | None = None,
                           transport: httpx.AsyncBaseTransport | None = None) -> dict:
    if not reference.download_url.startswith("https://"):
        raise ValueError("ChatGPT attachment must have an HTTPS download_url")
    if expected_sha256 is not None and (len(expected_sha256) != 64 or
                                        any(c not in "0123456789abcdefABCDEF" for c in expected_sha256)):
        raise ValueError("expected_sha256 must be a 64-digit hex string")
    target = remote_path
    tmp = None
    if atomic_replace:
        tmp = posixpath.join(posixpath.dirname(remote_path) or ".", ".libersh-" + uuid.uuid4().hex + ".tmp")
        target = tmp
    received = 0
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=120,
                                     transport=transport) as http:
            async with http.stream("GET", reference.download_url,
                                   headers={"Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length is not None and int(length) > MAX_UPLOAD_BYTES:
                    raise ValueError("attachment exceeds 512 MB")
                async with sftp.open(target, "xb" if tmp else "wb", block_size=65536) as file:
                    async for chunk in response.aiter_bytes(65536):
                        received += len(chunk)
                        if received > MAX_UPLOAD_BYTES:
                            raise ValueError("attachment exceeds 512 MB")
                        digest.update(chunk)
                        await file.write(chunk)
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            raise ValueError("attachment SHA-256 mismatch")
        if tmp:
            await sftp.posix_rename(tmp, remote_path)
    except BaseException:
        if tmp:
            try:
                await sftp.remove(tmp)
            except OSError:
                pass
        raise
    return {"path": remote_path, "bytes_written": received, "sha256": digest.hexdigest(),
            "atomic_replace": atomic_replace}
