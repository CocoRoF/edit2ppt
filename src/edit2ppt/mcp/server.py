"""MCP server factory.

Builds a FastMCP application with the tools registered for the current
milestone. The same factory is reused by:
  - stdio transport: for local agents (Claude Desktop / Cursor) that
    spawn the server as a subprocess
  - HTTP+SSE transport (M4.4): for remote agents that just need a URL

Tests construct the server in-process and call tools through MCP's
in-memory client (no actual transport).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..db.models import AssetKind
from ..services.assets import (
    AssetError,
    UploadResult,
    _safe_ext,
    _storage_key,
    build_download,
    get_asset,
    upload_asset,
)
from ..db.models import Asset
from . import catalog
from .context import MCPContext, get_default_context


def build_mcp_server(context: MCPContext | None = None) -> FastMCP:
    """Construct and return a fresh FastMCP server.

    Pass an explicit *context* (e.g. in tests) to override the process-wide
    default. The MCP tools acquire DB sessions / storage / tenant from this
    context exactly once per tool call.
    """
    ctx_provider = context or get_default_context()

    mcp = FastMCP(
        name="edit2ppt",
        instructions=(
            "edit2ppt generates editable Korean-first PowerPoint decks. "
            "Discover templates with `list_templates`, narration voices with "
            "`list_voices`. Upload sources with `upload_source` (small files inline, "
            "or `request_upload_url` for larger files). Look up asset metadata with "
            "`get_asset` and produce signed download URLs with `download_url`."
        ),
    )

    # ---- Catalog tools --------------------------------------------------

    @mcp.tool(
        name="hello",
        description=(
            "Health check. Returns service identity and the list of MCP tools. "
            "Use this first to verify a remote edit2ppt server is reachable."
        ),
    )
    def hello() -> dict[str, Any]:
        return {
            "service": "edit2ppt",
            "ok": True,
            "tools": [t.name for t in mcp._tool_manager.list_tools()],
        }

    @mcp.tool(
        name="list_templates",
        description=(
            "List the layout templates on this server. Use a returned `name` as "
            "`template_name` later in generate_deck."
        ),
    )
    def list_templates(locale: str = "ko-KR") -> dict[str, Any]:
        return {"templates": catalog.list_templates(locale=locale)}

    @mcp.tool(
        name="list_voices",
        description=(
            "List curated Edge-TTS voices, optionally filtered by `lang` "
            "(e.g. 'ko-KR' or 'ko'). Use a returned `voice_id` in the narration step."
        ),
    )
    def list_voices(lang: str | None = None) -> dict[str, Any]:
        return {"voices": catalog.list_voices(lang=lang)}

    # ---- Asset tools ----------------------------------------------------

    @mcp.tool(
        name="upload_source",
        description=(
            "Upload a small source file (PDF / DOCX / PPTX / XLSX / image) inline as "
            "base64-encoded bytes. Korean filenames are preserved end-to-end (stored "
            "on the asset row as `original_filename`; the object storage key is "
            "always ASCII). For files larger than ~10MB, prefer `request_upload_url` "
            "and PUT directly to the presigned URL instead."
        ),
    )
    async def upload_source(
        filename: str,
        content_base64: str,
        mime_type: str | None = None,
        kind: str = "source",
    ) -> dict[str, Any]:
        try:
            content = base64.b64decode(content_base64, validate=True)
        except binascii.Error as exc:
            raise AssetError(f"Invalid base64 content: {exc}") from exc

        async with ctx_provider.scope() as scope:
            try:
                kind_enum = AssetKind(kind)
            except ValueError as exc:
                raise AssetError(
                    f"Unknown asset kind {kind!r}. Valid: "
                    + ", ".join(k.value for k in AssetKind)
                ) from exc

            result: UploadResult = await upload_asset(
                session=scope.session,
                storage=scope.storage,
                tenant=scope.tenant,
                kind=kind_enum,
                content=content,
                original_filename=filename,
                mime_type=mime_type,
            )

            return {
                "asset_id": str(result.asset.id),
                "kind": result.asset.kind.value,
                "original_filename": result.asset.original_filename,
                "storage_key": result.asset.storage_key,
                "mime_type": result.asset.mime_type,
                "size": result.asset.size,
                "sha256": result.sha256,
            }

    @mcp.tool(
        name="request_upload_url",
        description=(
            "Allocate a presigned PUT URL for a large source upload. Returns "
            "`{ asset_id, upload_url, storage_key, expires_in_seconds }`. The "
            "caller PUTs the file bytes directly to `upload_url` within the TTL. "
            "Korean filenames are preserved in the registered asset row."
        ),
    )
    async def request_upload_url(
        filename: str,
        mime_type: str = "application/octet-stream",
        kind: str = "source",
        expires_in_seconds: int = 300,
    ) -> dict[str, Any]:
        try:
            kind_enum = AssetKind(kind)
        except ValueError as exc:
            raise AssetError(f"Unknown asset kind: {kind!r}") from exc

        if not (30 <= expires_in_seconds <= 3600):
            raise AssetError("expires_in_seconds must be between 30 and 3600.")

        async with ctx_provider.scope() as scope:
            asset_id = uuid.uuid4()
            ext = _safe_ext(filename, fallback_mime=mime_type)
            storage_key = _storage_key(scope.tenant.id, kind_enum, asset_id, ext)
            presigned = await scope.storage.presigned_put_url(
                storage_key,
                expires_in_seconds=expires_in_seconds,
                content_type=mime_type,
            )
            row = Asset(
                id=asset_id,
                tenant_id=scope.tenant.id,
                kind=kind_enum,
                original_filename=filename,
                storage_key=storage_key,
                mime_type=mime_type,
                size=0,
            )
            scope.session.add(row)
            await scope.session.flush()
            return {
                "asset_id": str(asset_id),
                "storage_key": storage_key,
                "upload_url": presigned.url,
                "expires_in_seconds": presigned.expires_in_seconds,
            }

    @mcp.tool(
        name="get_asset",
        description=(
            "Look up an asset's metadata by `asset_id`. Returns kind, size, mime, "
            "original_filename (Korean preserved), storage_key (ASCII), sha256, "
            "and timestamps. Use this to confirm an upload landed."
        ),
    )
    async def get_asset_tool(asset_id: str) -> dict[str, Any]:
        try:
            aid = uuid.UUID(asset_id)
        except ValueError as exc:
            raise AssetError(f"asset_id must be a valid UUID: {asset_id!r}") from exc

        async with ctx_provider.scope() as scope:
            asset = await get_asset(session=scope.session, tenant=scope.tenant, asset_id=aid)
            return {
                "asset_id": str(asset.id),
                "kind": asset.kind.value,
                "original_filename": asset.original_filename,
                "storage_key": asset.storage_key,
                "mime_type": asset.mime_type,
                "size": asset.size,
                "sha256": asset.sha256,
                "created_at": asset.created_at.isoformat(),
            }

    @mcp.tool(
        name="download_url",
        description=(
            "Issue a short-lived signed GET URL for downloading an asset. The "
            "URL carries a Content-Disposition that restores the original Korean "
            "filename when the user agent saves the file."
        ),
    )
    async def download_url(asset_id: str, expires_in_seconds: int = 300) -> dict[str, Any]:
        try:
            aid = uuid.UUID(asset_id)
        except ValueError as exc:
            raise AssetError(f"asset_id must be a valid UUID: {asset_id!r}") from exc
        if not (30 <= expires_in_seconds <= 3600):
            raise AssetError("expires_in_seconds must be between 30 and 3600.")

        async with ctx_provider.scope() as scope:
            info = await build_download(
                session=scope.session,
                storage=scope.storage,
                tenant=scope.tenant,
                asset_id=aid,
                expires_in_seconds=expires_in_seconds,
            )
            return {
                "download_url": info.url,
                "expires_in_seconds": info.expires_in_seconds,
                "filename": info.filename,
                "mime_type": info.mime_type,
            }

    return mcp


__all__ = ["build_mcp_server"]
