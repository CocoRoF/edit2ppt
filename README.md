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
| M0 (in progress) | Package skeleton, i18n catalog, FastAPI scaffold, health endpoint | DB + Redis + S3 wiring, docker-compose |
| M1 (in progress) | Core engine imported, Chinese assets renamed to English, G1/G2/G3 Korean patches applied + 66 unit tests pass | Korean prompt variants |
| M2 | — | Tool functions + Anthropic SDK (BYOK) |
| M3 | — | REST API + Job queue + SSE |
| M4 | — | MCP server (HTTP+SSE) |
| M5–M7 | — | Korean prompts/templates, multi-tenant ops, branding |

## Bilingual conventions (load-bearing)

Two tracks, strictly separated:

| Track | Where | Language |
|-------|-------|----------|
| **A** | filesystem, code identifiers, DB schema, API path, storage keys | **English ASCII only** |
| **B** | UI text, error messages, MCP tool descriptions | **Korean + English, paired** |
| **C** | user content, slide text, speaker notes, TTS | **user's language, preserved** |

Enforced by a pre-commit ASCII lint and a unit test. See
[`ppt-master-analysis/06-bilingual-conventions.md`](./ppt-master-analysis/06-bilingual-conventions.md).

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
