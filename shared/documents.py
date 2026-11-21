"""Service-neutral document payload used across gRPC boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class IndexDocumentPayload:
    file_id: str
    title: str
    content: str = ""
    tags: str = ""
    owner_id: int = 0
    mime_type: str = ""
    updated_at: str = ""
    version: int = 1
    description: str = ""

    @property
    def semantic_text(self) -> str:
        parts = [self.title.strip()]
        if self.description:
            parts.append(self.description.strip())
        if self.content:
            parts.append(self.content)
        return "\n".join(part for part in parts if part)

    @classmethod
    def from_node(cls, node, content: str = "") -> "IndexDocumentPayload":
        updated = node.updated_at
        if isinstance(updated, datetime):
            updated = updated.isoformat(timespec="seconds") + "Z"
        return cls(
            file_id=str(node.node_id),
            title=node.name or "",
            content=content or "",
            tags="",
            owner_id=int(node.owner_id),
            mime_type=node.mime_type or "",
            updated_at=updated or "",
            version=int(node.version or 1),
            description=node.description or "",
        )
