"""Keyword search service package.

Exports are resolved lazily so that ``python -m services.search.server`` does
not import the server module twice (which triggers runpy warnings).
"""

from __future__ import annotations

__all__ = ["SearchClient", "SearchEngine", "SearchGrpcServer", "SearchQueryError"]


def __getattr__(name: str):
    if name == "SearchClient":
        from .client import SearchClient

        return SearchClient
    if name in {"SearchEngine", "SearchQueryError"}:
        from .engine import SearchEngine, SearchQueryError

        return {"SearchEngine": SearchEngine, "SearchQueryError": SearchQueryError}[name]
    if name == "SearchGrpcServer":
        from .server import SearchGrpcServer

        return SearchGrpcServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
