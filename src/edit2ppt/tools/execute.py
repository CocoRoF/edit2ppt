"""Execute tool: per-page SVG generation by the Executor LLM role.

For each page in the Strategist's plan, this tool calls the LLM with:
- system prompt: executor-base + style variant (consultant/general/...) for the page lang
- user message: spec_lock YAML + this page's content outline + any images for it

The LLM returns an SVG string (plus optional speaker notes). Pages are
independent so the worker can fan them out in parallel — see
ppt-master-analysis/04-integration-plan.md §4.9.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Literal

from pydantic import Field

from ..llm import AnthropicClient, DEFAULT_MODEL, build_output_lang_directive, load_prompt
from ..llm.anthropic_client import LLMResult, LLMUsage
from .strategize import LLMCallable
from .types import (
    CostBreakdown,
    DEFAULT_LANG,
    LangCode,
    ToolRequest,
    ToolResponse,
    WarningEntry,
)

ExecutorStyle = Literal["general", "consultant", "consultant-top"]


class ExecutorImage(ToolRequest):
    """One image available to a page (bytes or external URL)."""

    placeholder: str = Field(..., description="Token used by the LLM to reference this image.")
    url: str | None = None
    description: str | None = None


class ExecutePageRequest(ToolRequest):
    spec_lock: str = Field(..., description="YAML spec_lock from the Strategist.")
    page_index: int = Field(..., ge=0)
    page_summary: str = Field(..., description="Per-page content outline (markdown).")
    images: list[ExecutorImage] = Field(default_factory=list)
    style: ExecutorStyle = "general"
    lang: LangCode = DEFAULT_LANG
    model: str = DEFAULT_MODEL
    anthropic_api_key: str


class ExecutePageResponse(ToolResponse):
    page_index: int
    svg: str
    speaker_notes: str
    raw_output: str
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


class ExecuteBatchRequest(ToolRequest):
    """Parallel execution of multiple pages with a shared spec_lock."""

    spec_lock: str
    pages: list[ExecutePageRequest]
    max_concurrency: int = Field(default=4, ge=1, le=16)


class ExecuteBatchResponse(ToolResponse):
    results: list[ExecutePageResponse]
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


async def execute_page(
    req: ExecutePageRequest,
    *,
    client: LLMCallable | None = None,
) -> ExecutePageResponse:
    started = time.perf_counter()
    warnings: list[WarningEntry] = []

    system_prompt = _build_system_prompt(req.style, req.lang)
    user_message = _build_user_message(req)

    llm = client or AnthropicClient(api_key=req.anthropic_api_key, model=req.model)
    result = await llm.complete(
        system_prompt=system_prompt,
        user_message=user_message,
        max_output_tokens=8192,
        cache_system=True,
        model=req.model,
    )

    svg, notes = _parse_output(result.text, warnings)
    # Normalise the LLM's raw SVG before it flows downstream. Quality
    # and export both run on this exact string — every fix-up applied
    # here means one less stage-specific patch elsewhere:
    #   * id-backfill on anonymous top-level <g> (kills the
    #     `<g> has no id` warning class).
    #   * <image href> normalised to bare basename. The model loves to
    #     prefix references with `../images/`, which doesn't resolve in
    #     the workspace where images sit alongside SVGs.
    #   * Strip `opacity` from <image> (PPT doesn't support image
    #     opacity; the legacy checker bans it. We could decompose into
    #     image + overlay rect but losing the mute is acceptable for
    #     keeping the build green).
    svg = _autoid_top_level_groups(svg)
    svg, image_basenames = _normalise_image_refs(svg, req.images)

    return ExecutePageResponse(
        page_index=req.page_index,
        svg=svg,
        speaker_notes=notes,
        raw_output=result.text,
        cost=_cost_from_usage(result.usage, time.perf_counter() - started),
        warnings=warnings,
    )


async def execute_batch(
    req: ExecuteBatchRequest,
    *,
    client: LLMCallable | None = None,
) -> ExecuteBatchResponse:
    """Run every page in parallel under a concurrency cap.

    Per-page exceptions are captured and surfaced as warnings (the failed
    page gets a placeholder SVG so subsequent stages — quality, export —
    still see N slides). This preserves the rest of the deck when one
    Executor call goes sideways.
    """
    started = time.perf_counter()
    sem = asyncio.Semaphore(req.max_concurrency)

    async def _run_one(p: ExecutePageRequest) -> ExecutePageResponse:
        async with sem:
            return await execute_page(p, client=client)

    raw_results = await asyncio.gather(
        *[_run_one(p) for p in req.pages], return_exceptions=True
    )

    results: list[ExecutePageResponse] = []
    warnings: list[WarningEntry] = []
    for page_req, outcome in zip(req.pages, raw_results):
        if isinstance(outcome, Exception):
            warnings.append(
                WarningEntry(
                    code="execute_page_failed",
                    message=f"Page {page_req.page_index} executor failed: {outcome}",
                    detail={
                        "page_index": page_req.page_index,
                        "error_type": type(outcome).__name__,
                    },
                )
            )
            results.append(_placeholder_response(page_req))
        else:
            results.append(outcome)

    total = CostBreakdown(duration_seconds=time.perf_counter() - started)
    for r in results:
        total = CostBreakdown(
            input_tokens=total.input_tokens + r.cost.input_tokens,
            output_tokens=total.output_tokens + r.cost.output_tokens,
            cache_read_tokens=total.cache_read_tokens + r.cost.cache_read_tokens,
            cache_write_tokens=total.cache_write_tokens + r.cost.cache_write_tokens,
            duration_seconds=total.duration_seconds,
        )
        warnings.extend(r.warnings)

    return ExecuteBatchResponse(
        results=sorted(results, key=lambda r: r.page_index),
        cost=total,
        warnings=warnings,
    )


def _placeholder_response(req: ExecutePageRequest) -> ExecutePageResponse:
    """Minimal valid SVG used when an executor call fails. Keeps the deck
    aligned to N slides so quality / export still run cleanly."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" '
        'width="1920" height="1080">'
        '<rect x="0" y="0" width="1920" height="1080" fill="#fafafa"/>'
        f'<text x="120" y="540" font-family="sans-serif" font-size="36" fill="#888">'
        f'Page {req.page_index + 1} could not be generated.'
        '</text>'
        '</svg>'
    )
    return ExecutePageResponse(
        page_index=req.page_index,
        svg=svg,
        speaker_notes="",
        raw_output="",
        cost=CostBreakdown(),
        warnings=[],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_system_prompt(style: ExecutorStyle, lang: LangCode) -> str:
    """Stitch the runtime language directive + base executor + style variant.

    All prompts are English single-source (see llm/prompt_loader.py); the
    directive tells the LLM to emit slide content in *lang* while keeping
    SVG attribute names / asset filenames / token names English.
    """
    directive = build_output_lang_directive(lang)
    base = load_prompt("executor-base")
    variant_role = {
        "general": "executor-general",
        "consultant": "executor-consultant",
        "consultant-top": "executor-consultant-top",
    }[style]
    variant = load_prompt(variant_role)
    return f"{directive}\n\n---\n\n{base}\n\n---\n\n{variant}"


def _build_user_message(req: ExecutePageRequest) -> str:
    lines: list[str] = []
    lines.append(f"# Page {req.page_index} ({req.lang})")
    lines.append("")
    lines.append("## spec_lock")
    lines.append("```yaml")
    lines.append(req.spec_lock.strip())
    lines.append("```")
    lines.append("")
    lines.append("## Page content")
    lines.append(req.page_summary.strip())
    lines.append("")
    if req.images:
        lines.append("## Images available")
        for img in req.images:
            extra = f" — {img.description}" if img.description else ""
            location = img.url or "(inline, bound to placeholder)"
            lines.append(f"- `{img.placeholder}` @ {location}{extra}")
        lines.append("")
    lines.append("## Output format")
    lines.append(
        "Produce two fenced blocks in this order:\n"
        "1. ```svg ... ``` — the full slide SVG for this page only.\n"
        "2. ```notes ... ``` — speaker notes (markdown). May be empty."
    )
    return "\n".join(lines)


_SVG_BLOCK_RE = re.compile(r"```(?:svg|xml)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_NOTES_BLOCK_RE = re.compile(r"```(?:notes|markdown)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _parse_output(text: str, warnings: list[WarningEntry]) -> tuple[str, str]:
    svg_match = _SVG_BLOCK_RE.search(text)
    if svg_match:
        svg = svg_match.group(1).strip()
    else:
        # Some models emit a raw <svg>...</svg> block without fences. Tolerate it.
        bare = re.search(r"<svg[\s\S]*?</svg>", text, re.IGNORECASE)
        if bare:
            svg = bare.group(0).strip()
            warnings.append(
                WarningEntry(
                    code="unfenced_svg",
                    message="Executor returned an unfenced <svg> block; accepted but verify formatting.",
                )
            )
        else:
            raise ValueError("Executor output did not contain an SVG block")

    notes_match = _NOTES_BLOCK_RE.search(text)
    notes = notes_match.group(1).strip() if notes_match else ""
    return svg, notes


def _normalise_image_refs(svg: str, available: list) -> tuple[str, set[str]]:
    """Make every `<image>` resilient to the workspace layout.

    Three transforms applied in one pass:
      1. `href` (or `xlink:href`) is reduced to the **basename** of the
         path. The model frequently writes `../images/cover_bg.png`
         expecting an `images/` subfolder; export puts the bytes
         directly next to the SVG. Normalising to `cover_bg.png` makes
         the reference resolve regardless of layout.
      2. The `opacity` attribute is removed — PPTX has no native
         image opacity, the legacy quality rule bans it, and the
         tiniest production case where this fires (a chapter divider
         dimmed for readability) is acceptable to render at full
         opacity. The retry hint would otherwise loop the model
         needlessly.
      3. `<image>` elements whose basename is not in the executor's
         image bundle are dropped entirely — the reference would
         dangle and crash the converter. The slide loses a decoration
         but stays intact.

    Returns (svg, basenames_referenced). The basename set lets the
    caller cross-check against the bundle.

    Best effort: parse failures fall through, returning the input.
    """
    if not svg or "<image" not in svg:
        return svg, set()
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg, set()

    SVG_NS = "http://www.w3.org/2000/svg"
    XLINK_NS = "http://www.w3.org/1999/xlink"

    # Build the lookup set of bundled basenames.
    bundle: set[str] = set()
    for img in available or []:
        url = getattr(img, "url", None)
        if url:
            bundle.add(url.rsplit("/", 1)[-1])

    parent_of: dict[ET.Element, ET.Element] = {}
    for p in root.iter():
        for c in p:
            parent_of[c] = p

    referenced: set[str] = set()
    drops: list[tuple[ET.Element, ET.Element]] = []

    for elem in list(root.iter()):
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "image":
            continue

        href = elem.get("href") or elem.get(f"{{{XLINK_NS}}}href")
        if href and not href.startswith("data:"):
            basename = href.rsplit("/", 1)[-1]
            if elem.get("href") is not None:
                elem.set("href", basename)
            else:
                elem.set(f"{{{XLINK_NS}}}href", basename)
            referenced.add(basename)
            # If the basename isn't in the bundle, drop the element —
            # better a slide missing one decoration than a crash.
            if bundle and basename not in bundle:
                parent = parent_of.get(elem)
                if parent is not None:
                    drops.append((parent, elem))

        if "opacity" in elem.attrib:
            del elem.attrib["opacity"]

    for parent, elem in drops:
        parent.remove(elem)

    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="unicode"), referenced


def _autoid_top_level_groups(svg: str) -> str:
    """Backfill `id` on every top-level <g> of *svg* that lacks one.

    Runs at the boundary where the LLM's raw SVG enters the pipeline.
    Quality, retry, export and the final PPTX all see the normalized
    text — eliminates the spammy `Top-level visible <g> #N has no id`
    warnings without changing the LLM's behaviour or the visible
    output.

    Best-effort: if the SVG fails to parse we return it untouched and
    let the downstream stages report the real error.
    """
    if not svg or "<svg" not in svg:
        return svg
    try:
        from xml.etree import ElementTree as ET

        from ..core.svg_to_pptx.drawingml_converter import (
            _autogen_top_level_group_ids,
        )

        root = ET.fromstring(svg)
        if _autogen_top_level_group_ids(root) == 0:
            return svg
        # Preserve the `xmlns` declaration in the serialized output —
        # ElementTree drops the namespace prefix when re-serialising,
        # so we explicitly re-register the default SVG namespace.
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        return ET.tostring(root, encoding="unicode")
    except Exception:
        return svg


def _cost_from_usage(usage: LLMUsage, duration_seconds: float) -> CostBreakdown:
    return CostBreakdown(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_seconds=duration_seconds,
    )
