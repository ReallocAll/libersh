"""Prefect Horizon entrypoint: mcp_server.py:mcp."""
import os
from libersh.server import mcp

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), stateless_http=True)
