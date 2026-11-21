"""Snippet extraction and safe HTML highlighting."""

from __future__ import annotations

import html
import re


class SearchQueryError(ValueError):
    """Raised for malformed user search queries."""


def _unique_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in sorted((t for t in terms if t), key=len, reverse=True):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            result.append(term)
    return result


def make_snippet(text: str, terms: list[str], width: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    first = None
    for term in terms:
        position = lowered.find(term.lower())
        if position != -1 and (first is None or position < first):
            first = position

    if first is None:
        return text[:width] + ("…" if len(text) > width else "")

    start = max(0, first - width // 3)
    end = min(len(text), start + width)
    if end - start < width:
        start = max(0, end - width)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def highlight_html(text: str, terms: list[str], width: int = 180) -> str:
    """Return an HTML-escaped snippet where matches are wrapped in <mark>.

    Only ``<mark>`` is ever emitted as markup, so the output is safe to place
    in ``innerHTML`` in the browser.
    """
    snippet = make_snippet(text, terms, width=width)
    terms = _unique_terms(terms)
    if not snippet:
        return ""
    if not terms:
        return html.escape(snippet, quote=False)

    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    pieces: list[str] = []
    last = 0
    for match in pattern.finditer(snippet):
        pieces.append(html.escape(snippet[last : match.start()], quote=False))
        pieces.append("<mark>" + html.escape(match.group(0), quote=False) + "</mark>")
        last = match.end()
    pieces.append(html.escape(snippet[last:], quote=False))
    return "".join(pieces)
