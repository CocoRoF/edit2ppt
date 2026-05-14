"""Strategize tool: source markdown + intent -> design spec + spec_lock + page plan.

Wraps the Strategist role from `core/prompts/strategist.{lang}.md`. The Strategist
is the first LLM stage in the pipeline ([02-pipeline.md] §2.1 step 4) — it
decides colors / fonts / page count / per-page content outline.

The output design_spec/spec_lock are markdown + YAML strings that downstream
tools (executor, export) consume directly. We don't parse them in this tool —
the Strategist's contract is "structured markdown" and the next stage trusts it.
"""

from __future__ import annotations

import time
from typing import Protocol

from pydantic import Field

from ..llm import AnthropicClient, DEFAULT_MODEL, build_output_lang_directive, load_prompt
from ..llm.anthropic_client import LLMResult, LLMUsage
from .types import (
    CanvasFormat,
    CostBreakdown,
    DEFAULT_CANVAS,
    DEFAULT_LANG,
    LangCode,
    ToolRequest,
    ToolResponse,
    WarningEntry,
)


class StrategizeRequest(ToolRequest):
    # 0 or more source documents (markdown). When empty, the Strategist works
    # from `user_intent` alone — useful for "just generate a deck about X"
    # chat-style flows.
    sources_markdown: list[str] = Field(default_factory=list)
    user_intent: str = Field(..., min_length=1)
    template_name: str | None = None
    target_pages: tuple[int, int] = Field(default=(8, 12))
    canvas_format: CanvasFormat = DEFAULT_CANVAS
    style: str = Field(
        default="general",
        description="general | consultant | consultant-top — selects executor variant downstream.",
    )
    lang: LangCode = DEFAULT_LANG
    model: str = DEFAULT_MODEL
    anthropic_api_key: str = Field(
        ...,
        description="BYOK Anthropic key. Never persisted; only used for this call.",
    )


class StrategizeResponse(ToolResponse):
    raw_output: str = Field(..., description="Full LLM text output (markdown).")
    design_spec: str = Field(..., description="Human-readable design specification.")
    spec_lock: str = Field(..., description="Machine-readable execution contract (YAML).")
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


class LLMCallable(Protocol):
    """Minimal interface the tool needs from the LLM client (for test stubs)."""

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_output_tokens: int = ...,
        temperature: float = ...,
        cache_system: bool = ...,
        model: str | None = ...,
    ) -> LLMResult: ...


async def strategize(
    req: StrategizeRequest,
    *,
    client: LLMCallable | None = None,
) -> StrategizeResponse:
    """Run the Strategist on the given sources.

    `client` is injected for tests; production code lets the tool construct
    a real `AnthropicClient` from `req.anthropic_api_key`.
    """
    started = time.perf_counter()
    warnings: list[WarningEntry] = []

    # English single-source prompt + runtime language directive. The directive
    # tells the model to emit user-facing strings in req.lang while keeping
    # YAML / JSON keys English (Track A).
    system_prompt = build_output_lang_directive(req.lang) + "\n\n" + load_prompt("strategist")
    user_message = _build_user_message(req)

    llm = client or AnthropicClient(api_key=req.anthropic_api_key, model=req.model)
    result = await llm.complete(
        system_prompt=system_prompt,
        user_message=user_message,
        max_output_tokens=8192,
        cache_system=True,
        model=req.model,
    )

    design_spec, spec_lock = _split_output(result.text, warnings)

    return StrategizeResponse(
        raw_output=result.text,
        design_spec=design_spec,
        spec_lock=spec_lock,
        cost=_cost_from_usage(result.usage, time.perf_counter() - started),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_message(req: StrategizeRequest) -> str:
    lines: list[str] = []
    lines.append("# Inputs")
    lines.append(f"- Language: {req.lang}")
    lines.append(f"- Canvas format: {req.canvas_format}")
    lines.append(f"- Style: {req.style}")
    lines.append(f"- Target pages: {req.target_pages[0]}-{req.target_pages[1]}")
    if req.template_name:
        lines.append(f"- Template: {req.template_name}")
    lines.append("")
    lines.append("# User intent")
    lines.append(req.user_intent.strip())
    lines.append("")
    if req.sources_markdown:
        lines.append("# Sources")
        for i, src in enumerate(req.sources_markdown, start=1):
            lines.append(f"## Source {i}")
            lines.append("```markdown")
            lines.append(src.strip())
            lines.append("```")
        lines.append("")
    else:
        # "Topic-only" / chat mode: no source documents — design entirely
        # from the user_intent above. Mirrors ppt-master's standalone
        # topic-research workflow but inline.
        lines.append("# Source material")
        lines.append(
            "No source document was provided. Design this deck from the "
            "User intent above. Use your general knowledge to expand the "
            "topic into a coherent slide-by-slide outline. Do not refuse "
            "or stall waiting for sources — produce a usable design_spec "
            "and spec_lock based on the intent alone."
        )
        lines.append("")
    lines.append("# Output format")
    lines.append(
        "Produce two clearly-fenced sections in this exact order:\n"
        "1. A fenced block labelled `design_spec` containing the human-readable design spec (markdown).\n"
        "2. A fenced block labelled `spec_lock` containing the YAML execution contract."
    )
    return "\n".join(lines)


def _split_output(text: str, warnings: list[WarningEntry]) -> tuple[str, str]:
    """Pull the two fenced blocks out of the LLM response.

    The Strategist prompt asks for ```design_spec ... ``` and ```spec_lock ... ```
    fenced sections. We tolerate small label variations and fall back to the
    raw text if a block is missing (with a warning).
    """
    design_spec = _extract_block(text, ("design_spec", "design-spec"))
    spec_lock = _extract_block(text, ("spec_lock", "spec-lock", "yaml"))

    if design_spec is None:
        warnings.append(
            WarningEntry(
                code="missing_design_spec_block",
                message="Strategist output did not contain a `design_spec` fenced block; returning full text.",
            )
        )
        design_spec = text
    if spec_lock is None:
        warnings.append(
            WarningEntry(
                code="missing_spec_lock_block",
                message="Strategist output did not contain a `spec_lock` fenced block; downstream tools may fail.",
            )
        )
        spec_lock = ""

    return design_spec.strip(), spec_lock.strip()


def _extract_block(text: str, labels: tuple[str, ...]) -> str | None:
    """Find the first ```<label> ... ``` block whose label is in *labels*."""
    fence = "```"
    pos = 0
    while True:
        start = text.find(fence, pos)
        if start == -1:
            return None
        # Read label up to newline.
        header_end = text.find("\n", start + len(fence))
        if header_end == -1:
            return None
        label = text[start + len(fence) : header_end].strip().lower()
        end = text.find(fence, header_end + 1)
        if end == -1:
            return None
        if any(label == lbl or label.startswith(lbl) for lbl in labels):
            return text[header_end + 1 : end]
        pos = end + len(fence)


def _cost_from_usage(usage: LLMUsage, duration_seconds: float) -> CostBreakdown:
    return CostBreakdown(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_seconds=duration_seconds,
    )
