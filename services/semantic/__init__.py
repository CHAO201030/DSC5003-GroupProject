"""Semantic search service package.

Exports are resolved lazily so that ``python -m services.semantic.server`` does
not import the server module twice (which triggers runpy warnings).
"""

from __future__ import annotations

__all__ = [
    "HashingEmbedder",
    "SemanticClient",
    "SemanticEngine",
    "SemanticGrpcServer",
    "SentenceTransformerEmbedder",
    "build_embedder",
]


def __getattr__(name: str):
    if name == "SemanticClient":
        from .client import SemanticClient

        return SemanticClient
    if name in {"HashingEmbedder", "SentenceTransformerEmbedder", "build_embedder"}:
        from .embedder import (
            HashingEmbedder,
            SentenceTransformerEmbedder,
            build_embedder,
        )

        return {
            "HashingEmbedder": HashingEmbedder,
            "SentenceTransformerEmbedder": SentenceTransformerEmbedder,
            "build_embedder": build_embedder,
        }[name]
    if name == "SemanticEngine":
        from .engine import SemanticEngine

        return SemanticEngine
    if name == "SemanticGrpcServer":
        from .server import SemanticGrpcServer

        return SemanticGrpcServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
