"""SVG quality check tool.

Wraps `core.svg_quality_checker.SVGQualityChecker` behind a stateless function
that accepts SVG strings (instead of file paths) and returns structured issues.
The engine still writes scratch files (it does internal cross-reference checks
across the deck), so we use a temp workspace.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import Field

from ._workspace import temp_workspace, write_text
from .types import (
    CanvasFormat,
    CostBreakdown,
    DEFAULT_CANVAS,
    QualityIssue,
    ToolRequest,
    ToolResponse,
    WarningEntry,
)


class QualitySlide(ToolRequest):
    index: int = Field(..., ge=0)
    name: str
    svg: str


class QualityCheckRequest(ToolRequest):
    slides: list[QualitySlide]
    canvas_format: CanvasFormat = DEFAULT_CANVAS
    template_mode: bool = Field(
        default=False,
        description="Skip spec_lock drift / image attribution checks for template authoring.",
    )


class QualityCheckResponse(ToolResponse):
    issues: list[QualityIssue]
    passed: bool = Field(..., description="True iff no error-severity issues.")
    cost: CostBreakdown
    warnings: list[WarningEntry] = Field(default_factory=list)


def check_svg_quality(req: QualityCheckRequest) -> QualityCheckResponse:
    """Run the engine's quality checks across an in-memory deck of SVGs."""
    from ..core.svg_quality_checker import SVGQualityChecker

    started = time.perf_counter()
    issues: list[QualityIssue] = []

    if not req.slides:
        return QualityCheckResponse(
            issues=[],
            passed=True,
            cost=CostBreakdown(duration_seconds=time.perf_counter() - started),
        )

    checker = SVGQualityChecker(template_mode=req.template_mode)

    with temp_workspace(prefix="edit2ppt-quality-") as ws:
        svg_dir = ws / "svgs"
        svg_dir.mkdir()
        for slide in sorted(req.slides, key=lambda s: s.index):
            write_text(svg_dir, f"{slide.name}.svg", slide.svg)

        # Run per-file checks. The engine also has a directory-level checker,
        # but the per-file path is enough for the M2 contract.
        for slide in sorted(req.slides, key=lambda s: s.index):
            svg_path = svg_dir / f"{slide.name}.svg"
            result = checker.check_file(str(svg_path), expected_format=req.canvas_format)
            for err in result.get("errors", []):
                issues.append(
                    QualityIssue(
                        page_index=slide.index,
                        severity="error",
                        code="quality_error",
                        message=str(err),
                        location=slide.name,
                    )
                )
            for warn in result.get("warnings", []):
                issues.append(
                    QualityIssue(
                        page_index=slide.index,
                        severity="warning",
                        code="quality_warning",
                        message=str(warn),
                        location=slide.name,
                    )
                )

    passed = not any(i.severity == "error" for i in issues)
    return QualityCheckResponse(
        issues=issues,
        passed=passed,
        cost=CostBreakdown(duration_seconds=time.perf_counter() - started),
    )
