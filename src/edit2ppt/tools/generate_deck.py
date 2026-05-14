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
import logging
import re
import time

logger = logging.getLogger(__name__)
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
    # 0 or more source documents. When empty, the Strategist designs from
    # `user_intent` alone — the topic-only / "just chat" path.
    sources: list[ConvertRequest] = Field(default_factory=list)
    user_intent: str = Field(..., min_length=1)
    target_pages: tuple[int, int] = (8, 12)
    canvas_format: CanvasFormat = DEFAULT_CANVAS
    style: ExecutorStyle = "general"
    lang: LangCode = DEFAULT_LANG
    template_name: str | None = None
    model: str = DEFAULT_MODEL
    anthropic_api_key: str = Field(..., description="BYOK; never persisted.")
    fail_on_quality_error: bool = True

    # When > 0, pages flagged as quality errors are re-run that many times
    # with an extra "the previous SVG had errors; emit something simpler"
    # hint appended to their page_summary. Pairs well with
    # fail_on_quality_error=True to attempt recovery before giving up.
    retry_pages_on_quality_error: int = Field(default=0, ge=0, le=3)

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

    # Stage 1: convert sources (parallel). Skipped entirely when the
    # caller didn't supply any — the Strategist works from user_intent alone.
    sources_markdown: list[str] = []
    if req.sources:
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

    page_summaries = _split_page_plan(
        strat.design_spec,
        strat.spec_lock,
        raw_output=strat.raw_output,
    )
    if not page_summaries:
        # Surface enough of the Strategist response to actually diagnose
        # the failure. Cap each section at ~4 KB to keep logs readable.
        headings = _all_markdown_headings(strat.design_spec)
        logger.error(
            "Strategist output did not yield any page summaries.\n"
            "design_spec length=%d, spec_lock length=%d.\n"
            "markdown headings in design_spec (truncated to first 40):\n%s\n"
            "spec_lock (first 2 KB):\n%s\n"
            "design_spec (last 2 KB):\n%s",
            len(strat.design_spec or ""),
            len(strat.spec_lock or ""),
            "\n".join(headings[:40]) or "<none>",
            (strat.spec_lock or "")[:2000],
            (strat.design_spec or "")[-2000:],
        )
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

    # Stage 5: quality check (with optional retry-on-error)
    await _emit(
        on_event,
        StageEvent(stage="checking_quality", progress=0.80, message_key="stages.checking_quality"),
    )
    page_results = {p.page_index: p for p in exec_batch.results}
    quality_resp = _run_quality_check(page_results, req.canvas_format)
    cost = _merge_cost(cost, quality_resp.cost)

    # Per-page retry loop. Each round re-runs only the pages flagged as
    # `quality_error`. Stops when no errors remain or the retry budget is
    # exhausted. Failures are still reported as warnings on the final
    # response — `fail_on_quality_error=True` is checked AFTER retries.
    retries_left = req.retry_pages_on_quality_error
    while retries_left > 0:
        failing_pages = sorted(
            {issue.page_index for issue in quality_resp.issues
             if issue.severity == "error" and issue.page_index is not None}
        )
        if not failing_pages:
            break

        warnings.append(
            WarningEntry(
                code="quality_retry",
                message=(
                    f"Retrying {len(failing_pages)} page(s) with quality errors "
                    f"(round {req.retry_pages_on_quality_error - retries_left + 1})."
                ),
                detail={"pages": failing_pages},
            )
        )
        retry_reqs = [
            ExecutePageRequest(
                spec_lock=strat.spec_lock,
                page_index=i,
                page_summary=(
                    page_summaries[i]
                    + "\n\n> Retry hint: the previous SVG failed quality checks. "
                    "Emit a simpler version of this page — fewer shapes, no "
                    "advanced filters, plain text + a single image if present."
                ),
                images=images_by_page.get(i, []),
                style=req.style,
                lang=req.lang,
                model=req.model,
                anthropic_api_key=req.anthropic_api_key,
            )
            for i in failing_pages
        ]
        retry_batch = await execute_batch(
            ExecuteBatchRequest(spec_lock=strat.spec_lock, pages=retry_reqs),
            client=client,
        )
        cost = _merge_cost(cost, retry_batch.cost)
        warnings.extend(retry_batch.warnings)
        for r in retry_batch.results:
            page_results[r.page_index] = r

        quality_resp = _run_quality_check(page_results, req.canvas_format)
        cost = _merge_cost(cost, quality_resp.cost)
        retries_left -= 1

    # Surface unresolved quality errors and (when configured) hard-fail.
    if not quality_resp.passed:
        errors = [i for i in quality_resp.issues if i.severity == "error"]
        if req.fail_on_quality_error:
            raise RuntimeError(
                f"Quality check failed with {len(errors)} error(s) after "
                f"{req.retry_pages_on_quality_error} retry round(s). "
                "Set fail_on_quality_error=False to export anyway."
            )
        warnings.append(
            WarningEntry(
                code="quality_errors_present",
                message=(
                    f"Exporting with {len(errors)} unresolved quality error(s); "
                    "PPT may not render every element correctly."
                ),
                detail={"error_count": len(errors)},
            )
        )

    # Apply any retried page results back into the batch view.
    exec_batch.results[:] = sorted(page_results.values(), key=lambda r: r.page_index)

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

def _run_quality_check(
    page_results: dict[int, "ExecutePageResponse"],
    canvas_format: CanvasFormat,
) -> QualityCheckResponse:
    """Run the SVG quality checker over the current page results."""
    from .execute import ExecutePageResponse  # for the forward ref

    quality_slides = [
        QualitySlide(
            index=p.page_index,
            name=f"slide_{p.page_index:02d}",
            svg=p.svg,
        )
        for p in sorted(page_results.values(), key=lambda r: r.page_index)
    ]
    return check_svg_quality(
        QualityCheckRequest(slides=quality_slides, canvas_format=canvas_format)
    )


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


# Heading patterns the Strategist might emit for per-page sections. Ordered
# so that the most specific (Page/Slide/페이지/슬라이드 + index) wins first;
# numbered fallback (`## 1.` / `## 1) Title`) catches templates that drop
# the keyword. All patterns are case-insensitive and anchored to a line start.
# The depth range 1–6 covers Markdown's full heading scale — the reference
# design_spec template emits page outlines as h4 (`#### Slide 01 - Cover`)
# under `## IX. Content Outline` / `### Part 1: Chapter`.
_PAGE_HEADING_PATTERNS = [
    # `## Page 1`, `## Page-1`, `## Page #1`, `## Page: 1`
    r"^#{1,6}\s+(?:Page|Slide|페이지|슬라이드|ページ|スライド)[\s\-:#]*\d+",
    # `## Slide 1: Title`  / `## 페이지 1 — 표지`
    r"^#{1,6}\s+(?:Page|Slide|페이지|슬라이드|ページ|スライド)\s+\d+[\s\-:—:]",
    # Numbered heading without a keyword: `## 1.`, `## 1)`, `## 1 - Title`
    r"^#{1,6}\s+\d+[\.\)\-]\s",
]
_PAGE_HEADING_RE = re.compile(
    "|".join(f"(?:{p})" for p in _PAGE_HEADING_PATTERNS),
    re.MULTILINE | re.IGNORECASE,
)


def _split_page_plan(
    design_spec: str,
    spec_lock: str,
    *,
    raw_output: str | None = None,
) -> list[str]:
    """Extract per-page content summaries from the Strategist's output.

    Heading patterns supported (case-insensitive, line-start, h1-h6):
        Page / Slide / 페이지 / 슬라이드 / ページ / スライド  + index
        Page-1 / Slide#1 / Slide: 1 / Page 1: Title / 페이지 1 — 표지
        Numbered headings without keyword (`## 1.`, `## 1)`, `## 1 - Title`)

    Resolution order — each layer covers a different Strategist quirk:
      1. Heading scan on `design_spec`.
      2. YAML parse of `spec_lock` looking for `pages` / `page_rhythm` /
         `page_layouts` / `outline` / `slides` collections.
      3. Markdown-style `## page_rhythm` / `## page_layouts` sections
         inside `spec_lock` (the format the shipped spec_lock_reference
         uses — markdown headings, not YAML keys).
      4. Heading scan on `raw_output` (catches the case where fence
         extraction truncated design_spec mid-document).
      5. Legacy YAML-ish line-walker over a `pages:` block.

    Returns [] only if every layer comes up empty — the caller then
    logs a diagnostic dump and raises.
    """
    # Layer 1: page headings inside design_spec.
    matches = list(_PAGE_HEADING_RE.finditer(design_spec))
    if matches:
        positions = [m.start() for m in matches] + [len(design_spec)]
        return [
            design_spec[positions[i] : positions[i + 1]].strip()
            for i in range(len(positions) - 1)
        ]

    # Layer 2: YAML-parsed spec_lock with a top-level list collection.
    yaml_chunks = _pages_from_spec_lock_yaml(spec_lock)
    if yaml_chunks:
        return yaml_chunks

    # Layer 3: markdown-style spec_lock — the shipped reference template
    # uses `## page_rhythm` / `## page_layouts` markdown sections with
    # `- P01: anchor` data lines. Count those to derive the page list.
    md_chunks = _pages_from_spec_lock_markdown(spec_lock)
    if md_chunks:
        return md_chunks

    # Layer 4: scan the entire raw_output. Triggers when an internal
    # ``` truncated design_spec mid-document and §IX Content Outline
    # spilled out into raw_output but not into our `design_spec` slice.
    if raw_output:
        raw_matches = list(_PAGE_HEADING_RE.finditer(raw_output))
        if raw_matches:
            positions = [m.start() for m in raw_matches] + [len(raw_output)]
            return [
                raw_output[positions[i] : positions[i + 1]].strip()
                for i in range(len(positions) - 1)
            ]

    # Layer 5: legacy line-walker over spec_lock's `pages:` block —
    # tolerates variants the YAML parser rejects (inline maps, weird
    # indentation).
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


# Matches `- P01: <anything>` data lines under a markdown `## page_rhythm`
# / `## page_layouts` / `## page_charts` section in spec_lock.
_SPEC_LOCK_PAGE_ROW_RE = re.compile(
    r"^\s*-\s*P\d{1,3}\s*:",
    re.MULTILINE | re.IGNORECASE,
)


def _pages_from_spec_lock_markdown(spec_lock: str) -> list[str]:
    """Read the markdown-shaped spec_lock used by the shipped reference
    template, where pages are declared as `- P01: tag` rows under one
    of several `## <section>` markdown headings.

    Strategy: collect every `- P<NN>: ...` row across the document and
    deduplicate by index. Each unique index becomes one page summary
    carrying every row attribute that mentions it (rhythm tag, layout
    name, chart template, etc.) so the executor still has structured
    context to work from.
    """
    if not spec_lock.strip():
        return []
    if not _SPEC_LOCK_PAGE_ROW_RE.search(spec_lock):
        return []

    # Map: "P01" -> list of attribute snippets harvested from each section.
    rows: dict[str, list[str]] = {}
    order: list[str] = []
    current_section = ""
    for line in spec_lock.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            current_section = stripped.lstrip("# ").strip().lower()
            continue
        m = re.match(r"^\s*-\s*(P\d{1,3})\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if not m:
            continue
        key = m.group(1).upper()
        value = m.group(2).strip()
        snippet = f"{current_section or 'page'}: {value}" if value else current_section or "page"
        if key not in rows:
            rows[key] = []
            order.append(key)
        rows[key].append(snippet)

    if not order:
        return []

    return [
        f"# {key}\n" + "\n".join(f"- {snip}" for snip in rows[key])
        for key in order
    ]


def _all_markdown_headings(text: str) -> list[str]:
    """Return every markdown heading line in *text* (h1-h6).

    Used by the diagnostic logger so an operator looking at a parse
    failure can immediately see what shape the Strategist actually
    produced — no need to scroll through 10 KB of design_spec.
    """
    if not text:
        return []
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*#{1,6}\s+\S", line)
    ]


def _pages_from_spec_lock_yaml(spec_lock: str) -> list[str]:
    """Locate a page-list-shaped collection inside spec_lock and return one
    string summary per entry.

    Accepts any of these top-level keys (and a few synonyms the Strategist
    sometimes invents): `pages`, `page_rhythm`, `page_layouts`, `outline`,
    `slides`. The first key whose value is a non-empty list is taken.
    """
    if not spec_lock.strip():
        return []
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a hard dep
        return []
    try:
        doc = yaml.safe_load(spec_lock)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    for key in ("pages", "page_rhythm", "page_layouts", "outline", "slides"):
        value = doc.get(key)
        if isinstance(value, list) and value:
            return [_yaml_entry_to_summary(item) for item in value if item is not None]
    return []


def _yaml_entry_to_summary(item: object) -> str:
    """Render a YAML list entry as a markdown-ish summary the Executor
    can read. Scalars become themselves; dicts become a `key: value`
    bullet list."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        import yaml  # local import; cheap when the call site already
        # decided this path is in play.
        try:
            return yaml.safe_dump(item, allow_unicode=True, sort_keys=False).strip()
        except yaml.YAMLError:
            return repr(item)
    return str(item)


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
