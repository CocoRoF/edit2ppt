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
from ._image_plan import ImagePlanItem, parse_image_plan
from .audio import NarrateRequest, NarrateSlide, narrate_async
from .convert import ConvertRequest, ConvertResponse, convert_to_markdown
from .execute import (
    ExecuteBatchRequest,
    ExecutePageRequest,
    ExecutorImage,
    ExecutorStyle,
    execute_batch,
)
from .export import ExportRequest, ExportResponse, SlideInput, export_pptx
from .images import (
    GenerateImageRequest,
    SearchImageRequest,
    generate_image,
    search_image,
)
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
    "narrating",
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

    # BYOK keys for image acquisition. Map of provider env-var names to keys,
    # e.g. {"OPENAI_API_KEY": "sk-...", "PEXELS_API_KEY": "..."}. The keys are
    # exported as env vars only for the duration of each image call.
    image_api_keys: dict[str, str] = Field(default_factory=dict)

    # Defaults for the image plan when the Strategist's spec_lock leaves them
    # implicit. Both can be overridden per-page via the spec_lock image entry.
    default_image_backend: str = "openai"
    default_search_providers: list[str] = Field(
        default_factory=lambda: ["pexels", "pixabay"]
    )

    # Skip the image acquisition stage entirely (text-only deck). Useful for
    # tests / low-cost runs.
    skip_images: bool = False

    # Narration / audio.
    narrate: bool = Field(
        default=False,
        description=(
            "When true, synthesize per-slide speaker notes with Edge-TTS and "
            "embed the resulting MP3s into the PPTX so PowerPoint auto-plays "
            "them on slide entry."
        ),
    )
    narration_voice: str | None = Field(
        default=None,
        description="Edge-TTS ShortName. None -> lang's default voice (ko-KR -> SunHi).",
    )
    narration_rate: str = Field(default="+0%", description='Speaking rate, e.g. "+0%", "-10%".')
    narration_use_timings: bool = Field(
        default=False,
        description=(
            "If true, slide auto-advance times derive from each MP3's duration "
            "(plus narration_padding). Pairs with `narrate=True`."
        ),
    )
    narration_padding: float = Field(default=0.5, ge=0.0)


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

    # Stage 3: image acquisition. Parse the Strategist's image plan, fetch
    # each image (AI-generated or web-searched), and bundle the bytes for
    # both the Executor (so it can reference them by placeholder) and the
    # Export stage (so it can drop the files alongside the SVGs in the
    # workspace).
    images_by_page: dict[int, list[ExecutorImage]] = {}
    image_bytes_by_filename: dict[str, bytes] = {}
    if not req.skip_images:
        plan = parse_image_plan(strat.spec_lock)
        if plan:
            await _emit(
                on_event,
                StageEvent(
                    stage="acquiring_images",
                    progress=0.30,
                    message_key="stages.acquiring_images",
                ),
            )
            for item in plan:
                try:
                    image_bytes, mime, ack = await asyncio.to_thread(
                        _acquire_image,
                        item,
                        req,
                    )
                except Exception as exc:
                    warnings.append(
                        WarningEntry(
                            code="image_acquisition_failed",
                            message=(
                                f"Page {item.page_index} image "
                                f"{item.placeholder!r}: {exc}"
                            ),
                            detail={"page_index": item.page_index, "placeholder": item.placeholder},
                        )
                    )
                    continue

                ext = _ext_for_mime(mime)
                filename = f"{item.placeholder}{ext}"
                image_bytes_by_filename[filename] = image_bytes
                images_by_page.setdefault(item.page_index, []).append(
                    ExecutorImage(
                        placeholder=item.placeholder,
                        url=filename,  # relative path the SVG will reference
                        description=item.description or ack,
                    )
                )
                cost = CostBreakdown(
                    input_tokens=cost.input_tokens,
                    output_tokens=cost.output_tokens,
                    cache_read_tokens=cost.cache_read_tokens,
                    cache_write_tokens=cost.cache_write_tokens,
                    image_count=cost.image_count + 1,
                    audio_seconds=cost.audio_seconds,
                    duration_seconds=cost.duration_seconds,
                )

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
            images=images_by_page.get(i, []),
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

    # Stage 6: prepare slide inputs (shared by audio + export below).
    slides = [
        SlideInput(
            index=p.page_index,
            name=f"slide_{p.page_index:02d}",
            svg=p.svg,
            notes=p.speaker_notes or None,
        )
        for p in exec_batch.results
    ]

    # Stage 7: (optional) narration — synthesize MP3 per slide BEFORE export
    # so the engine can embed audio into the PPTX. Failures here flow to
    # warnings and the deck still exports without audio.
    narration_bytes_by_slide: dict[str, bytes] = {}
    if req.narrate:
        narratable = [
            NarrateSlide(
                index=s.index,
                name=s.name,
                notes_markdown=s.notes or "",
            )
            for s in slides
            if s.notes
        ]
        if narratable:
            await _emit(
                on_event,
                StageEvent(stage="narrating", progress=0.88, message_key="stages.narrating"),
            )
            try:
                narration_resp = await narrate_async(
                    NarrateRequest(
                        slides=narratable,
                        lang=req.lang,
                        voice=req.narration_voice,
                        rate=req.narration_rate,
                    )
                )
                for audio in narration_resp.audios:
                    narration_bytes_by_slide[audio.name] = audio.mp3
                cost = CostBreakdown(
                    input_tokens=cost.input_tokens,
                    output_tokens=cost.output_tokens,
                    cache_read_tokens=cost.cache_read_tokens,
                    cache_write_tokens=cost.cache_write_tokens,
                    image_count=cost.image_count,
                    audio_seconds=cost.audio_seconds + narration_resp.cost.audio_seconds,
                    duration_seconds=cost.duration_seconds,
                )
                warnings.extend(narration_resp.warnings)
            except Exception as exc:
                warnings.append(
                    WarningEntry(
                        code="narration_failed",
                        message=(
                            f"Narration synthesis failed: {exc}. "
                            "Deck exports without audio."
                        ),
                    )
                )

    # Stage 8: export
    await _emit(
        on_event,
        StageEvent(stage="exporting", progress=0.92, message_key="stages.exporting"),
    )
    export_resp: ExportResponse = export_pptx(
        ExportRequest(
            slides=slides,
            canvas_format=req.canvas_format,
            lang=req.lang,
            images=image_bytes_by_filename,
            narration_audio=narration_bytes_by_slide,
            narration_padding=req.narration_padding,
            use_narration_timings=req.narration_use_timings,
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

def _acquire_image(
    item: ImagePlanItem,
    req: "GenerateDeckRequest",
) -> tuple[bytes, str, str | None]:
    """Resolve a single ImagePlanItem to (bytes, mime, ack-text).

    Generate path: dispatches to `tools.images.generate_image`.
    Search path:   dispatches to `tools.images.search_image`.

    The third return value is an acknowledgment / attribution string the
    Executor can show as a credit line ("Photo: …") when required.
    """
    if item.mode == "generate":
        prompt = item.prompt or item.description or ""
        if not prompt:
            raise ValueError(f"image plan item {item.placeholder!r} has no prompt")
        backend = item.backend or req.default_image_backend
        result = generate_image(
            GenerateImageRequest(
                prompt=prompt,
                backend=backend,
                aspect_ratio=item.aspect_ratio,
                api_keys=req.image_api_keys,
            )
        )
        return result.image, result.mime_type, None

    if item.mode == "search":
        query = item.query or item.description or ""
        if not query:
            raise ValueError(f"image plan item {item.placeholder!r} has no query")
        providers = item.providers or req.default_search_providers
        result = search_image(
            SearchImageRequest(
                query=query,
                providers=providers,
                aspect_ratio=item.aspect_ratio,
                api_keys=req.image_api_keys,
            )
        )
        ack = None
        if result.attribution:
            ack = f"사진: {result.attribution}" if "Korean" in str(item.description or "") or result.license == "CC BY" else f"Photo: {result.attribution}"
        return result.image, result.mime_type, ack

    raise ValueError(f"unknown image plan mode {item.mode!r}")


def _ext_for_mime(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type, ".png")


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
