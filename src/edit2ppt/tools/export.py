"""Export tool: render finalized SVGs into a PPTX (with Korean lang propagation).

This is the M1 capstone tool: it stitches together the core engine's
`create_pptx_with_native_svg` builder, hides the disk I/O behind a temp
workspace, and exposes a clean Pydantic-typed function the rest of the
server (and tests) can call.

The `lang` parameter threads through to OOXML `<a:rPr lang="...">` via the
G2 patch in `core/svg_to_pptx/{pptx_notes,drawingml_elements}.py`. When the
caller doesn't pass `lang`, each text run's language is detected from its
content.
"""

from __future__ import annotations

import time

from pydantic import Field

from ..core.svg_to_pptx.pptx_builder import create_pptx_with_native_svg
from ..core.svg_to_pptx.drawingml_utils import detect_lang
from ._workspace import temp_workspace, write_text
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


class SlideInput(ToolRequest):
    """A single slide's rendered SVG + optional speaker notes."""

    index: int = Field(..., description="0-based slide index", ge=0)
    name: str = Field(..., description="Slide stem (used inside the PPTX rels)")
    svg: str = Field(..., description="Fully-rendered SVG markup")
    notes: str | None = Field(default=None, description="Speaker notes (Markdown)")


class ExportRequest(ToolRequest):
    """Inputs for `export_pptx`."""

    slides: list[SlideInput]
    canvas_format: CanvasFormat = DEFAULT_CANVAS
    lang: LangCode = Field(
        default=DEFAULT_LANG,
        description="OOXML lang attribute. ko-KR by default. Auto-detected per-run when None.",
    )
    transition: str | None = "fade"
    transition_duration: float = 0.5
    animation: str | None = None
    animation_duration: float = 0.4
    animation_stagger: float = 0.5
    animation_trigger: str = "after-previous"
    enable_notes: bool = True
    use_native_shapes: bool = True
    use_compat_mode: bool = True


class ExportResponse(ToolResponse):
    pptx: bytes
    page_count: int
    detected_langs: list[LangCode] = Field(
        default_factory=list,
        description="Per-slide language inferred from svg text content; useful for QA.",
    )
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


def export_pptx(req: ExportRequest) -> ExportResponse:
    """Render a deck of SVGs into an editable PPTX.

    The deck's `lang` is applied to OOXML rPr blocks; per-run detection still
    fires when a slide contains text in a different script (e.g. an English
    chart label inside an otherwise-Korean deck — that run gets lang="en-US").

    Raises:
        ValueError: if `slides` is empty.
        RuntimeError: if the underlying engine returns failure.
    """
    if not req.slides:
        raise ValueError("export_pptx requires at least one slide")

    started = time.perf_counter()
    warnings: list[WarningEntry] = []
    detected: list[LangCode] = []

    with temp_workspace(prefix="edit2ppt-export-") as ws:
        svg_dir = ws / "svgs"
        svg_dir.mkdir()
        svg_paths = []
        notes_map: dict[str, str] = {}

        # Sort by index so the resulting deck is deterministic regardless of caller order.
        for slide in sorted(req.slides, key=lambda s: s.index):
            svg_path = write_text(svg_dir, f"{slide.name}.svg", slide.svg)
            svg_paths.append(svg_path)
            if slide.notes:
                notes_map[slide.name] = slide.notes
            detected.append(detect_lang(slide.svg, default=req.lang))  # type: ignore[arg-type]

        output_path = ws / "output.pptx"
        ok = create_pptx_with_native_svg(
            svg_files=svg_paths,
            output_path=output_path,
            canvas_format=req.canvas_format,
            verbose=False,
            transition=req.transition,
            transition_duration=req.transition_duration,
            use_compat_mode=req.use_compat_mode,
            notes=notes_map or None,
            enable_notes=req.enable_notes,
            use_native_shapes=req.use_native_shapes,
            animation=req.animation,
            animation_duration=req.animation_duration,
            animation_stagger=req.animation_stagger,
            animation_trigger=req.animation_trigger,
        )
        if not ok:
            raise RuntimeError("core engine reported failure during PPTX assembly")
        pptx_bytes = output_path.read_bytes()

    duration = time.perf_counter() - started
    return ExportResponse(
        pptx=pptx_bytes,
        page_count=len(req.slides),
        detected_langs=detected,
        cost=CostBreakdown(duration_seconds=duration),
        warnings=warnings,
    )
