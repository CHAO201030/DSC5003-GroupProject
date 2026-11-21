"""Conversions between the internal document payload and generated protobuf."""

from __future__ import annotations

from clouddisk_grpc import clouddisk_pb2
from shared.documents import IndexDocumentPayload


def document_to_proto(document: IndexDocumentPayload) -> clouddisk_pb2.Document:
    return clouddisk_pb2.Document(
        file_id=str(document.file_id),
        title=document.title or "",
        content=document.content or "",
        tags=document.tags or "",
        owner_id=int(document.owner_id or 0),
        mime_type=document.mime_type or "",
        updated_at=document.updated_at or "",
        version=int(document.version or 1),
        description=document.description or "",
    )


def document_from_proto(document: clouddisk_pb2.Document) -> IndexDocumentPayload:
    return IndexDocumentPayload(
        file_id=document.file_id,
        title=document.title,
        content=document.content,
        tags=document.tags,
        owner_id=int(document.owner_id),
        mime_type=document.mime_type,
        updated_at=document.updated_at,
        version=int(document.version),
        description=document.description,
    )
