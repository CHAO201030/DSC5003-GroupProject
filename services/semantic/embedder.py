"""Embedding backends for the semantic service.

Primary backend: ``sentence-transformers`` (multilingual MiniLM by default).
Fallback backend: a deterministic hashing embedder (character n-grams + jieba
words) that requires no model download and keeps the service usable offline.
The fallback is intentionally reported as ``degraded`` through the gRPC
responses so the UI can tell users that true semantic similarity is limited.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

from services.search.tokenizer import tokenize

logger = logging.getLogger(__name__)


class Embedder:
    name: str = "base"
    version: str = "base-v1"
    dim: int = 384
    degraded: bool = False

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def _normalise(vectors: np.ndarray) -> np.ndarray:
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        vectors = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms


class SentenceTransformerEmbedder(Embedder):
    """Multilingual sentence-transformer embeddings (normalised, cosine/IP)."""

    def __init__(
        self,
        model_name: str,
        cache_dir: str | Path,
        *,
        allow_download: bool = True,
    ) -> None:
        from sentence_transformers import SentenceTransformer  # lazy heavy import

        self.model_name = model_name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.cache_dir))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self.cache_dir))

        try:
            self._model = SentenceTransformer(
                model_name,
                cache_folder=str(self.cache_dir),
                local_files_only=not allow_download,
            )
        except TypeError:  # older sentence-transformers without local_files_only
            self._model = SentenceTransformer(
                model_name, cache_folder=str(self.cache_dir)
            )

        self.name = model_name
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.version = f"{model_name}@dim{self.dim}"
        self.degraded = False

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return self._normalise(np.asarray(vectors, dtype=np.float32))


class HashingEmbedder(Embedder):
    """Deterministic offline fallback.

    Uses word tokens plus character 2/3-grams hashed into a fixed-size vector.
    It is lexical rather than truly semantic, but it keeps the API, index and
    degradation path fully testable without downloading any model weights.
    """

    _SPACE_RE = re.compile(r"\s+")

    def __init__(self, dim: int = 384) -> None:
        self.dim = max(int(dim), 64)
        self.name = "hash-ngram"
        self.version = f"hash-ngram-v1-d{self.dim}"
        self.degraded = True

    def _features(self, text: str) -> Counter:
        features: Counter = Counter()
        for token in tokenize(text[:50_000]):
            features[f"w:{token}"] += 1.0

        compact = self._SPACE_RE.sub("", text[:50_000])
        for n, weight in ((2, 0.5), (3, 0.35)):
            for index in range(max(0, len(compact) - n + 1)):
                gram = compact[index : index + n]
                if gram.strip():
                    features[f"c{n}:{gram}"] += weight
        return features

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature, weight in self._features(text or "").items():
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "little")
                index = value % self.dim
                sign = 1.0 if (value >> 63) & 1 else -1.0
                matrix[row, index] += sign * float(weight)
        return self._normalise(matrix)


def _cached_model_available(cache_dir: str | Path, model_name: str) -> bool:
    """Cheap check for a complete Hugging Face snapshot in our cache dir.

    This prevents ``auto`` from even importing/downloading when no neural
    model has been fetched yet.
    """
    local_path = Path(model_name).expanduser()
    if local_path.is_dir():
        return True

    cache_root = Path(cache_dir)
    model_root = cache_root / ("models--" + model_name.replace("/", "--"))
    snapshots = model_root / "snapshots"
    if not snapshots.is_dir():
        return False

    weight_files = (
        "model.safetensors",
        "pytorch_model.bin",
        "model.safetensors.index.json",
        "pytorch_model.bin.index.json",
    )
    for snapshot in snapshots.iterdir():
        if not snapshot.is_dir():
            continue
        if not (snapshot / "config.json").is_file():
            continue
        if any((snapshot / name).is_file() for name in weight_files):
            return True
    return False


def build_embedder(
    *,
    backend: str,
    model_name: str,
    cache_dir: str | Path,
    allow_download: bool = True,
    hash_dim: int = 384,
) -> Embedder:
    backend = (backend or "auto").strip().lower()

    if backend in {"hash", "hashing", "offline"}:
        logger.info("Using offline hashing embedder (dim=%s)", hash_dim)
        return HashingEmbedder(dim=hash_dim)

    if backend in {"auto", "sentence-transformers", "sentence_transformers", "st"}:
        # "auto" is offline-first: it uses a model that is already cached but
        # never starts a multi-hundred-megabyte download during service
        # startup.  Run scripts/download_semantic_model.py (or choose the
        # explicit backend) to opt into downloading.
        if backend == "auto" and not _cached_model_available(cache_dir, model_name):
            logger.info(
                "No cached sentence-transformers model for %s in %s; using the "
                "offline hashing embedder. Run "
                "`python scripts/download_semantic_model.py` to enable the neural "
                "model.",
                model_name,
                cache_dir,
            )
            return HashingEmbedder(dim=hash_dim)

        effective_download = allow_download if backend != "auto" else False
        try:
            embedder = SentenceTransformerEmbedder(
                model_name, cache_dir, allow_download=effective_download
            )
            logger.info(
                "Loaded sentence-transformers model %s (dim=%s)",
                model_name,
                embedder.dim,
            )
            return embedder
        except Exception as exc:
            if backend == "auto":
                logger.warning(
                    "No usable cached sentence-transformers model (%s); falling back "
                    "to the offline hashing embedder. Run "
                    "`python scripts/download_semantic_model.py` (or set "
                    "SEMANTIC_EMBEDDER=sentence-transformers) to enable the "
                    "neural model.",
                    exc,
                )
                return HashingEmbedder(dim=hash_dim)
            raise RuntimeError(
                f"无法加载 sentence-transformers 模型 {model_name}: {exc}"
            ) from exc

    raise ValueError(
        "SEMANTIC_EMBEDDER 必须是 auto、sentence-transformers 或 hash"
    )
