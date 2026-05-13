"""MCP server factory.

Builds a FastMCP application with the tools registered for the current
milestone. The same factory is reused by:
  - stdio transport (M4.1+): for local agents (Claude Desktop / Cursor) that
    spawn the server as a subprocess
  - HTTP+SSE transport (M4.4): for remote agents that just need a URL

Tests construct the server in-process and call tools through MCP's
in-memory client (no actual transport).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import catalog


def build_mcp_server() -> FastMCP:
    """Construct and return a fresh FastMCP server with all tools wired up.

    Build order:
      M4.1 (this milestone): list_templates, list_voices, hello
      M4.2: upload_source, get_asset, download_url
      M4.3: generate_deck (with progress notifications)
    """
    mcp = FastMCP(
        name="edit2ppt",
        instructions=(
            "edit2ppt generates editable Korean-first PowerPoint decks. "
            "Discover templates with `list_templates`, available narration voices "
            "with `list_voices`, then (M4.2+) upload sources and call `generate_deck`."
        ),
    )

    # ---- Tools registered at M4.1 ---------------------------------------

    @mcp.tool(
        name="hello",
        description=(
            "Health check. Returns server identity, supported locales, and the "
            "set of MCP tools available. Useful for quickly verifying a remote "
            "edit2ppt server is reachable and which feature surface it exposes."
        ),
    )
    def hello() -> dict[str, Any]:
        return {
            "service": "edit2ppt",
            "ok": True,
            "tools": [t.name for t in mcp._tool_manager.list_tools()],
        }

    @mcp.tool(
        name="list_templates",
        description=(
            "List the layout templates available on this server. Each entry "
            "carries a name (use it as `template_name` later in generate_deck), "
            "a short summary, and keywords. Defaults to ko-KR-aware ordering."
        ),
    )
    def list_templates(locale: str = "ko-KR") -> dict[str, Any]:
        return {"templates": catalog.list_templates(locale=locale)}

    @mcp.tool(
        name="list_voices",
        description=(
            "List curated Edge-TTS voices. Pass `lang` to filter (e.g. 'ko-KR' for "
            "Korean voices, 'en' for any English voice). Returned voice_id values "
            "can be passed to the narration step of generate_deck."
        ),
    )
    def list_voices(lang: str | None = None) -> dict[str, Any]:
        return {"voices": catalog.list_voices(lang=lang)}

    return mcp


__all__ = ["build_mcp_server"]
