import base64
import json
import pytest
from libersh.response import encode_bytes, format_result, page_payload
from libersh.files import decode_payload


def test_binary_and_utf8():
    binary = b"\x00\xff\xfeHello"
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
    with pytest.raises(ValueError):
        format_result({"x": "\x00" * 800_000})
