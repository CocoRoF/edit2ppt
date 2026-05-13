# edit2ppt

**AI-agent-native PPT generation server. Korean-language-first. MCP-ready.**

[한국어 README](./README.ko.md) · [Built on ppt-master by Hugo He (MIT)](https://github.com/hugohe3/ppt-master)

---

`edit2ppt` is a hosted PPT generation engine. Instead of installing a local skill
and asking your IDE's LLM to drive it, you point any MCP-capable agent at our
server URL. The agent gains a small set of tools (`generate_deck`,
`upload_source`, `list_templates`) and produces real, editable PPTX files.

Two things make it different from ppt-master:

1. **Server-side, not local.** Nothing to install on the user side. File I/O is
   over HTTPS; storage is S3-compatible; jobs run in workers.
2. **Korean-native.** Hangul text width, Korean fonts, OOXML `lang="ko-KR"`,
   bilingual error messages, and Korean layout templates ship out of the box.

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
