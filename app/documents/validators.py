"""Strict validation of Telegram-sourced files."""

from dataclasses import dataclass
from pathlib import Path
import re


ALLOWED = {
    ".txt": {"text/plain"}, ".md": {"text/markdown", "text/plain"},
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".csv": {"text/csv", "text/plain", "application/csv"},
    ".json": {"application/json", "text/json", "text/plain"},
    ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"},
    ".png": {"image/png"}, ".webp": {"image/webp"},
}
MAGIC = {".pdf": (b"%PDF-",), ".jpg": (b"\xff\xd8\xff",),
         ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG\r\n\x1a\n",),
         ".webp": (b"RIFF",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",)}


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message); self.code = code; self.user_message = message


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    safe_filename: str
    extension: str
    document_type: str


class DocumentValidator:
    def __init__(self, max_bytes: int): self.max_bytes = max_bytes

    @staticmethod
    def safe_filename(name: str | None) -> str:
        raw = Path((name or "document").replace("\\", "/")).name
        stem = re.sub(r"[^A-Za-z0-9А-Яа-я._ -]", "_", raw).strip(" .")
        return (stem[:180] or "document")

    def validate_metadata(self, name: str | None, mime: str | None, size: int) -> ValidatedFile:
        if size < 0 or size > self.max_bytes:
            raise ValidationError("FILE_TOO_LARGE", "Файл слишком большой. Максимальный размер — %.0f МБ." % (self.max_bytes / 1048576))
        safe = self.safe_filename(name)
        ext = Path(safe).suffix.lower()
        if ext not in ALLOWED:
            raise ValidationError("UNSUPPORTED_FORMAT", "Этот формат файла пока не поддерживается.")
        if (mime or "").lower() not in ALLOWED[ext]:
            raise ValidationError("MIME_MISMATCH", "Тип файла не соответствует его расширению.")
        return ValidatedFile(safe, ext, {".jpg":"image", ".jpeg":"image", ".png":"image", ".webp":"image"}.get(ext, ext[1:]))

    def validate_content(self, path: Path, extension: str) -> None:
        head = path.read_bytes()[:16]
        signatures = MAGIC.get(extension)
        if signatures and not any(head.startswith(value) for value in signatures):
            raise ValidationError("MAGIC_MISMATCH", "Содержимое файла не соответствует заявленному формату.")
        if extension == ".webp" and (len(head) < 12 or head[8:12] != b"WEBP"):
            raise ValidationError("MAGIC_MISMATCH", "Содержимое файла не соответствует WEBP.")
