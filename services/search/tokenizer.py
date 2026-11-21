"""Chinese/English tokenisation for SQLite FTS5.

``jieba.cut_for_search`` is used for indexing and for ordinary queries so that
partial words such as “机器”/“学习” can match “机器学习”.  Precise mode is used
inside quoted phrases.  A small stop-word list removes low-signal terms.
"""

from __future__ import annotations

import logging
import re

try:  # pragma: no cover - exercised implicitly
    import jieba

    jieba.setLogLevel(logging.WARNING)
except Exception:  # pragma: no cover - fallback for minimal installs
    jieba = None

STOPWORDS = {
    # Chinese
    "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "那", "他", "她", "它", "们", "与",
    "及", "或", "而", "但", "并", "被", "把", "让", "从", "对", "为", "以",
    "于", "中", "之", "等", "个", "可以", "这个", "那个", "什么", "如何",
    # English
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "he", "her", "his", "how", "i", "in", "is", "it", "its", "of",
    "on", "or", "she", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "will", "with", "you", "your",
}

_CLEAN_RE = re.compile(r"[^\w㐀-䶿一-鿿]+", re.UNICODE)
_HAS_WORD_RE = re.compile(r"[\w㐀-䶿一-鿿]", re.UNICODE)


def _normalise_token(raw: str) -> str:
    token = raw.strip().lower()
    if not token:
        return ""
    token = _CLEAN_RE.sub("", token)
    if not token or token in STOPWORDS:
        return ""
    if not _HAS_WORD_RE.search(token):
        return ""
    return token


def tokenize(text: str) -> list[str]:
    """Search-mode tokens (overlapping terms, good for Chinese recall)."""
    if not text:
        return []
    if jieba is not None:
        raw_tokens = jieba.cut_for_search(text)
    else:  # pragma: no cover - simple regex fallback
        raw_tokens = re.findall(r"[A-Za-z0-9_]+|[一-鿿]", text)

    tokens: list[str] = []
    for raw in raw_tokens:
        token = _normalise_token(raw)
        if token:
            tokens.append(token)
    return tokens


def tokenize_phrase(text: str) -> list[str]:
    """Precise tokens used for quoted phrase queries."""
    if not text:
        return []
    if jieba is not None:
        raw_tokens = jieba.cut(text, cut_all=False)
    else:  # pragma: no cover
        raw_tokens = text.split()

    tokens = []
    for raw in raw_tokens:
        token = _normalise_token(raw)
        if token:
            tokens.append(token)
    return tokens


def index_text(text: str) -> str:
    """Tokenised text stored in the FTS5 columns."""
    return " ".join(tokenize(text))
