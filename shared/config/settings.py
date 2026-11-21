"""Central configuration for all CloudDisk processes.

Nothing security-relevant or environment-specific is hard-coded in the
service code: every value below can be overridden with an environment
variable or a value in ``.env``.  Use ``python-dotenv`` when available.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:  # python-dotenv is optional at runtime but present in environment.yml
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - tiny fallback
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:  # fail fast on malformed configuration
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def default_database_url() -> str:
    """The demo default: a local SQLite file under ``data/``."""
    return f"sqlite:///{PROJECT_ROOT / 'data' / 'clouddisk.db'}"


def normalise_database_url(url: str) -> str:
    """Resolve a relative SQLite URL against the project root.

    ``sqlite:///data/clouddisk.db`` becomes an absolute
    ``sqlite:////.../data/clouddisk.db`` URL, so the demo works regardless of
    the process working directory.  Absolute SQLite URLs and MySQL URLs are
    returned unchanged.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url.startswith("sqlite:////"):
        return url
    if url.startswith("sqlite:///:memory:"):
        return url
    raw = url[len(prefix) :]
    if re.match(r"^[A-Za-z]:[\\/]", raw):  # Windows absolute path
        return url
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return "sqlite:///" + str(path.resolve())


@dataclass(slots=True)
class Settings:
    """Runtime settings shared by the gateway, search and semantic services."""

    app_name: str = "CloudDisk"
    environment: str = "development"

    # --- persistence -----------------------------------------------------
    database_url: str = field(default_factory=default_database_url)
    database_echo: bool = False
    auto_create_tables: bool = True

    data_dir: Path = PROJECT_ROOT / "data"
    object_store_dir: Path = PROJECT_ROOT / "data" / "objects"
    temp_dir: Path = PROJECT_ROOT / "data" / "tmp"
    log_dir: Path = PROJECT_ROOT / "data" / "logs"

    # --- authentication --------------------------------------------------
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    bcrypt_rounds: int = 12

    # --- HTTP ------------------------------------------------------------
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    cors_origins: tuple[str, ...] = ("*",)
    default_page_size: int = 20
    max_page_size: int = 100

    # --- upload / extraction --------------------------------------------
    max_upload_bytes: int = 512 * 1024 * 1024
    max_index_bytes: int = 2 * 1024 * 1024
    max_index_chars: int = 200_000

    # --- keyword search service -----------------------------------------
    enable_search_service: bool = True
    search_host: str = "127.0.0.1"
    search_port: int = 50051
    search_timeout_seconds: float = 5.0
    search_index_path: Path = PROJECT_ROOT / "data" / "search" / "search_index.db"
    search_max_candidates: int = 500
    search_title_weight: float = 8.0
    search_content_weight: float = 1.0
    search_tags_weight: float = 3.0

    # --- semantic service ------------------------------------------------
    enable_semantic_service: bool = True
    semantic_host: str = "127.0.0.1"
    semantic_port: int = 50052
    index_timeout_seconds: float = 30.0
    health_timeout_seconds: float = 2.0
    semantic_embedder: str = "auto"  # auto | sentence-transformers | hash
    semantic_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    semantic_allow_model_download: bool = True
    semantic_model_version: str = ""
    semantic_model_cache_dir: Path = PROJECT_ROOT / "data" / "models"
    semantic_index_path: Path = PROJECT_ROOT / "data" / "faiss" / "semantic.index"
    semantic_index_meta_path: Path = PROJECT_ROOT / "data" / "faiss" / "semantic_meta.json"
    semantic_chunk_size: int = 400
    semantic_chunk_overlap: int = 80
    semantic_max_chunks: int = 200
    semantic_hash_dim: int = 1024
    semantic_min_score: float = 0.15
    semantic_relative_floor: float = 0.35

    def __post_init__(self) -> None:
        self.database_url = normalise_database_url(self.database_url)
        if len(self.jwt_secret) < 16:
            raise ValueError("JWT_SECRET must be at least 16 characters long")
        if self.max_upload_bytes <= 0:
            raise ValueError("MAX_UPLOAD_BYTES must be positive")
        if self.default_page_size <= 0 or self.max_page_size < self.default_page_size:
            raise ValueError("Invalid pagination configuration")
        if self.semantic_chunk_overlap >= self.semantic_chunk_size:
            raise ValueError("SEMANTIC_CHUNK_OVERLAP must be smaller than SEMANTIC_CHUNK_SIZE")

    # -- helpers ----------------------------------------------------------
    @property
    def using_default_jwt_secret(self) -> bool:
        return self.jwt_secret.startswith("dev-only") or self.jwt_secret.startswith("please-change")

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.object_store_dir,
            self.temp_dir,
            self.log_dir,
            self.search_index_path.parent,
            self.semantic_index_path.parent,
            self.semantic_index_meta_path.parent,
            self.semantic_model_cache_dir,
        ):
            Path(path).mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables / ``.env``."""
        load_dotenv(PROJECT_ROOT / ".env", override=False)

        data_dir = _path("DATA_DIR", PROJECT_ROOT / "data")

        def under_data(name: str, suffix: str) -> Path:
            return _path(name, data_dir / suffix)

        origins = tuple(
            origin.strip()
            for origin in _str("CORS_ORIGINS", "*").split(",")
            if origin.strip()
        ) or ("*",)

        return cls(
            environment=_str("APP_ENV", "development"),
            database_url=_str("DATABASE_URL", default_database_url()),
            database_echo=_bool("DATABASE_ECHO", False),
            auto_create_tables=_bool("AUTO_CREATE_TABLES", True),
            data_dir=data_dir,
            object_store_dir=under_data("OBJECT_STORE_DIR", "objects"),
            temp_dir=under_data("TEMP_DIR", "tmp"),
            log_dir=under_data("LOG_DIR", "logs"),
            jwt_secret=_str("JWT_SECRET", "dev-only-insecure-secret-change-me"),
            jwt_algorithm=_str("JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=_int("ACCESS_TOKEN_EXPIRE_MINUTES", 720),
            bcrypt_rounds=_int("BCRYPT_ROUNDS", 12),
            gateway_host=_str("HTTP_HOST", "0.0.0.0"),
            gateway_port=_int("HTTP_PORT", 8000),
            frontend_dir=_path("FRONTEND_DIR", PROJECT_ROOT / "frontend"),
            cors_origins=origins,
            default_page_size=_int("DEFAULT_PAGE_SIZE", 20),
            max_page_size=_int("MAX_PAGE_SIZE", 100),
            max_upload_bytes=_int("MAX_UPLOAD_BYTES", 512 * 1024 * 1024),
            max_index_bytes=_int("MAX_INDEX_BYTES", 2 * 1024 * 1024),
            max_index_chars=_int("MAX_INDEX_CHARS", 200_000),
            enable_search_service=_bool("ENABLE_SEARCH_SERVICE", True),
            search_host=_str("SEARCH_SERVICE_HOST", "127.0.0.1"),
            search_port=_int("SEARCH_SERVICE_PORT", 50051),
            search_timeout_seconds=_float("SEARCH_TIMEOUT_SECONDS", 5.0),
            search_index_path=under_data("SEARCH_INDEX_PATH", "search/search_index.db"),
            search_max_candidates=_int("SEARCH_MAX_CANDIDATES", 500),
            search_title_weight=_float("SEARCH_TITLE_WEIGHT", 8.0),
            search_content_weight=_float("SEARCH_CONTENT_WEIGHT", 1.0),
            search_tags_weight=_float("SEARCH_TAGS_WEIGHT", 3.0),
            enable_semantic_service=_bool("ENABLE_SEMANTIC_SERVICE", True),
            semantic_host=_str("SEMANTIC_SERVICE_HOST", "127.0.0.1"),
            semantic_port=_int("SEMANTIC_SERVICE_PORT", 50052),
            index_timeout_seconds=_float("INDEX_TIMEOUT_SECONDS", 30.0),
            health_timeout_seconds=_float("HEALTH_TIMEOUT_SECONDS", 2.0),
            semantic_embedder=_str("SEMANTIC_EMBEDDER", "auto"),
            semantic_model_name=_str(
                "SEMANTIC_MODEL_NAME",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            semantic_allow_model_download=_bool("SEMANTIC_ALLOW_MODEL_DOWNLOAD", True),
            semantic_model_version=_str("SEMANTIC_MODEL_VERSION", ""),
            semantic_model_cache_dir=under_data("SEMANTIC_MODEL_CACHE_DIR", "models"),
            semantic_index_path=under_data("SEMANTIC_INDEX_PATH", "faiss/semantic.index"),
            semantic_index_meta_path=under_data("SEMANTIC_INDEX_META_PATH", "faiss/semantic_meta.json"),
            semantic_chunk_size=_int("SEMANTIC_CHUNK_SIZE", 400),
            semantic_chunk_overlap=_int("SEMANTIC_CHUNK_OVERLAP", 80),
            semantic_max_chunks=_int("SEMANTIC_MAX_CHUNKS", 200),
            semantic_hash_dim=_int("SEMANTIC_HASH_DIM", 1024),
            semantic_min_score=_float("SEMANTIC_MIN_SCORE", 0.15),
            semantic_relative_floor=_float("SEMANTIC_RELATIVE_SCORE_FLOOR", 0.35),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached). Useful for CLI entry points."""
    return Settings.from_env()
