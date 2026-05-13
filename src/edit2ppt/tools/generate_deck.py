"""1-shot orchestrator: sources + intent -> PPTX bytes.

Wires the M2 tool layer end-to-end so a caller can produce a deck with a
single function call:

    result = await generate_deck(GenerateDeckRequest(
        sources=[ConvertRequest(source_type="pdf", content=pdf_bytes)],
        user_intent="Q3 영업 결과 임원 보고",
        target_pages=(8, 12),
        lang="ko-KR",
        anthropic_api_key="sk-ant-...",
    ))

Pipeline stages:
  1. convert    — each source -> markdown (parallel)
  2. strategize — LLM produces design_spec + spec_lock + page_plan
  3. images     — (optional) per-page image acquisition (parallel)
  4. execute    — LLM produces per-page SVG (parallel)
  5. quality    — SVG quality checks (deterministic)
  6. export     — SVGs -> PPTX (deterministic)
  7. narrate    — (optional) speaker notes -> MP3 (parallel)

Each stage emits a `StageEvent` via the `on_event` callback so callers
(workers, MCP servers) can stream progress.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Awaitable, Callable, Literal

from pydantic import Field

from ..llm import AnthropicClient, DEFAULT_MODEL
from .convert import ConvertRequest, ConvertResponse, convert_to_markdown
from .execute import (
    ExecuteBatchRequest,
    ExecutePageRequest,
    ExecutorImage,
    ExecutorStyle,
    execute_batch,
)
from .export import ExportRequest, ExportResponse, SlideInput, export_pptx
from .quality import QualityCheckRequest, QualityCheckResponse, QualitySlide, check_svg_quality
from .strategize import StrategizeRequest, StrategizeResponse, strategize
from .types import (
    CanvasFormat,
    CostBreakdown,
    DEFAULT_CANVAS,
    DEFAULT_LANG,
    LangCode,
    QualityIssue,
    ToolRequest,
    ToolResponse,
    WarningEntry,
)

StageName = Literal[
    "queued",
    "converting",
    "strategizing",
    "acquiring_images",
    "executing_pages",
    "checking_quality",
    "exporting",
    "done",
    "failed",
]


class StageEvent(ToolResponse):
    """Progress event emitted by the orchestrator. Subscribers map to MCP/SSE."""

    stage: StageName
    progress: float = Field(..., ge=0.0, le=1.0)
    message_key: str  # i18n catalog key, e.g. "stages.executing_page"
    message_vars: dict = Field(default_factory=dict)
    page_index: int | None = None


EventCallback = Callable[[StageEvent], Awaitable[None]] | Callable[[StageEvent], None] | None


class GenerateDeckRequest(ToolRequest):
    sources: list[ConvertRequest]
    user_intent: str
    target_pages: tuple[int, int] = (8, 12)
    canvas_format: CanvasFormat = DEFAULT_CANVAS
    style: ExecutorStyle = "general"
    lang: LangCode = DEFAULT_LANG
    template_name: str | None = None
    model: str = DEFAULT_MODEL
    anthropic_api_key: str = Field(..., description="BYOK; never persisted.")
    fail_on_quality_error: bool = True


class GenerateDeckResponse(ToolResponse):
    pptx: bytes
    page_count: int
    spec_lock: str
    design_spec: str
    detected_langs: list[LangCode]
    quality_issues: list[QualityIssue]
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


async def generate_deck(
    req: GenerateDeckRequest,
    *,
    on_event: EventCallback = None,
) -> GenerateDeckResponse:
    """Run the full pipeline. Raises on unrecoverable errors."""
    started = time.perf_counter()
    warnings: list[WarningEntry] = []
    cost = CostBreakdown()

    await _emit(on_event, StageEvent(stage="queued", progress=0.0, message_key="stages.queued"))

    # Stage 1: convert sources (parallel)
    await _emit(
        on_event,
        StageEvent(stage="converting", progress=0.05, message_key="stages.converting"),
    )
    convert_results = await asyncio.gather(
        *(asyncio.to_thread(convert_to_markdown, src) for src in req.sources)
    )
    cost = _merge_cost(cost, *[r.cost for r in convert_results])
    for r in convert_results:
        warnings.extend(r.warnings)
    sources_markdown = [r.markdown for r in convert_results]

    # Stage 2: strategize (LLM)
    await _emit(
        on_event,
        StageEvent(stage="strategizing", progress=0.20, message_key="stages.strategizing"),
    )
    client = AnthropicClient(api_key=req.anthropic_api_key, model=req.model)
    strat: StrategizeResponse = await strategize(
        StrategizeRequest(
            sources_markdown=sources_markdown,
            user_intent=req.user_intent,
            template_name=req.template_name,
            target_pages=req.target_pages,
            canvas_format=req.canvas_format,
            style=req.style,
            lang=req.lang,
            model=req.model,
            anthropic_api_key=req.anthropic_api_key,
        ),
        client=client,
    )
    cost = _merge_cost(cost, strat.cost)
    warnings.extend(strat.warnings)

    page_summaries = _split_page_plan(strat.design_spec, strat.spec_lock)
    if not page_summaries:
        raise RuntimeError(
            "Strategist output did not yield any page summaries; "
            "cannot run executor. Inspect strat.raw_output."
        )

    # Stage 3: (skipping image acquisition in M2 — image_plan parsing arrives in M5)
    # The Executor still receives an empty image list and may inline placeholders.

    # Stage 4: execute pages (parallel, LLM)
    await _emit(
        on_event,
        StageEvent(stage="executing_pages", progress=0.40, message_key="stages.executing_pages"),
    )
    page_reqs: list[ExecutePageRequest] = [
        ExecutePageRequest(
            spec_lock=strat.spec_lock,
            page_index=i,
            page_summary=summary,
            images=[],
            style=req.style,
            lang=req.lang,
            model=req.model,
            anthropic_api_key=req.anthropic_api_key,
        )
        for i, summary in enumerate(page_summaries)
    ]
    exec_batch = await execute_batch(
        ExecuteBatchRequest(spec_lock=strat.spec_lock, pages=page_reqs),
        client=client,
    )
    cost = _merge_cost(cost, exec_batch.cost)
    warnings.extend(exec_batch.warnings)

    # Stage 5: quality check
    await _emit(
        on_event,
        StageEvent(stage="checking_quality", progress=0.80, message_key="stages.checking_quality"),
    )
    quality_slides = [
        QualitySlide(index=p.page_index, name=f"slide_{p.page_index:02d}", svg=p.svg)
        for p in exec_batch.results
    ]
    quality_resp: QualityCheckResponse = check_svg_quality(
        QualityCheckRequest(slides=quality_slides, canvas_format=req.canvas_format)
    )
    cost = _merge_cost(cost, quality_resp.cost)
    if req.fail_on_quality_error and not quality_resp.passed:
        errors = [i for i in quality_resp.issues if i.severity == "error"]
        raise RuntimeError(
            f"Quality check failed with {len(errors)} error(s). "
            "Set fail_on_quality_error=False to export anyway."
        )

    # Stage 6: export
    await _emit(
        on_event,
        StageEvent(stage="exporting", progress=0.92, message_key="stages.exporting"),
    )
    slides = [
        SlideInput(
            index=p.page_index,
            name=f"slide_{p.page_index:02d}",
            svg=p.svg,
            notes=p.speaker_notes or None,
        )
        for p in exec_batch.results
    ]
    export_resp: ExportResponse = export_pptx(
        ExportRequest(
            slides=slides,
            canvas_format=req.canvas_format,
            lang=req.lang,
        )
    )
    cost = _merge_cost(cost, export_resp.cost)
    warnings.extend(export_resp.warnings)

    cost = CostBreakdown(
        input_tokens=cost.input_tokens,
        output_tokens=cost.output_tokens,
        cache_read_tokens=cost.cache_read_tokens,
        cache_write_tokens=cost.cache_write_tokens,
        image_count=cost.image_count,
        audio_seconds=cost.audio_seconds,
        duration_seconds=time.perf_counter() - started,
    )

    await _emit(on_event, StageEvent(stage="done", progress=1.0, message_key="stages.done"))

    return GenerateDeckResponse(
        pptx=export_resp.pptx,
        page_count=export_resp.page_count,
        spec_lock=strat.spec_lock,
        design_spec=strat.design_spec,
        detected_langs=export_resp.detected_langs,
        quality_issues=quality_resp.issues,
        cost=cost,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PAGE_HEADING_RE = re.compile(r"^##\s+(?:Page|페이지|Slide)\s+\d+", re.MULTILINE | re.IGNORECASE)


def _split_page_plan(design_spec: str, spec_lock: str) -> list[str]:
    """Extract per-page content summaries from the Strategist's output.

    The prompt asks for `## Page N` / `## Slide N` / `## 페이지 N` sections
    inside the design_spec. We split on those markers; if none are present,
    fall back to splitting spec_lock on its `pages:` block (best-effort).
    """
    if _PAGE_HEADING_RE.search(design_spec):
        # Split into chunks starting at each heading.
        positions = [m.start() for m in _PAGE_HEADING_RE.finditer(design_spec)]
        positions.append(len(design_spec))
        return [design_spec[positions[i] : positions[i + 1]].strip() for i in range(len(positions) - 1)]

    # Fallback: hunt for YAML page entries in spec_lock.
    chunks: list[str] = []
    in_pages = False
    current: list[str] = []
    for line in spec_lock.splitlines():
        stripped = line.rstrip()
        if not in_pages and stripped.strip().lower().startswith("pages:"):
            in_pages = True
            continue
        if in_pages:
            if stripped and not stripped.startswith((" ", "\t", "-")):
                # Left the pages block.
                if current:
                    chunks.append("\n".join(current).strip())
                break
            if stripped.startswith("-"):
                if current:
                    chunks.append("\n".join(current).strip())
                current = [stripped]
            else:
                current.append(stripped)
    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c]


async def _emit(callback: EventCallback, event: StageEvent) -> None:
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


def _merge_cost(base: CostBreakdown, *others: CostBreakdown) -> CostBreakdown:
    inp = base.input_tokens
    out = base.output_tokens
    cr = base.cache_read_tokens
    cw = base.cache_write_tokens
    ic = base.image_count
    aud = base.audio_seconds
    dur = base.duration_seconds
    for c in others:
        inp += c.input_tokens
        out += c.output_tokens
        cr += c.cache_read_tokens
        cw += c.cache_write_tokens
        ic += c.image_count
        aud += c.audio_seconds
        # don't sum duration — orchestrator tracks wall-clock separately
    return CostBreakdown(
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
        image_count=ic,
        audio_seconds=aud,
        duration_seconds=dur,
    )
