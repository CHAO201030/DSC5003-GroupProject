#!/usr/bin/env bash
# Regenerate Python gRPC bindings into clouddisk_grpc/.
# Run with the DSC5003 environment active (grpcio-tools is required).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

mkdir -p clouddisk_grpc
python -m grpc_tools.protoc \
  -I protos \
  --python_out=clouddisk_grpc \
  --grpc_python_out=clouddisk_grpc \
  protos/clouddisk.proto

# grpcio-tools emits an absolute import; make it package-relative.
sed -i 's/^import clouddisk_pb2 as clouddisk__pb2$/from . import clouddisk_pb2 as clouddisk__pb2/' \
  clouddisk_grpc/clouddisk_pb2_grpc.py

python - <<'PY'
from pathlib import Path
init = Path("clouddisk_grpc/__init__.py")
if not init.exists():
    init.write_text(
        '"""Generated Protocol Buffer / gRPC bindings for CloudDisk."""\n',
        encoding="utf-8",
    )
PY

echo "Generated clouddisk_grpc/clouddisk_pb2.py and clouddisk_grpc/clouddisk_pb2_grpc.py"
