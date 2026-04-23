from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm-diet-shadow")


@mcp.tool()
def read_file(path: str) -> str:
    """Read and return the contents of a file at the given path."""
    return Path(path).read_text(encoding="utf-8")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
