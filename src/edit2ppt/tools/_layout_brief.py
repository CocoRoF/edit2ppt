"""Deterministic per-page layout brief generator.

For each page in the deck we produce a structured "layout brief" that
declares the safe area, the page-number / footer / chapter-label
zones, and a content zone shaped by the page's rhythm tag. The brief
is injected into the Executor's user_message as YAML, BEFORE the
page outline, so the LLM has hard bounding-box constraints to work
inside.

Why this matters
================

Without a brief, the Executor invents the box geometry every call —
the same outline produces different layouts across pages because
the model picks coordinates from scratch each time. With a brief:

* Every page's page-number box is the same 130×30 at (1100, 680)
  regardless of which model invocation produced it.
* Footer / source-citation lives at a known position.
* Chapter labels have enough width to fit Korean + English text.
* Hero / body zones are shaped by the spec_lock `page_rhythm` tag —
  anchor → big centred hero, dense → grid, breathing → single hero
  with whitespace.

The brief is best-effort guidance, not a hard contract. The model is
welcome to deviate when the page's content genuinely needs different
geometry; the brief just removes the "we have to invent every box
from scratch" cognitive load.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal


# Canonical canvas dimensions. Layout briefs are emitted in this
# coordinate space; if the Executor picks a different viewBox the
# normaliser (P #42) wraps the SVG so coordinates still resolve.
_CANVAS_W = 1280
_CANVAS_H = 720

# Safe area: 40 px margin on every edge.
_SAFE_MARGIN = 40
_SAFE_X = _SAFE_MARGIN
_SAFE_Y = _SAFE_MARGIN
_SAFE_W = _CANVAS_W - 2 * _SAFE_MARGIN  # 1200
_SAFE_H = _CANVAS_H - 2 * _SAFE_MARGIN  # 640


ZoneRole = Literal[
    "title",
    "subtitle",
    "hero",
    "body",
    "footer",
    "page_number",
    "chapter_label",
    "image",
]


@dataclass
class Zone:
    role: ZoneRole
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return {"role": self.role, "x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class PageLayoutBrief:
    page_index: int
    page_id: str  # P01, P02, ...
    rhythm: Literal["anchor", "dense", "breathing"]
    canvas_w: int = _CANVAS_W
    canvas_h: int = _CANVAS_H
    safe_x: int = _SAFE_X
    safe_y: int = _SAFE_Y
    safe_w: int = _SAFE_W
    safe_h: int = _SAFE_H
    zones: list[Zone] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "page_id": self.page_id,
            "rhythm": self.rhythm,
            "canvas": {"w": self.canvas_w, "h": self.canvas_h},
            "safe_area": {
                "x": self.safe_x, "y": self.safe_y,
                "w": self.safe_w, "h": self.safe_h,
            },
            "zones": [z.to_dict() for z in self.zones],
        }


# Standard footer zones used on every page. Sized for 12pt+ text so
# "01 / 12" and chapter labels survive projection.
def _footer_zones() -> list[Zone]:
    return [
        # Bottom band, full safe-area width.
        Zone(role="footer", x=_SAFE_X, y=680, w=_SAFE_W, h=24),
        # Page-number sub-zone: right-aligned, generous width so
        # "NN / MM" at 12pt always fits.
        Zone(role="page_number", x=1100, y=684, w=140, h=20),
    ]


def _chapter_label_zone() -> Zone:
    """Chapter / section banner at the top. 1180×24 is wide enough
    for "CHAPTER 02 — 현실 진단" with English+Korean mix."""
    return Zone(role="chapter_label", x=_SAFE_X, y=_SAFE_Y, w=_SAFE_W, h=24)


def _zones_for_rhythm(rhythm: str) -> list[Zone]:
    """Pick the content-zone shape from the rhythm tag.

    ``anchor`` — cover / chapter / closing: title-dominant, big
        centred hero, no body grid.
    ``dense`` — most pages: title up top, multi-block body zone.
    ``breathing`` — single concept, hero quote, big image: one
        large hero zone centred, no body grid.
    """
    # Body zone starts below the chapter label (top ~ 80) and ends
    # above the footer (bottom ~ 668). Title takes the upper slice.
    if rhythm == "breathing":
        # Single large hero spanning most of the canvas.
        return [
            Zone(role="hero", x=_SAFE_X, y=160, w=_SAFE_W, h=420),
            Zone(role="subtitle", x=_SAFE_X, y=600, w=_SAFE_W, h=60),
        ]
    if rhythm == "anchor":
        # Title on top, hero centred below.
        return [
            Zone(role="title", x=_SAFE_X, y=120, w=_SAFE_W, h=120),
            Zone(role="hero", x=_SAFE_X, y=260, w=_SAFE_W, h=300),
            Zone(role="subtitle", x=_SAFE_X, y=580, w=_SAFE_W, h=60),
        ]
    # dense (default)
    return [
        Zone(role="title", x=_SAFE_X, y=100, w=_SAFE_W, h=80),
        Zone(role="subtitle", x=_SAFE_X, y=190, w=_SAFE_W, h=40),
        Zone(role="body", x=_SAFE_X, y=250, w=_SAFE_W, h=400),
    ]


# Match `P<NN>: rhythm` rows the Strategist embeds in spec_lock under
# `page_rhythm` (markdown form) or as a YAML map.
_RE_PAGE_RHYTHM_LINE = re.compile(
    r"^\s*-?\s*P0*(\d{1,3})\s*[:=]\s*(anchor|dense|breathing)",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_rhythm_from_spec_lock(spec_lock: str) -> dict[int, str]:
    """Pull every `P<NN>: <rhythm-tag>` declaration out of spec_lock.

    Tolerant of both the YAML-map form (``P01: anchor``) and the
    markdown-list form (``- P01: anchor``). Index is 1-based to match
    spec_lock convention.
    """
    if not spec_lock:
        return {}
    out: dict[int, str] = {}
    for m in _RE_PAGE_RHYTHM_LINE.finditer(spec_lock):
        idx = int(m.group(1))
        out[idx] = m.group(2).lower()
    return out


def build_layout_briefs(
    *,
    spec_lock: str,
    page_count: int,
) -> list[PageLayoutBrief]:
    """Generate one ``PageLayoutBrief`` per slide.

    Briefs are 1:1 with the page count. Rhythm is sourced from
    spec_lock when declared; otherwise we default to ``dense`` for
    interior pages and ``anchor`` for the first and last page (cover
    + closing are the canonical anchor positions).
    """
    if page_count <= 0:
        return []
    declared = _parse_rhythm_from_spec_lock(spec_lock)
    briefs: list[PageLayoutBrief] = []
    for i in range(page_count):
        idx_1based = i + 1
        rhythm = declared.get(idx_1based)
        if rhythm is None:
            rhythm = "anchor" if i == 0 or i == page_count - 1 else "dense"
        zones = _zones_for_rhythm(rhythm) + [_chapter_label_zone()] + _footer_zones()
        briefs.append(
            PageLayoutBrief(
                page_index=i,
                page_id=f"P{idx_1based:02d}",
                rhythm=rhythm,
                zones=zones,
            )
        )
    return briefs


def render_brief_yaml(brief: PageLayoutBrief) -> str:
    """Render a brief as the YAML block that goes into the Executor's
    user message. The schema is deliberately flat so the LLM can
    consume it without needing to track nested keys."""
    import yaml

    body = brief.to_dict()
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False).strip()
