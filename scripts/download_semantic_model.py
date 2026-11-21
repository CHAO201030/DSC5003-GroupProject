#!/usr/bin/env python
"""Explicitly download and warm the sentence-transformers model cache.

``SEMANTIC_EMBEDDER=auto`` never downloads a model during service startup; it
uses a cached model if present and otherwise falls back to the offline hashing
embedder.  Run this script once when you want real neural semantic search:

    conda run -n DSC5003 python scripts/download_semantic_model.py

The default model is multilingual and about 471 MB.  A smaller Chinese model
can be selected explicitly, e.g.:

    conda run -n DSC5003 python scripts/download_semantic_model.py \
        --model BAAI/bge-small-zh-v1.5

After the download, restart the semantic service.  If you change the model,
rebuild the semantic index from the UI or ``scripts/rebuild_indexes.py``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.config.settings import Settings  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Download a semantic embedding model")
    parser.add_argument(
        "--model",
        default=settings.semantic_model_name,
        help="sentence-transformers model name",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(settings.semantic_model_cache_dir),
        help="local model cache directory",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir).expanduser()
    if not cache_dir.is_absolute():
        cache_dir = PROJECT_ROOT / cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"model    : {args.model}")
    print(f"cache_dir: {cache_dir}")
    print("downloading / verifying model weights (this may take a while) ...")

    started = time.time()
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(args.model, cache_folder=str(cache_dir))
        dim = int(model.get_sentence_embedding_dimension())
        vectors = model.encode(
            ["人工智能与机器学习", "红烧肉的家常做法"],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"download/verification failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - started
    print(f"ok: dim={dim}, sample_shape={tuple(vectors.shape)}, elapsed={elapsed:.1f}s")
    print(
        "Model is cached. Restart the semantic service; "
        "SEMANTIC_EMBEDDER=auto will now load it."
    )
    if args.model != settings.semantic_model_name:
        print(
            f"NOTE: set SEMANTIC_MODEL_NAME={args.model} in .env so the service "
            "uses this model."
        )
    if str(cache_dir) != str(Path(settings.semantic_model_cache_dir)):
        print(
            f"NOTE: set SEMANTIC_MODEL_CACHE_DIR={cache_dir} in .env so the "
            "service reads this cache."
        )
    print("If this is a different model than before, rebuild the semantic index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
