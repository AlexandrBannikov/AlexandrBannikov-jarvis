"""Document domain models."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class Provenance:
    page: int | None = None
    sheet: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section: str | None = None


@dataclass(slots=True)
class ExtractedPart:
    text: str
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class DocumentChunk:
    index: int
    text: str
    provenance: Provenance
    normalized_tokens: frozenset[str]


@dataclass(slots=True)
class DocumentSession:
    id: str
    user_id: int
    chat_id: int
    telegram_message_id: int
    telegram_file_id_hash: str
    original_filename: str
    safe_filename: str
    mime_type: str
    file_size: int
    sha256: str
    status: str
    document_type: str
    extracted_char_count: int
    extracted_page_count: int
    created_at: datetime
    expires_at: datetime
    last_accessed_at: datetime
    error_code: str | None
    is_active: bool
    file_path: Path | None = None
