"""Locale-aware prompt loader.

Prompts live under `src/edit2ppt/core/prompts/<role>.<lang>.md`. The loader
picks the best file for a given lang with this fallback chain:

    <role>.<lang2>.md  (e.g. strategist.ko.md)  ->
    <role>.en.md       (e.g. strategist.en.md)

`lang2` is the 2-letter prefix of the BCP-47 code (ko-KR -> ko). English is
the universal fallback because that's how the ppt-master prompts ship today;
ko / zh / ja variants will appear over M5.

The loader caches file contents in memory — a process-wide LRU keyed by
(role, lang2). Tests can clear the cache by calling `load_prompt.cache_clear()`.
"""

from __future__ import annotations

import functools
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "core" / "prompts"

# Roles whose prompt files must exist in at least the .en.md form.
KNOWN_ROLES: set[str] = {
    "strategist",
    "executor-base",
    "executor-consultant",
    "executor-consultant-top",
    "executor-general",
    "image-base",
    "image-generator",
    "image-searcher",
    "image-layout-spec",
    "template-designer",
    "shared-standards",
    "svg-image-embedding",
    "canvas-formats",
    "animations",
}


@functools.lru_cache(maxsize=64)
def load_prompt(role: str, lang: str = "ko-KR") -> str:
    """Return the markdown content of <role>.<lang2>.md, falling back to .en.md.

    Args:
        role: Prompt role (see KNOWN_ROLES).
        lang: BCP-47 locale; only the language prefix is used for file matching.

    Raises:
        FileNotFoundError: if neither the locale-specific file nor the .en.md
            fallback exists.
    """
    lang2 = (lang.split("-")[0] or "en").lower()
    candidates = [
        PROMPTS_DIR / f"{role}.{lang2}.md",
        PROMPTS_DIR / f"{role}.en.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"No prompt found for role={role!r}, lang={lang!r}. "
        f"Looked in: {[str(p) for p in candidates]}"
    )


def list_available_locales(role: str) -> list[str]:
    """Return the language prefixes for which <role>.<prefix>.md exists."""
    prefix = f"{role}."
    suffix = ".md"
    return sorted(
        p.stem.removeprefix(prefix).removesuffix(".md") for p in PROMPTS_DIR.glob(f"{prefix}*.md")
    ) if PROMPTS_DIR.exists() else []
