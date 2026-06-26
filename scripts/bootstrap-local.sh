#!/usr/bin/env bash
# Local bootstrap — zero-infra setup. Uses uv to install Python 3.11 + deps,
# generates .env with a fresh Fernet key, and runs DB migrations.
#
# Usage:  ./scripts/bootstrap-local.sh
#
# After this completes, run ./scripts/dev-local.sh to start backend + frontend.

set -euo pipefail

cd "$(dirname "$0")/.."

UV="${HOME}/.local/bin/uv"
if ! command -v "$UV" >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# ── 1. Python + backend venv
echo "==> Installing Python 3.11 via uv"
"$UV" python install 3.11

pushd backend >/dev/null
echo "==> Creating .venv and installing backend deps"
"$UV" venv --python 3.11
"$UV" pip install -e ".[dev]"
popd >/dev/null

# ── 2. .env — only create if missing
if [[ ! -f .env ]]; then
  echo "==> Generating .env with a fresh Fernet key"
  FERNET_KEY=$("$UV" run --directory backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  cat > .env <<EOF
# Local mode — SQLite + local filesystem + inline task executor.
# To go back to Docker, copy from .env.example instead.

APP_ENV=development
LOG_LEVEL=INFO

# ── Required: your Anthropic API key
ANTHROPIC_API_KEY=

# ── Encryption key for user provider keys (auto-generated)
ENCRYPTION_KEY=$FERNET_KEY

# ── Zero-infra local defaults
DATABASE_URL=sqlite+aiosqlite:///./cmds.db
SYNC_DATABASE_URL=sqlite:///./cmds.db
TASK_EXECUTOR=inline
STORAGE_BACKEND=local
STORAGE_LOCAL_ROOT=./storage
S3_PUBLIC_ENDPOINT=http://localhost:8001
CORS_ORIGINS=http://localhost:5173,http://localhost:5174
EOF
  echo ""
  echo "  !!  Edit .env and paste your ANTHROPIC_API_KEY before proceeding"
  echo ""
else
  echo "==> .env already exists (keeping it)"
fi

# ── 3. Alembic migrations
echo "==> Running migrations against SQLite"
pushd backend >/dev/null
"$UV" run alembic upgrade head
popd >/dev/null

# ── 4. Frontend deps
if [[ ! -d frontend/node_modules ]]; then
  echo "==> Installing frontend deps"
  pushd frontend >/dev/null
  PATH=/usr/local/bin:$PATH /usr/local/bin/npm install
  popd >/dev/null
fi

echo ""
echo "==> Bootstrap complete."
echo "Next:  ./scripts/dev-local.sh"
