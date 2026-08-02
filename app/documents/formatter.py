"""Telegram-safe document messages and secret redaction."""

import re
from .models import DocumentChunk, DocumentSession

PATTERNS=(re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),re.compile(r"(?i)(password\s*[=:]\s*)\S+"),re.compile(r"-----BEGIN[\s\S]{0,80}PRIVATE KEY-----"))
def redact(text):
    found=False
    for p in PATTERNS:
        text,n=p.subn(lambda m:(m.group(1) if m.lastindex else "")+"[СКРЫТО]",text); found |= bool(n)
    return text+("\n\n⚠️ Потенциальные секреты в ответе замаскированы." if found else "")
def provenance(chunk:DocumentChunk):
    p=chunk.provenance; bits=[]
    if p.page:bits.append(f"стр. {p.page}")
    if p.sheet:bits.append(f"лист {p.sheet}")
    if p.row_start:bits.append(f"строки {p.row_start}"+(f"–{p.row_end}" if p.row_end!=p.row_start else ""))
    if p.section:bits.append(p.section)
    return ", ".join(bits) or f"фрагмент {chunk.index+1}"
def received(s:DocumentSession):
    if s.document_type=="image":
        return f"Изображение получено: {s.original_filename}\nРазмер: {s.file_size/1048576:.1f} МБ\n\nМожно попросить описать изображение, прочитать видимый текст или объяснить схему."
    details=f"Страниц: {s.extracted_page_count}\n" if s.extracted_page_count else ""
    return f"Файл получен: {s.original_filename}\nТип: {s.document_type.upper()}\nРазмер: {s.file_size/1048576:.1f} МБ\n{details}Текст успешно извлечён.\n\nМожно спросить:\n— Кратко о чём документ?\n— Найди нужный пункт.\n— Какие основные положения?"
