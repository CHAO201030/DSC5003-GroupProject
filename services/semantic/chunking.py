"""Sentence-aware document chunking for embedding."""

from __future__ import annotations

import re

_BOUNDARIES = "。！？!?.;；\n"
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_NEWLINES_RE = re.compile(r"\n{2,}")


def chunk_text(
    text: str,
    *,
    chunk_size: int = 400,
    overlap: int = 80,
    max_chunks: int = 200,
) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n", text).strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    total = len(text)
    while start < total and len(chunks) < max_chunks:
        hard_end = min(start + chunk_size, total)
        end = hard_end
        if hard_end < total:
            window_start = max(start + chunk_size // 2, start + 1)
            window = text[window_start:hard_end]
            boundary = -1
            for char in _BOUNDARIES:
                position = window.rfind(char)
                if position != -1:
                    boundary = max(boundary, window_start + position + 1)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break
        start = max(start + 1, end - overlap)

    return chunks
