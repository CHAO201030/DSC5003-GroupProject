"""MD5 content-addressed local object storage.

Layout (two-level fan-out as required):

    data/objects/ab/cd/abcdef...full-md5

Temporary uploads live in ``data/tmp`` on the same filesystem so that the
final move is an atomic ``os.replace``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from shared.config.settings import Settings
from shared.errors import StorageIntegrityError

logger = logging.getLogger(__name__)

MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.object_store_dir)
        self.temp_dir = Path(settings.temp_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # -- paths -----------------------------------------------------------
    @staticmethod
    def rel_key(md5: str) -> str:
        return f"{md5[:2]}/{md5[2:4]}/{md5}"

    def path_for(self, md5: str) -> Path:
        if not MD5_PATTERN.match(md5 or ""):
            raise StorageIntegrityError("非法的 MD5 值")
        return self.root / md5[:2] / md5[2:4] / md5

    def exists(self, md5: str) -> bool:
        return self.path_for(md5).is_file()

    def size(self, md5: str) -> int:
        return self.path_for(md5).stat().st_size

    # -- write / delete --------------------------------------------------
    def persist_temp(self, temp_path: str | Path, md5: str, size: int) -> bool:
        """Move a verified temporary file into the object store.

        Returns ``True`` when a new physical file was created, ``False`` when
        identical content already existed (the temporary file is discarded).
        """
        temp_path = Path(temp_path)
        target = self.path_for(md5)

        if target.exists():
            if target.stat().st_size != size:
                raise StorageIntegrityError(
                    "MD5 相同但文件大小不一致，拒绝复用已有存储对象"
                )
            self._discard(temp_path)
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(temp_path, target)  # atomic on the same filesystem
        except OSError as exc:
            raise StorageIntegrityError(f"写入存储对象失败: {exc}") from exc
        return True

    def discard_temp(self, temp_path: str | Path) -> None:
        self._discard(Path(temp_path))

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            logger.warning("Failed to remove temporary file %s", path, exc_info=True)

    def delete(self, md5: str) -> bool:
        """Remove a physical object (only called when ref_count == 0)."""
        path = self.path_for(md5)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete object %s", path, exc_info=True)
            return False

        # Best-effort pruning of empty shard directories.
        for directory in (path.parent, path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break
        return True

    def delete_path(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            path.unlink(missing_ok=True)
            for directory in (path.parent, path.parent.parent):
                try:
                    directory.rmdir()
                except OSError:
                    break
            return True
        except OSError:
            logger.warning("Failed to delete object file %s", path, exc_info=True)
            return False

    def read_bytes(self, md5: str, limit: int | None = None) -> bytes:
        path = self.path_for(md5)
        if not path.is_file():
            raise StorageIntegrityError("存储对象缺失，无法读取")
        with path.open("rb") as handle:
            return handle.read() if limit is None else handle.read(limit)

    def absolute_path(self, md5: str) -> Path:
        path = self.path_for(md5)
        if not path.is_file():
            raise StorageIntegrityError("存储对象缺失")
        return path
