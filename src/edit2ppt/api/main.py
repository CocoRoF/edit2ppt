"""FastAPI application root.

Wires the lifespan (DB init), routers, and bilingual error handlers.

Routes:
  GET  /health                       — liveness probe
  GET  /v1/locales                   — supported message catalog locales
  GET  /v1/messages/sample           — i18n smoke test
  POST /v1/assets                    — multipart upload
  POST /v1/assets/presigned          — presigned PUT URL
  GET  /v1/assets/{id}               — metadata
  GET  /v1/assets/{id}/download      — presigned GET URL (Korean filename safe)
  DELETE /v1/assets/{id}             — delete

See ppt-master-analysis/04-integration-plan.md for the layered architecture
and ppt-master-analysis/05-roadmap.md for the milestone breakdown.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..config import get_settings
from ..i18n import default_catalog
from .dependencies import Catalog, RequestLocale
from .errors import install_error_handlers
from ..mcp.http_transport import mount_mcp
from .routes import assets as assets_routes
from .routes import jobs as jobs_routes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "edit2ppt starting",
        extra={"environment": settings.environment, "default_lang": settings.default_lang},
    )
    catalog = default_catalog()
    logger.info("loaded i18n locales: %s", catalog.supported_locales())
    yield
    logger.info("edit2ppt shutting down")


app = FastAPI(
    title="edit2ppt",
    description=(
        "AI-agent-native PPT generation server. Korean-language-first, "
        "built on top of ppt-master (MIT)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

install_error_handlers(app)
app.include_router(assets_routes.router)
app.include_router(jobs_routes.router)

# MCP transports — mounted at /mcp (Streamable HTTP) and /mcp-sse (SSE).
# Both expose the same FastMCP tool set; agents pick whichever matches their
# spec version. See docs/mcp-clients.md for Claude Desktop / Cursor setup.
mount_mcp(app)


# ---------------------------------------------------------------------------
# Meta routes
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe. M3.5 extends with DB / Redis / Storage checks."""
    return {"status": "ok"}


@app.get("/v1/locales", tags=["meta"])
async def list_locales(catalog: Catalog) -> dict:
    return {"locales": catalog.supported_locales()}


@app.get("/v1/messages/sample", tags=["meta"])
async def sample_message(locale: RequestLocale, catalog: Catalog) -> dict:
    return {
        "locale": locale,
        "stage_message": catalog.get("stages.executing_page", locale, page=3, total=10),
        "stage_message_en": catalog.get("stages.executing_page", "en-US", page=3, total=10),
        "error_example": catalog.get(
            "errors.invalid_source_format", locale, format="rtf", allowed="pdf, docx, pptx, xlsx"
        ),
    }
