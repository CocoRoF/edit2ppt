"""Object storage layer for edit2ppt: pluggable adapters (S3, in-memory).

S3Storage is the production backend (works against AWS S3, MinIO, Cloudflare R2).
InMemoryStorage is for tests. Both implement ObjectStorage.

See ppt-master-analysis/04-integration-plan.md §4.7 and
ppt-master-analysis/06-bilingual-conventions.md §6.6.3 for the
Korean-filename-on-download story.
"""

from .base import ObjectStorage, PresignedUrl, StoredObject, assert_ascii_key
from .content_disposition import build_content_disposition
from .memory import InMemoryStorage

# S3 is imported lazily so test environments without aioboto3 still work.
__all__ = [
    "ObjectStorage",
    "PresignedUrl",
    "StoredObject",
    "assert_ascii_key",
    "build_content_disposition",
    "InMemoryStorage",
    "get_default_storage",
]


def get_default_storage() -> ObjectStorage:
    """Return a process-wide S3Storage singleton (production)."""
    from .s3 import S3Storage  # local import to avoid aioboto3 at module load

    if not hasattr(get_default_storage, "_instance"):
        get_default_storage._instance = S3Storage()  # type: ignore[attr-defined]
    return get_default_storage._instance  # type: ignore[attr-defined]
