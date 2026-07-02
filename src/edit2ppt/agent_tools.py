"""edit2ppt as a function-calling tool set for LLM agents.

``ANTHROPIC_TOOLS`` is a ready-to-send ``tools=[...]`` list for the
Anthropic Messages API (any function-calling runtime that accepts JSON
Schema works the same way); ``run_tool`` / ``run_tool_async`` dispatch a
tool call to the library facade (:mod:`edit2ppt.simple`) and return a
JSON-safe dict for the tool_result block.

    import anthropic
    from edit2ppt.agent_tools import ANTHROPIC_TOOLS, run_tool

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-opus-4-7", max_tokens=2048,
        tools=ANTHROPIC_TOOLS,
        messages=[{"role": "user", "content": "deck.pptx 3번 슬라이드 제목 바꿔줘"}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            result = run_tool(block.name, block.input)

All tools operate on local file paths. The two LLM-backed tools
(generate/edit) read the Anthropic key from ``api_key=`` on the dispatcher
or ``ANTHROPIC_API_KEY``; the rest are deterministic and keyless.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["ANTHROPIC_TOOLS", "TOOL_NAMES", "run_tool", "run_tool_async"]

_PPTX_PATH = {"type": "string", "description": "Path to a local .pptx file."}

ANTHROPIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": "generate_pptx",
        "description": (
            "Generate a complete, natively editable PowerPoint deck from a "
            "one-line intent (Korean-first; any language works). Optionally "
            "ground it in source documents (PDF/DOCX/PPTX/XLSX/HTML) and/or "
            "reuse an existing PPTX as the design template: "
            "deck_mode='template_restyle' builds a fresh deck on its "
            "masters/theme, 'template_extend' appends new slides after its "
            "existing ones. Slow (multiple LLM calls; typically minutes)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "What the deck is for, e.g. 'Q3 영업 결과 임원 보고'.",
                },
                "output": {
                    "type": "string",
                    "description": "Where to write the resulting .pptx.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional source document paths to ground the content in.",
                },
                "template": {
                    "type": "string",
                    "description": "Optional PPTX path to inherit design from.",
                },
                "deck_mode": {
                    "type": "string",
                    "enum": ["new", "template_restyle", "template_extend"],
                    "description": "How to use the template (default: new).",
                },
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Target [min, max] page count (default [8, 12]).",
                },
                "lang": {"type": "string", "description": "BCP-47, default ko-KR."},
            },
            "required": ["intent", "output"],
        },
    },
    {
        "name": "edit_pptx",
        "description": (
            "Apply one natural-language edit turn to an existing deck: "
            "rewrite/add/delete slides ('3번 슬라이드 제목 바꿔줘', 'add a "
            "roadmap slide after slide 2'). Untouched slides keep their "
            "identity. Question-only instructions are answered in `reply` "
            "without changing the file. Attach reference documents via "
            "`sources` for content-grounded edits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pptx": _PPTX_PATH,
                "instruction": {"type": "string"},
                "output": {
                    "type": "string",
                    "description": "Output path (default: <input>_edited.pptx).",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Reference document paths for this edit.",
                },
                "lang": {"type": "string", "description": "BCP-47, default ko-KR."},
            },
            "required": ["pptx", "instruction"],
        },
    },
    {
        "name": "preview_pptx",
        "description": (
            "Render every slide of a deck to a self-contained SVG file "
            "(masters inlined, images embedded) for visual inspection. "
            "Deterministic, no LLM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pptx": _PPTX_PATH,
                "out_dir": {
                    "type": "string",
                    "description": "Directory for slide_NNN.svg files.",
                },
            },
            "required": ["pptx", "out_dir"],
        },
    },
    {
        "name": "set_pptx_text",
        "description": (
            "Replace exact paragraphs of text in a deck deterministically "
            "(no LLM, instant, formatting preserved). Address paragraphs by "
            "shape_id + para (or table shape_id + row/col + para) as listed "
            "by analyze_pptx. Use this instead of edit_pptx for plain text "
            "swaps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pptx": _PPTX_PATH,
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slide": {"type": "integer", "description": "0-based slide index."},
                            "shape_id": {"type": "integer"},
                            "para": {"type": "integer", "description": "0-based paragraph index."},
                            "new_text": {"type": "string"},
                            "old_text": {
                                "type": "string",
                                "description": "Current text (concurrency guard).",
                            },
                            "row": {"type": "integer", "description": "Table row (with col)."},
                            "col": {"type": "integer", "description": "Table column (with row)."},
                        },
                        "required": ["slide", "shape_id", "para", "new_text"],
                    },
                },
                "output": {
                    "type": "string",
                    "description": "Output path (default: <input>_edited.pptx).",
                },
            },
            "required": ["pptx", "edits"],
        },
    },
    {
        "name": "analyze_pptx",
        "description": (
            "Inspect a deck: page count, canvas size, theme colors/fonts, "
            "and a per-slide text outline where every paragraph carries the "
            "address (shape_id/para or table row/col) that set_pptx_text "
            "needs. Deterministic, no LLM. Call this before editing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"pptx": _PPTX_PATH},
            "required": ["pptx"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in ANTHROPIC_TOOLS]


async def run_tool_async(
    name: str, tool_input: dict[str, Any], *, api_key: str | None = None
) -> dict[str, Any]:
    """Dispatch one tool call; returns a JSON-safe result dict.

    Unknown tools and bad inputs raise ``ValueError`` — catch and convert
    to an error tool_result if your agent loop prefers soft failures.
    """
    from . import simple

    args = dict(tool_input)
    if name == "generate_pptx":
        result = await simple.async_generate_pptx(
            args.pop("intent"),
            output=args.pop("output"),
            api_key=api_key,
            sources=args.pop("sources", None),
            template=args.pop("template", None),
            deck_mode=args.pop("deck_mode", "new"),
            pages=tuple(args.pop("pages", (8, 12))),  # type: ignore[arg-type]
            lang=args.pop("lang", "ko-KR"),
        )
        return {
            "path": str(result.path),
            "page_count": result.page_count,
            "warnings": result.warnings,
        }
    if name == "edit_pptx":
        result = await simple.async_edit_pptx(
            args.pop("pptx"),
            args.pop("instruction"),
            output=args.pop("output", None),
            api_key=api_key,
            sources=args.pop("sources", None),
            lang=args.pop("lang", "ko-KR"),
        )
        return {
            "path": str(result.path),
            "changed": result.changed,
            "reply": result.reply,
            "page_count": result.page_count,
            "operations": result.operations,
        }
    if name == "preview_pptx":
        paths = simple.preview_pptx(args["pptx"], out_dir=args["out_dir"])
        return {"svg_paths": [str(p) for p in paths], "page_count": len(paths)}
    if name == "set_pptx_text":
        result = simple.set_pptx_text(
            args["pptx"], args["edits"], output=args.get("output")
        )
        return {
            "path": str(result.path),
            "applied": result.applied,
            "results": result.results,
        }
    if name == "analyze_pptx":
        return simple.analyze_pptx(args["pptx"])
    raise ValueError(f"unknown edit2ppt tool: {name!r} (known: {TOOL_NAMES})")


def run_tool(
    name: str, tool_input: dict[str, Any], *, api_key: str | None = None
) -> dict[str, Any]:
    """Sync wrapper for :func:`run_tool_async`."""
    return asyncio.run(run_tool_async(name, tool_input, api_key=api_key))
