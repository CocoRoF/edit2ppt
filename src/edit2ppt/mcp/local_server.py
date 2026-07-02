"""Zero-infra MCP server: edit2ppt tools over LOCAL files, stdio transport.

Unlike ``edit2ppt.mcp.server`` (the hosted multi-tenant server with asset
storage and a database), this server needs nothing but the pip package —
tools read and write .pptx files on the local filesystem directly, which
is exactly what desktop agents (Claude Desktop / Claude Code / Cursor)
want:

    { "mcpServers": { "edit2ppt": {
        "command": "edit2ppt-mcp",
        "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}
    } } }

The LLM-backed tools (generate/edit) take the key from ``api_key`` or the
``ANTHROPIC_API_KEY`` env var; preview / set_text / analyze are
deterministic and keyless.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP


def build_local_mcp_server() -> FastMCP:
    mcp = FastMCP(
        "edit2ppt",
        instructions=(
            "Generate, chat-edit, preview and text-edit PowerPoint (.pptx) "
            "files on the local filesystem. Korean-first, natively editable "
            "output. Call analyze_pptx first when you need paragraph "
            "addresses for set_pptx_text."
        ),
    )

    from .. import simple

    @mcp.tool(
        name="generate_pptx",
        description=(
            "Generate a complete, natively editable PPTX from a one-line "
            "intent. Optional: ground it in source documents (PDF/DOCX/"
            "PPTX/XLSX/HTML paths) and/or inherit design from an existing "
            "PPTX via template + deck_mode ('template_restyle' = fresh deck "
            "on its masters/theme, 'template_extend' = append after its "
            "slides). Slow — multiple LLM calls, typically minutes."
        ),
    )
    async def generate_pptx_tool(
        intent: str,
        output: str,
        sources: list[str] | None = None,
        template: str | None = None,
        deck_mode: str = "new",
        pages: tuple[int, int] = (8, 12),
        lang: str = "ko-KR",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        result = await simple.async_generate_pptx(
            intent,
            output=output,
            api_key=api_key,
            sources=sources,
            template=template,
            deck_mode=deck_mode,
            pages=pages,
            lang=lang,
        )
        return {
            "path": str(result.path),
            "page_count": result.page_count,
            "warnings": result.warnings,
        }

    @mcp.tool(
        name="edit_pptx",
        description=(
            "Apply one natural-language edit turn to an existing local deck "
            "('3번 슬라이드 제목 바꿔줘', 'add a roadmap slide after slide "
            "2', 'delete the last slide'). Untouched slides keep their "
            "identity. Question-only instructions answer in `reply` without "
            "changing the file. Attach reference docs via `sources`."
        ),
    )
    async def edit_pptx_tool(
        pptx: str,
        instruction: str,
        output: str | None = None,
        sources: list[str] | None = None,
        lang: str = "ko-KR",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        result = await simple.async_edit_pptx(
            pptx,
            instruction,
            output=output,
            api_key=api_key,
            sources=sources,
            lang=lang,
        )
        return {
            "path": str(result.path),
            "changed": result.changed,
            "reply": result.reply,
            "page_count": result.page_count,
            "operations": result.operations,
        }

    @mcp.tool(
        name="preview_pptx",
        description=(
            "Render every slide of a local deck to self-contained SVG files "
            "(slide_NNN.svg in out_dir) for visual inspection. "
            "Deterministic, no LLM, no key."
        ),
    )
    async def preview_pptx_tool(pptx: str, out_dir: str) -> dict[str, Any]:
        paths = simple.preview_pptx(pptx, out_dir=out_dir)
        return {"svg_paths": [str(p) for p in paths], "page_count": len(paths)}

    @mcp.tool(
        name="set_pptx_text",
        description=(
            "Replace exact text paragraphs deterministically (instant, no "
            "LLM, formatting preserved). Address paragraphs by shape_id + "
            "para — or table shape_id + row/col + para — as returned by "
            "analyze_pptx. Prefer this over edit_pptx for plain text swaps."
        ),
    )
    async def set_pptx_text_tool(
        pptx: str,
        edits: list[dict],
        output: str | None = None,
    ) -> dict[str, Any]:
        result = simple.set_pptx_text(pptx, edits, output=output)
        return {
            "path": str(result.path),
            "applied": result.applied,
            "results": result.results,
        }

    @mcp.tool(
        name="analyze_pptx",
        description=(
            "Inspect a local deck: page count, canvas size, theme "
            "colors/fonts, and a per-slide text outline where every "
            "paragraph carries the address set_pptx_text needs. "
            "Deterministic, no LLM, no key. Call before editing."
        ),
    )
    async def analyze_pptx_tool(pptx: str) -> dict[str, Any]:
        return simple.analyze_pptx(pptx)

    return mcp
