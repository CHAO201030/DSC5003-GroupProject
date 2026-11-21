"""Application-level errors shared by every CloudDisk service.

Every error carries a stable machine-readable ``code`` and an HTTP status so
that the Gateway can return one consistent error envelope:

    {"error": {"code": "...", "message": "...", "details": ...}}
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    default_message: str = "请求无法处理"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.default_message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return {"error": payload}


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "输入参数不合法"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "未认证或登录已过期"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "没有访问该资源的权限"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "资源不存在"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    default_message = "资源冲突"


class StorageIntegrityError(AppError):
    status_code = 500
    code = "STORAGE_INTEGRITY_ERROR"
    default_message = "存储内容校验失败"


class StorageUnavailableError(AppError):
    status_code = 503
    code = "STORAGE_UNAVAILABLE"
    default_message = "文件存储暂不可用"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    default_message = "文件超过允许的最大大小"


class ServiceUnavailableError(AppError):
    """Raised when an internal gRPC dependency (search/semantic) is down."""

    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "依赖服务暂不可用"


class SemanticUnavailableError(ServiceUnavailableError):
    code = "SEMANTIC_UNAVAILABLE"
    default_message = "语义搜索暂不可用，可继续使用关键词搜索"


class SearchUnavailableError(ServiceUnavailableError):
    code = "SEARCH_UNAVAILABLE"
    default_message = "关键词搜索服务暂不可用，文件管理不受影响"
