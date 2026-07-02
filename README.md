# edit2ppt

**AI-agent-native PPT generation server. Korean-language-first. MCP-ready.**

[한국어 README](./README.ko.md) · [Built on ppt-master by Hugo He (MIT)](https://github.com/hugohe3/ppt-master)

---

`edit2ppt` is an AI-agent-native PPTX engine: generate decks from a one-line
intent, chat-edit existing files slide by slide, render previews, and patch
text deterministically — all producing real, natively editable PowerPoint.

**Use it four ways** (same engine, pick your surface):

```bash
pip install edit2ppt              # library + agent tools + local MCP
pip install "edit2ppt[server]"    # + the hosted multi-tenant service
```

### 1 · Python library

```python
from edit2ppt import generate_pptx, edit_pptx, preview_pptx, set_pptx_text, analyze_pptx

generate_pptx("Q3 영업 결과 임원 보고", output="deck.pptx")          # intent → deck
r = edit_pptx("deck.pptx", "3번 슬라이드 제목을 'Q3 요약'으로 바꿔줘")  # one chat turn
svgs = preview_pptx(r.path)                                          # slides → SVG
info = analyze_pptx(r.path)                                          # text outline + addresses
set_pptx_text(r.path, [{"slide": 0, "shape_id": 2, "para": 0,
                        "new_text": "새 제목"}])                      # no-LLM, instant
```

BYOK: `api_key=...` or `ANTHROPIC_API_KEY`. `preview/set_text/analyze` need no key.

### 2 · Agent tools (function calling)

```python
from edit2ppt.agent_tools import ANTHROPIC_TOOLS, run_tool

msg = client.messages.create(model="claude-opus-4-7", tools=ANTHROPIC_TOOLS, ...)
for block in msg.content:
    if block.type == "tool_use":
        result = run_tool(block.name, block.input)
```

### 3 · Local MCP server (zero infra)

```jsonc
// Claude Desktop / Claude Code / Cursor
{ "mcpServers": { "edit2ppt": {
    "command": "edit2ppt-mcp",
    "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
} } }
```

Five tools over local files: `generate_pptx`, `edit_pptx`, `preview_pptx`,
`set_pptx_text`, `analyze_pptx`. No database, no storage, no server.

### 4 · Hosted service

`edit2ppt serve` runs the FastAPI service (REST + SSE jobs + hosted MCP) that
powers the [web studio](https://hrletsgo.me/edit2ppt) — multi-tenant assets,
job queue, S3-compatible storage.

Two things make it different from ppt-master:

1. **Agent-native at every layer.** The same stateless tool functions back the
   library, the function-calling schemas, both MCP servers and the hosted API.
2. **Korean-native.** Hangul text width, Korean fonts, OOXML `lang="ko-KR"`,
   bilingual error messages, and Korean layout templates ship out of the box.

## Use your own PPTX as the template

Upload any `.pptx` via `POST /v1/assets` (or the MCP `upload_source` tool) and
pass its id as `template_asset_id` on `generate-deck`:

- `deck_mode: "template_restyle"` — a **fresh deck built inside your package**:
  your slide masters, layouts, theme colors and fonts are preserved (background
  chrome / logos render behind every generated slide); the original slides are
  removed. The Strategist receives a deterministic analysis of your theme
  (colors / fonts / canvas / tone samples) and designs to match it.
- `deck_mode: "template_extend"` — the generated slides (native DrawingML
  shapes, charts, icons and SVG-derived graphics) are **appended after your
  existing slides**, so the deck grows in place.

16:9 and 4:3 templates are supported; generated coordinates are rescaled to the
host deck's exact slide dimensions.

## Chat-edit an existing deck

Two endpoints power the web studio's "PPT 같이 만들기" (and are exposed over
MCP as `edit_deck`):

- `POST /v1/preview` — deterministic, synchronous: every slide rendered to a
  self-contained SVG (masters/layouts inlined, images base64-embedded) for
  browser display.
- `POST /v1/jobs/edit-deck` — one chat turn: an LLM planner turns the
  instruction + deck outline into slide-level operations (edit / add /
  delete), one LLM call per touched slide rewrites its SVG, then a
  deterministic recompose splices the result back into the package. Untouched
  slides keep their identity (ids, notes, animations). Each turn produces a
  new pptx asset; the prior revision is preserved. Question-only turns answer
  in chat without changing the deck.

## Architecture (at a glance)

```
External Agent (Claude / Cursor / your bot)
        │ MCP (HTTP+SSE)
        ▼
┌─────────────────────────────────────────────────┐
│  edit2ppt server                                │
│   MCP routes  ─┐                                │
│   REST routes ─┤── Job queue (arq + Redis) ──┐  │
│                │                              │  │
│                ▼                              ▼  │
│        Tool functions (Python)        Workers   │
│                │                              │  │
│                ▼                              ▼  │
│   Core engine (ppt-master fork + Korean patches) │
│                                                  │
│   PostgreSQL · Object storage (S3) · Redis      │
└─────────────────────────────────────────────────┘
```

See [`ppt-master-analysis/`](./ppt-master-analysis/) for the full design dossier
(philosophy, pipeline, gap analysis, integration plan, roadmap, conventions).

## Status

| Milestone | What works | What's coming |
|-----------|-----------|---------------|
| M0 | Package skeleton, i18n catalog, FastAPI scaffold, health endpoint, ASCII-paths lint | — |
| M1 | Core engine in `src/edit2ppt/core/`, Chinese assets renamed to English, G1/G2/G3 Korean Critical patches + 66 unit tests pass | — |
| M2 | Layer 2 Tool functions (convert/strategize/execute/quality/export/audio) + Anthropic SDK BYOK + 1-shot `generate_deck` orchestrator + 77 tests | — |
| M3 | `docker-compose.yml` + Postgres/Redis/MinIO + SQLAlchemy + Alembic + S3 storage + Korean filename roundtrip + Asset/Job/SSE endpoints + 119 tests | — |
| M4 | MCP server (stdio + HTTP+SSE + Streamable HTTP) with `list_templates`, `list_voices`, `upload_source`, `get_asset`, `download_url`, `generate_deck` (with progress notifications) + Claude Desktop / Cursor guide + 149 tests | — |
| M5 | — | Korean prompts/templates (`*.ko.md`) |
| M6 | — | Auth / multi-tenant / observability |
| M7 | — | Korean layout templates + branding |

## Bilingual conventions (load-bearing)

Two tracks, strictly separated:

| Track | Where | Language |
|-------|-------|----------|
| **A** | filesystem, code identifiers, DB schema, API path, storage keys | **English ASCII only** |
| **B** | UI text, error messages, MCP tool descriptions | **Korean + English, paired** |
| **C** | user content, slide text, speaker notes, TTS | **user's language, preserved** |

Enforced by a pre-commit ASCII lint and a unit test. See
[`ppt-master-analysis/06-bilingual-conventions.md`](./ppt-master-analysis/06-bilingual-conventions.md).

## Connecting an AI agent via MCP

Once `docker compose up -d` is running and the dev server is up, agents
(Claude Desktop, Cursor, etc.) can connect over either transport.

**Local stdio** — agent launches edit2ppt as a subprocess:

```json
{
  "mcpServers": {
    "edit2ppt": {
      "command": "/path/to/edit2ppt/.venv/bin/python",
      "args": ["-m", "edit2ppt.mcp.stdio_main"]
    }
  }
}
```

**Remote HTTP** — agent calls `https://your-host/mcp` (Streamable HTTP) or
`/mcp-sse/sse` (legacy SSE). See [docs/mcp-clients.md](docs/mcp-clients.md)
for full configuration including BYOK + auth headers.

## Development

Requires Python 3.11+. Use [`uv`](https://github.com/astral-sh/uv) for fast
dependency management.

```bash
# Create env + install deps
uv venv .venv
uv pip install --python .venv/bin/python -e .[dev]

# Run tests
.venv/bin/python -m pytest

# ASCII path lint
.venv/bin/python scripts/lint_ascii_paths.py

# Run dev server (once dependencies are installed)
.venv/bin/python -m edit2ppt.cli serve --reload
# → http://localhost:8000/health
# → http://localhost:8000/v1/messages/sample (with Accept-Language: ko-KR)
```

## License

[MIT](./LICENSE). Built on top of [ppt-master](https://github.com/hugohe3/ppt-master)
by Hugo He, also MIT-licensed. Attribution preserved.

## Acknowledgments

- [ppt-master](https://github.com/hugohe3/ppt-master) — the SVG-to-OOXML
  conversion engine that powers our Layer 1.
- SVG Repo · Tabler Icons · Simple Icons · Phosphor Icons — icon libraries
  inherited from ppt-master.
