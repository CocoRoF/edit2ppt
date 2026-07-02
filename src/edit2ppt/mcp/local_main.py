"""Console entry point for the zero-infra local MCP server.

    edit2ppt-mcp                      # stdio transport (Claude Desktop 등)
    uvx --from edit2ppt edit2ppt-mcp  # no-install run

Claude Desktop / Cursor config:

    {
      "mcpServers": {
        "edit2ppt": {
          "command": "edit2ppt-mcp",
          "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}
        }
      }
    }
"""

from __future__ import annotations

from .local_server import build_local_mcp_server


def main() -> None:
    build_local_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
