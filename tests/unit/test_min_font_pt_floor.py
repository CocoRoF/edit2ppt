"""Minimum readable font size at projection scale.

Production decks were emitting `<a:rPr sz="825">` (8.25 pt) for body
annotations — an 11px SVG label translated 1:0.75. That's illegible
when the slide is projected. Enforce a 9pt floor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _build_slide_xml(svg: str, tmp_path: Path) -> str:
    from edit2ppt.core.svg_to_pptx.drawingml_converter import (
        convert_svg_to_slide_shapes,
    )

    svg_path = tmp_path / "slide.svg"
    svg_path.write_text(svg, encoding="utf-8")
    slide_xml, _media, _rels, _anim = convert_svg_to_slide_shapes(svg_path)
    return slide_xml


def _font_sizes_in(xml: str) -> list[int]:
    return [int(m) for m in re.findall(r'\bsz="(\d+)"', xml)]


@pytest.mark.parametrize("svg_px", [8, 9, 10, 11])
def test_small_text_is_floored_at_9pt(tmp_path, svg_px):
    """Anything ≤ 11px (≤ 8.25pt at 0.75 ratio) bumps up to 900 (9pt)."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        f'<text x="100" y="200" font-size="{svg_px}" font-family="Pretendard, sans-serif">미세 라벨</text>'
        f'</svg>'
    )
    xml = _build_slide_xml(svg, tmp_path)
    sizes = _font_sizes_in(xml)
    assert sizes, "expected at least one <a:rPr sz=> in the output"
    # The floor is 900 = 9pt; nothing in the output should fall below it.
    assert min(sizes) >= 900


def test_legibly_sized_text_is_unchanged(tmp_path):
    """A 20px body text (15pt at 0.75 ratio) should round-trip without
    the floor kicking in — the floor must only bump tiny labels."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<text x="100" y="200" font-size="20" font-family="Pretendard, sans-serif">본문 텍스트</text>'
        '</svg>'
    )
    xml = _build_slide_xml(svg, tmp_path)
    sizes = _font_sizes_in(xml)
    assert 1500 in sizes  # 20px × 75 = 1500 (15pt)


def test_huge_hero_number_preserved(tmp_path):
    """Heroes and titles at large sizes round-trip cleanly."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<text x="100" y="200" font-size="120" font-family="Pretendard, sans-serif">55%</text>'
        '</svg>'
    )
    xml = _build_slide_xml(svg, tmp_path)
    sizes = _font_sizes_in(xml)
    assert 9000 in sizes  # 120px × 75 = 9000 (90pt)
