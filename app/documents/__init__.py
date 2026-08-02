"""Secure, ephemeral Telegram document analysis."""

from .service import DocumentError, DocumentService
from .storage import DocumentSessionStorage

__all__ = ["DocumentError", "DocumentService", "DocumentSessionStorage"]
