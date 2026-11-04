"""MCP response encoding and conservative transport budgeting."""
import base64
import json

# Limit text payloads well below Horizon's 4 MiB target after JSON/MCP escaping.
MAX_RAW_PAGE = 2 * 1024 * 1024
DEFAULT_PAGE = 64 * 1024


def encode_bytes(data: bytes) -> dict:
    try:
        content = data.decode("utf-8")
        return {"encoding": "utf-8", "data": content}
    except UnicodeDecodeError:
        return {"encoding": "base64", "data": base64.b64encode(data).decode("ascii")}


def page_payload(data: bytes, offset: int, total: int) -> dict:
    encoded = encode_bytes(data)
    return {
        **encoded, "offset": offset, "returned_bytes": len(data),
        "next_offset": offset + len(data), "total_bytes": total,
        "has_more": offset + len(data) < total,
    }


def limit_bytes(value: int, maximum: int = MAX_RAW_PAGE) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"limit_bytes must be between 1 and {maximum}")
    return value


def format_result(payload: dict) -> str:
    """Use one MCP text entry, with an explicit serialization budget."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(json.dumps({"text": raw}, ensure_ascii=False).encode("utf-8")) >= 4 * 1024 * 1024:
        raise ValueError("Response exceeds MCP budget; request a smaller page")
    return raw
