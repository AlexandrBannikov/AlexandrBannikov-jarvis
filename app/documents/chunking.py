"""Bounded local chunking and lexical retrieval."""

import re
from .models import DocumentChunk, ExtractedPart

TOKEN_RE=re.compile(r"[\w-]{2,}", re.UNICODE)
def normalize(text: str) -> frozenset[str]: return frozenset(t.casefold() for t in TOKEN_RE.findall(text))

class DocumentChunker:
    def __init__(self, chunk_chars: int=4000): self.chunk_chars=chunk_chars
    def chunk(self, parts: list[ExtractedPart]) -> list[DocumentChunk]:
        result=[]
        for part in parts:
            paragraphs=[x.strip() for x in re.split(r"\n{2,}",part.text) if x.strip()] or [part.text]
            current=""
            for paragraph in paragraphs:
                while len(paragraph)>self.chunk_chars:
                    piece,paragraph=paragraph[:self.chunk_chars],paragraph[self.chunk_chars:]
                    if current: result.append(self._make(len(result),current,part)); current=""
                    result.append(self._make(len(result),piece,part))
                if current and len(current)+len(paragraph)+2>self.chunk_chars:
                    result.append(self._make(len(result),current,part)); current=""
                current += ("\n\n" if current else "")+paragraph
            if current: result.append(self._make(len(result),current,part))
        return result
    @staticmethod
    def _make(index,text,part): return DocumentChunk(index,text,part.provenance,normalize(text))

    def search(self,chunks,query,*,page=None,sheet=None,limit=12):
        phrase=query.casefold().strip(); tokens=normalize(query); scored=[]
        for chunk in chunks:
            p=chunk.provenance
            if page is not None and p.page!=page: continue
            if sheet is not None and (p.sheet or "").casefold()!=sheet.casefold(): continue
            hay=chunk.text.casefold(); overlap=len(tokens & chunk.normalized_tokens)
            score=overlap*2+(8 if phrase and phrase in hay else 0)+(2 if p.section and phrase in p.section.casefold() else 0)
            if score or page is not None or sheet is not None: scored.append((score,chunk.index,chunk))
        return [x[2] for x in sorted(scored,key=lambda x:(-x[0],x[1]))[:limit]]
