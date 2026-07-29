"""Deterministic keyword ranking for local project memory."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from app.memory.models import MemoryRecord


_WORD = re.compile(r"[\w-]{2,}", re.UNICODE)


def terms(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text)}


def rank_memory(record: MemoryRecord, query: str) -> float:
    query_terms = terms(query)
    title_terms = terms(record.title)
    content_terms = terms(record.content)
    tag_terms = {tag.casefold() for tag in record.tags}
    overlap = (
        4 * len(query_terms & title_terms)
        + 2 * len(query_terms & tag_terms)
        + len(query_terms & content_terms)
    )
    phrase_bonus = (
        5
        if len(query_terms) > 1
        and query.casefold() in record.content.casefold()
        else 0
    )
    importance = record.importance * 0.6
    frequency = min(record.use_count, 20) * 0.1
    recency = 0.0
    timestamp = record.last_used_at or record.updated_at
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - moment).total_seconds() / 86400,
        )
        recency = max(0.0, 2.0 - age_days / 30)
    except ValueError:
        pass
    return overlap + phrase_bonus + importance + frequency + recency
