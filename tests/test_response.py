import base64
import json
import pytest
from libersh.response import (
    MAX_MCP_RESPONSE_BYTES, encode_bytes, fit_page_payload, format_result,
    framed_size, page_payload,
)
from libersh.files import decode_payload


def test_binary_and_utf8():
    binary = bytes([0, 255, 254]) + b"Hello"
    info = encode_bytes(binary)
    assert info["encoding"] == "base64"
    assert base64.b64decode(info["data"]) == binary
    assert decode_payload(info["data"], "base64") == binary
    assert encode_bytes("文字".encode())["encoding"] == "utf-8"
    assert page_payload(b"abc", 1, 8)["next_offset"] == 4
    with pytest.raises(ValueError):
        decode_payload("!", "base64")


def test_bounded_response():
    assert json.loads(format_result({"x": "hello"}))["x"] == "hello"
    assert framed_size({"x": "hello"}) < MAX_MCP_RESPONSE_BYTES
    with pytest.raises(ValueError):
        format_result({"x": "\\x00" * 800_000})


def test_page_auto_shrinks_to_final_envelope():
    source = bytes(800_000)
    first = fit_page_payload(source, 0, len(source), len(source), {"path": "/stdout"})
    assert first["size_limit_adjusted"]
    assert first["size_limit_reason"] == "mcp_response_budget"
    assert first["encoding"] == "utf-8"
    assert 0 < first["mcp_response_bytes"] < MAX_MCP_RESPONSE_BYTES
    assert first["mcp_response_bytes"] == framed_size(first)
    assert first["next_offset"] == first["returned_bytes"]
    second = fit_page_payload(
        source[first["next_offset"]:], first["next_offset"], len(source),
        len(source) - first["next_offset"], {"path": "/stdout"},
    )
    assert not second["has_more"]
    assert (first["data"] + second["data"]).encode() == source
    assert second["mcp_response_bytes"] == framed_size(second)


def test_binary_page_and_utf8_boundaries():
    binary = bytes([255]) * (2 * 1024 * 1024)
    page = fit_page_payload(binary, 0, len(binary), len(binary), {"path": "/binary"})
    assert page["encoding"] == "base64"
    assert not page["size_limit_adjusted"]
    assert base64.b64decode(page["data"]) == binary
    assert framed_size(page) < MAX_MCP_RESPONSE_BYTES
    utf8 = "猫猫猫".encode("utf-8")
    first = fit_page_payload(utf8[:8], 0, len(utf8), 8, {"path": "/utf8"})
    assert first["encoding"] == "utf-8"
    assert first["returned_bytes"] == 6
    assert first["size_limit_reason"] == "utf8_boundary"
    second = fit_page_payload(utf8[6:], 6, len(utf8), 8, {"path": "/utf8"})
    assert first["data"] + second["data"] == "猫猫猫"


def test_page_budget_includes_job_metadata():
    source = bytes(800_000)
    metadata = {"path": "/var/tmp/log", "job_id": "f" * 32, "stream": "stdout"}
    page = fit_page_payload(source, 0, len(source), len(source), metadata)
    assert page["job_id"] == metadata["job_id"]
    assert page["stream"] == "stdout"
    assert page["mcp_response_bytes"] == framed_size(page)
    assert page["mcp_response_bytes"] < MAX_MCP_RESPONSE_BYTES
