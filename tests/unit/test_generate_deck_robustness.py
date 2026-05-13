"""Unit tests for the C3 robustness improvements.

Covers three areas:
- Page-plan parsing tolerates many heading patterns (Korean, Japanese,
  numbered without keyword, hyphenated, colon-separated).
- execute_batch preserves partial results when a per-page call raises —
  the failing page gets a placeholder SVG; subsequent stages see N slides.
- generate_deck retries quality-error pages when retry_pages_on_quality_error
  is set, surfacing each retry as a warning.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pytest

from edit2ppt.llm.anthropic_client import LLMResult, LLMUsage
from edit2ppt.tools import (
    ConvertRequest,
    ConvertResponse,
    CostBreakdown,
    ExecuteBatchRequest,
    ExecuteBatchResponse,
    ExecutePageRequest,
    ExecutePageResponse,
    StrategizeResponse,
    execute_batch,
)
from edit2ppt.tools.generate_deck import GenerateDeckRequest, generate_deck, _split_page_plan


# ---------------------------------------------------------------------------
# Page-plan parsing
# ---------------------------------------------------------------------------


class TestPagePlanParsing:
    def test_english_page_headings(self):
        spec = "## Page 1\ncover\n## Page 2\nsummary\n## Page 3\nconclusion"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 3
        assert "cover" in chunks[0]
        assert "summary" in chunks[1]

    def test_korean_page_keyword(self):
        spec = "## 페이지 1\n표지\n## 페이지 2\n결론"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2
        assert "표지" in chunks[0]

    def test_korean_slide_keyword(self):
        spec = "## 슬라이드 1\n표지\n## 슬라이드 2\n결론"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_japanese_keywords(self):
        spec = "## ページ 1\n表紙\n## スライド 2\nまとめ"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_hyphenated_form(self):
        spec = "## Page-1\ncover\n## Page-2\nsummary"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_colon_with_title(self):
        spec = "## Slide 1: Cover page\nintro\n## Slide 2: Summary\nfindings"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_em_dash_form(self):
        spec = "## 페이지 1 — 표지\ncontents\n## 페이지 2 — 결론\nmore"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_numbered_without_keyword(self):
        spec = "## 1. Cover\nintro\n## 2. Summary\nfindings\n## 3) Conclusion\nwrap"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 3

    def test_mixed_levels(self):
        # Strategist may emit h1 or h3 instead of h2.
        spec = "# Page 1\nintro\n### Page 2\nbody"
        chunks = _split_page_plan(spec, "")
        assert len(chunks) == 2

    def test_no_headings_falls_back_to_spec_lock(self):
        design_spec = "Just freeform text with no headings."
        spec_lock = "pages:\n  - title: a\n  - title: b\n  - title: c"
        chunks = _split_page_plan(design_spec, spec_lock)
        assert len(chunks) >= 1  # falls back to spec_lock pages parsing


# ---------------------------------------------------------------------------
# execute_batch partial-result preservation
# ---------------------------------------------------------------------------


@dataclass
class _MixedSuccessClient:
    """Returns one good SVG, raises on the second call, returns good on the third."""

    calls: list[dict] = field(default_factory=list)
    fail_on_call_index: int = 1

    async def complete(self, system_prompt, user_message, **kwargs):
        idx = len(self.calls)
        self.calls.append({"system": system_prompt, "user": user_message, **kwargs})
        if idx == self.fail_on_call_index:
            raise RuntimeError("simulated upstream LLM blow-up")
        return LLMResult(
            text="```svg\n<svg></svg>\n```\n```notes\nx\n```",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
            model="stub",
            stop_reason="end_turn",
        )


class TestExecuteBatchPartialResults:
    @pytest.mark.asyncio
    async def test_failed_page_gets_placeholder_and_others_succeed(self):
        client = _MixedSuccessClient()
        page_reqs = [
            ExecutePageRequest(
                spec_lock="lang: ko-KR",
                page_index=i,
                page_summary=f"page {i}",
                lang="ko-KR",
                anthropic_api_key="stub",
            )
            for i in range(3)
        ]
        batch = await execute_batch(
            ExecuteBatchRequest(spec_lock="lang: ko-KR", pages=page_reqs),
            client=client,
        )

        # All three pages have a result entry (no aborted gather()).
        assert len(batch.results) == 3
        assert [r.page_index for r in batch.results] == [0, 1, 2]

        # The failing page is a placeholder; others have real SVG from the stub.
        # The placeholder contains the specific failure text.
        failed = [r for r in batch.results if "could not be generated" in r.svg]
        assert len(failed) == 1
        # The warning is surfaced with the right code.
        codes = {w.code for w in batch.warnings}
        assert "execute_page_failed" in codes


# ---------------------------------------------------------------------------
# Quality retry inside generate_deck
# ---------------------------------------------------------------------------


@dataclass
class _StrategizeStub:
    page_count: int

    async def __call__(self, req, *, client=None):
        return StrategizeResponse(
            raw_output="...",
            design_spec="\n\n".join(
                f"## Page {i+1}\npage {i} content" for i in range(self.page_count)
            ),
            spec_lock="lang: ko-KR\npages:\n  - p0\n  - p1",
            cost=CostBreakdown(input_tokens=5, output_tokens=5),
        )


# Canvas-format default is ppt169 -> viewBox `0 0 1280 720`. Stick to that
# so quality_check accepts the SVG without complaining about size mismatch.
_GOOD_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
    'width="1280" height="720">'
    '<rect x="0" y="0" width="1280" height="720" fill="#ffffff"/>'
    '<text x="120" y="360" font-family="Pretendard, sans-serif" '
    'font-size="48" fill="#1a1a1a">테스트</text>'
    "</svg>"
)
# Forbidden element <foreignObject> reliably triggers the quality checker.
_BAD_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
    'width="1280" height="720">'
    '<foreignObject x="0" y="0" width="1280" height="720">'
    "<div>broken</div>"
    "</foreignObject>"
    "</svg>"
)


@dataclass
class _RetryingExecuteBatchStub:
    """First call: bad SVG on page 1. Retry call: good SVG. Page 0 stays good
    the whole time."""

    received_specs: list[list[ExecutePageRequest]] = field(default_factory=list)

    async def __call__(self, req, *, client=None):
        self.received_specs.append(req.pages)
        results = []
        is_retry = len(self.received_specs) > 1
        for p in req.pages:
            if is_retry or p.page_index == 0:
                svg = _GOOD_SVG
            else:
                svg = _BAD_SVG
            results.append(
                ExecutePageResponse(
                    page_index=p.page_index,
                    svg=svg,
                    speaker_notes="",
                    raw_output="...",
                    cost=CostBreakdown(),
                    warnings=[],
                )
            )
        return ExecuteBatchResponse(results=results, cost=CostBreakdown(), warnings=[])


class TestQualityRetry:
    def setup_method(self):
        self.gd = sys.modules["edit2ppt.tools.generate_deck"]

    @pytest.mark.asyncio
    async def test_retry_invoked_with_simplification_hint(self, monkeypatch):
        # Wire stubs.
        strat = _StrategizeStub(page_count=2)
        execute = _RetryingExecuteBatchStub()
        monkeypatch.setattr(self.gd, "strategize", strat)
        monkeypatch.setattr(self.gd, "execute_batch", execute)
        monkeypatch.setattr(self.gd, "convert_to_markdown",
                            lambda r: ConvertResponse(
                                markdown="# x", detected_format="pdf",
                                original_filename=None, char_count=1,
                                cost=CostBreakdown(),
                            ))
        monkeypatch.setattr(self.gd, "AnthropicClient", lambda **kwargs: object())

        result = await generate_deck(
            GenerateDeckRequest(
                sources=[ConvertRequest(source_type="pdf", content=b"%PDF")],
                user_intent="x",
                target_pages=(2, 2),
                lang="ko-KR",
                anthropic_api_key="sk-ant-stub",
                fail_on_quality_error=False,  # don't fail; we want to inspect warnings
                retry_pages_on_quality_error=1,
                skip_images=True,
            )
        )

        # execute_batch was called twice: initial + 1 retry.
        assert len(execute.received_specs) == 2

        # The retry batch contained only the failing page (page_index=1).
        retry_pages = execute.received_specs[1]
        assert len(retry_pages) == 1
        assert retry_pages[0].page_index == 1
        # The retry hint is appended to the summary.
        assert "Retry hint" in retry_pages[0].page_summary

        # Warnings surface the retry.
        codes = {w.code for w in result.warnings}
        assert "quality_retry" in codes
