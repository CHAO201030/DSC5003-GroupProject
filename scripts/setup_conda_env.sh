#!/usr/bin/env bash
# Create (or verify) the DSC5003 conda environment from environment.yml.
#
# Usage:
#   bash scripts/setup_conda_env.sh              # install Miniforge if needed
#   CONDA_HOME=/opt/conda bash scripts/setup_conda_env.sh
#
# The script never installs into, or runs Python from, the conda `base`
# environment.  All checks use `conda run -n DSC5003`.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/environment.yml"
ENV_NAME="DSC5003"
CONDA_HOME="${CONDA_HOME:-$HOME/miniforge3}"
INSTALLER="/tmp/Miniforge3-Linux-x86_64.sh"
MIRROR_INSTALLER="https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease/Miniforge3-Linux-x86_64.sh"
GITHUB_INSTALLER="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"

find_conda() {
  if [ -x "$CONDA_HOME/bin/conda" ]; then
    echo "$CONDA_HOME/bin/conda"
    return 0
  fi
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return 0
  fi
  for candidate in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" \
                   "$HOME/mambaforge" /opt/conda /opt/miniconda3 /usr/local/miniconda3; do
    if [ -x "$candidate/bin/conda" ]; then
      echo "$candidate/bin/conda"
      return 0
    fi
  done
  return 1
}

download_installer() {
  echo "[setup] downloading Miniforge installer ..."
  if curl -fL --retry 3 --connect-timeout 20 \
        -o "$INSTALLER" "$MIRROR_INSTALLER"; then
    return 0
  fi
  echo "[setup] mirror failed, trying GitHub ..."
  curl -fL --retry 3 --connect-timeout 20 -o "$INSTALLER" "$GITHUB_INSTALLER"
}

if ! CONDA_BIN="$(find_conda)"; then
  download_installer
  echo "[setup] installing Miniforge into $CONDA_HOME (base is not used for the project)"
  bash "$INSTALLER" -b -p "$CONDA_HOME"
  CONDA_BIN="$CONDA_HOME/bin/conda"
fi

echo "[setup] using conda: $CONDA_BIN"

# Use a project-local conda configuration so the user's global config (and
# conda `base`) are left untouched.  The official channel is used by default;
# set CONDA_CHANNEL_ALIAS to a mirror when it is reachable from your network.
export CONDARC="${CONDARC:-$PROJECT_DIR/.condarc}"
if [ ! -f "$CONDARC" ]; then
  cat > "$CONDARC" <<YAML
channels:
  - conda-forge
channel_alias: ${CONDA_CHANNEL_ALIAS:-https://mirrors.ustc.edu.cn/anaconda/cloud}
show_channel_urls: true
YAML
fi

export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"

if "$CONDA_BIN" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "[setup] environment $ENV_NAME already exists"
else
  echo "[setup] creating environment $ENV_NAME from $ENV_FILE (this can take a while)"
  "$CONDA_BIN" env create -f "$ENV_FILE"
fi

echo "[setup] verifying $ENV_NAME (never base) ..."
"$CONDA_BIN" run --no-capture-output -n "$ENV_NAME" python - <<'PY'
import sys
print("python", sys.version.split()[0], "from", sys.executable)
import fastapi, sqlalchemy, pydantic, grpc, jieba, jwt, bcrypt, numpy, pytest
import pymysql, cryptography, alembic
import faiss, torch, transformers, sentence_transformers
print("fastapi", fastapi.__version__)
print("sqlalchemy", sqlalchemy.__version__)
print("pydantic", pydantic.__version__)
print("grpc", grpc.__version__)
print("faiss", faiss.__version__)
print("torch", torch.__version__)
print("sentence-transformers", sentence_transformers.__version__)
print("dependencies OK")
PY

CONDA_ROOT="$(cd "$(dirname "$CONDA_BIN")/.." && pwd)"
echo "[setup] done. Activate with:"
echo "        source \"$CONDA_ROOT/etc/profile.d/conda.sh\" && conda activate $ENV_NAME"
