"""Shared gRPC channel/server options."""

from __future__ import annotations

import grpc

# Index documents can be a few hundred KB; keep a generous message limit.
GRPC_OPTIONS = [
    ("grpc.max_send_message_length", 64 * 1024 * 1024),
    ("grpc.max_receive_message_length", 64 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
]


def make_channel(host: str, port: int) -> grpc.Channel:
    return grpc.insecure_channel(f"{host}:{port}", options=GRPC_OPTIONS)
