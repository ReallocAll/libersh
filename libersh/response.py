"""MCP response encoding and byte-accurate JSON-RPC transport budgeting."""
import base64
import json

MAX_RAW_PAGE = 2 * 1024 * 1024
DEFAULT_PAGE = 64 * 1024
# Keep 8 KiB beneath the 4 MiB target for transport-specific framing.
MAX_MCP_RESPONSE_BYTES = 4 * 1024 * 1024 - 8192


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


def framed_size(payload: dict) -> int:
    """Measure complete JSON-RPC response with exactly one MCP text entry."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    envelope = {
        "jsonrpc": "2.0", "id": "0" * 64,
        "result": {"content": [{"type": "text", "text": raw}], "isError": False},
    }
    return len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def fit_page_payload(data: bytes, offset: int, total: int, requested_bytes: int,
                     metadata: dict | None = None) -> dict:
    """Fit a file/log page, including its metadata, into the MCP response budget."""
    requested = len(data)

    def candidate(count: int) -> tuple[dict, int]:
        chunk = data[:count]
        # Do not split a valid UTF-8 character at the end of the page.
        # Invalid byte sequences elsewhere remain Base64, preserving every byte.
        try:
            chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data" and exc.start > 0:
                chunk = chunk[:exc.start]
        payload = {
            **(metadata or {}), **page_payload(chunk, offset, total),
            "requested_bytes": requested_bytes,
            "size_limit_adjusted": len(chunk) < requested,
            "mcp_response_bytes": 0,
        }
        if len(chunk) < requested:
            payload["size_limit_reason"] = (
                "mcp_response_budget" if count < requested else "utf8_boundary"
            )
        while True:
            size = framed_size(payload)
            if payload["mcp_response_bytes"] == size:
                return payload, size
            payload["mcp_response_bytes"] = size

    result, size = candidate(requested)
    if size < MAX_MCP_RESPONSE_BYTES:
        return result

    low, high = 1, requested - 1
    best = None
    while low <= high:
        mid = (low + high) // 2
        trial, size = candidate(mid)
        if size < MAX_MCP_RESPONSE_BYTES and trial["returned_bytes"] > 0:
            best = trial
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        raise ValueError("Page metadata exceeds MCP response budget")
    return best


def limit_bytes(value: int, maximum: int = MAX_RAW_PAGE) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"limit_bytes must be between 1 and {maximum}")
    return value


def format_result(payload: dict) -> str:
    """Return one MCP text entry only if its complete envelope fits."""
    if framed_size(payload) >= MAX_MCP_RESPONSE_BYTES:
        raise ValueError("Response exceeds MCP budget; request a smaller page")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
