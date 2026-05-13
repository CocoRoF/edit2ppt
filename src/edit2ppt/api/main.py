"""FastAPI application root.

This is the M0 scaffold: health check + i18n catalog wiring + dev-mode auth stub.
Real routes (assets, jobs, MCP) land in M3 / M4.

See ppt-master-analysis/04-integration-plan.md for the layered architecture
and ppt-master-analysis/05-roadmap.md for the milestone breakdown.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..i18n import MessageCatalog, default_catalog, normalize_locale

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup / shutdown hooks. Stubs for M0."""
    settings = get_settings()
    logger.info(
        "edit2ppt starting",
        extra={
            "environment": settings.environment,
            "default_lang": settings.default_lang,
        },
    )
    # Eagerly load the i18n catalog so YAML errors fail fast at startup.
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


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_request_locale(
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = ...,
) -> str:
    """Resolve the user's preferred locale from headers, falling back to default."""
    if accept_language:
        primary = accept_language.split(",")[0].split(";")[0].strip()
        return normalize_locale(primary, default=settings.default_lang)
    return normalize_locale(settings.default_lang, default=settings.default_lang)


def require_api_key(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = ...,
    catalog: Annotated[MessageCatalog, Depends(default_catalog)] = ...,
    locale: Annotated[str, Depends(get_request_locale)] = ...,
) -> str:
    """M0 stub: validate a single dev API key from settings.

    Real tenant key validation lands in M6. The function still returns the
    raw key string so downstream handlers can attribute usage once tenants exist.
    """
    if not settings.auth_dev_api_key:
        # Auth disabled in dev when no key is configured. Skip enforcement.
        return "anonymous-dev"

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": catalog.get("errors.unauthorized", locale),
                "message_en": catalog.get("errors.unauthorized", "en-US"),
            },
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != settings.auth_dev_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": catalog.get("errors.unauthorized", locale),
                "message_en": catalog.get("errors.unauthorized", "en-US"),
            },
        )
    return token.strip()


# ---------------------------------------------------------------------------
# Exception handler: bilingual error bodies
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap HTTPException details into the bilingual error envelope.

    If `detail` is already a dict containing `code` + `message`, pass it through.
    Otherwise wrap a plain string detail.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        body = {"error": detail}
    else:
        body = {
            "error": {
                "code": "HTTP_ERROR",
                "message": str(detail) if detail else "HTTP error",
                "message_en": str(detail) if detail else "HTTP error",
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body)


# ---------------------------------------------------------------------------
# Routes (M0)
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness + readiness probe. M0 stub: only confirms the process is up.

    M3 will extend this to check DB / Redis / Storage connectivity.
    """
    return {"status": "ok"}


@app.get("/v1/locales", tags=["meta"])
async def list_locales(
    catalog: Annotated[MessageCatalog, Depends(default_catalog)],
) -> dict:
    """Locales backed by a message catalog file."""
    return {"locales": catalog.supported_locales()}


@app.get("/v1/messages/sample", tags=["meta"])
async def sample_message(
    locale: Annotated[str, Depends(get_request_locale)],
    catalog: Annotated[MessageCatalog, Depends(default_catalog)],
) -> dict:
    """Demo endpoint: render the same message in the requested locale and English.

    Useful for verifying the catalog wiring end-to-end before real routes exist.
    Try: `curl -H 'Accept-Language: ko-KR' /v1/messages/sample`
    """
    return {
        "locale": locale,
        "stage_message": catalog.get("stages.executing_page", locale, page=3, total=10),
        "stage_message_en": catalog.get("stages.executing_page", "en-US", page=3, total=10),
        "error_example": catalog.get(
            "errors.invalid_source_format", locale, format="rtf", allowed="pdf, docx, pptx, xlsx"
        ),
    }
