"""edit2ppt — AI-agent-native PPTX engine. Korean-first.

Four ways to use it:

* **Library** — ``from edit2ppt import generate_pptx, edit_pptx, ...``
  (zero infra, works on local files)
* **Agent tools** — ``from edit2ppt.agent_tools import ANTHROPIC_TOOLS,
  run_tool`` (function-calling schemas + dispatcher)
* **Local MCP** — ``edit2ppt-mcp`` console script (stdio, zero infra)
* **Hosted service** — ``pip install "edit2ppt[server]"`` then
  ``edit2ppt serve`` (FastAPI + jobs + MCP over HTTP; powers the studio)

Exports resolve lazily so ``import edit2ppt`` stays fast and the base
install never touches server-only dependencies.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.5.0"

_LAZY: dict[str, str] = {
    # High-level facade (simple.py)
    "generate_pptx": ".simple",
    "edit_pptx": ".simple",
    "preview_pptx": ".simple",
    "set_pptx_text": ".simple",
    "analyze_pptx": ".simple",
    "async_generate_pptx": ".simple",
    "async_edit_pptx": ".simple",
    "GenerateResult": ".simple",
    "EditResult": ".simple",
    "TextEditsResult": ".simple",
    # Agent tool surface
    "ANTHROPIC_TOOLS": ".agent_tools",
    "run_tool": ".agent_tools",
    "run_tool_async": ".agent_tools",
}

__all__ = ["__version__", *sorted(_LAZY)]


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
