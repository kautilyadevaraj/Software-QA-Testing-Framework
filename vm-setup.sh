#!/usr/bin/env bash
# ============================================================
# SQAT — Azure Ubuntu VM Setup Script
# Run this script once on the VM after git clone.
# Usage:  bash vm-setup.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"
VENV_DIR="$SERVER_DIR/.venv"

log() { echo -e "\n\033[1;34m>>> $*\033[0m"; }
ok()  { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn(){ echo -e "\033[1;33m[WARN]\033[0m $*"; }

# ── 1. System dependencies ───────────────────────────────────────────────────
log "Installing system dependencies..."

sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg lsb-release \
    python3.12 python3.12-venv python3.12-dev \
    postgresql postgresql-contrib

# Node 20 (for Playwright CLI inside server/)
if ! command -v node &>/dev/null || [[ "$(node --version | cut -d. -f1 | tr -d v)" -lt 20 ]]; then
    log "Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
ok "Node $(node --version) ready"

# Docker (for RabbitMQ)
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    sudo apt-get install -y docker.io docker-compose-v2
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    warn "You have been added to the 'docker' group. Log out and back in (or run: newgrp docker) before the next step."
else
    ok "Docker $(docker --version) already installed"
fi

# uv (Python package/venv manager)
if ! command -v uv &>/dev/null; then
    log "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1090
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version) ready"

# ── 2. PostgreSQL database ───────────────────────────────────────────────────
log "Configuring PostgreSQL..."

sudo systemctl enable --now postgresql

# Read DB credentials from .env if it exists, otherwise use defaults
if [[ -f "$SERVER_DIR/.env" ]]; then
    DB_URL=$(grep -E '^DATABASE_URL=' "$SERVER_DIR/.env" | cut -d= -f2-)
    DB_PASS=$(echo "$DB_URL" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
    DB_USER=$(echo "$DB_URL" | sed -E 's|.*://([^:]+):.*|\1|')
    DB_NAME=$(echo "$DB_URL" | sed -E 's|.*/([^?]+).*|\1|')
else
    DB_USER="sqat"
    DB_PASS="sqat_password_change_me"
    DB_NAME="sqat_db"
    warn ".env not found — using default DB credentials: user=$DB_USER db=$DB_NAME"
    warn "Create server/.env from server/.env.vm.example before running migrations!"
fi

# Create user and DB idempotently
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
ok "PostgreSQL database '$DB_NAME' and user '$DB_USER' ready"

# ── 3. RabbitMQ via Docker ───────────────────────────────────────────────────
log "Starting RabbitMQ container..."

if docker ps -a --format '{{.Names}}' | grep -q '^sqat_rabbitmq$'; then
    docker start sqat_rabbitmq 2>/dev/null && ok "RabbitMQ container restarted" || true
else
    docker run -d \
        --name sqat_rabbitmq \
        --restart unless-stopped \
        -p 127.0.0.1:5672:5672 \
        -p 127.0.0.1:15672:15672 \
        rabbitmq:3-management
    ok "RabbitMQ container created (bound to 127.0.0.1 — not publicly accessible)"
fi

# ── 4. Python virtualenv + dependencies ──────────────────────────────────────
log "Creating Python virtualenv and installing dependencies..."

cd "$SERVER_DIR"
uv venv --python python3.12 "$VENV_DIR"
uv pip install -r requirements.txt --python "$VENV_DIR/bin/python"
ok "Python dependencies installed"

# ── 5. Playwright + Chromium ─────────────────────────────────────────────────
log "Installing Playwright and Chromium..."

# Install Node deps for Playwright CLI
[[ -f package-lock.json ]] && npm ci --omit=dev || npm install --omit=dev
"$VENV_DIR/bin/python" -m playwright install chromium --with-deps
ok "Playwright + Chromium installed"

# ── 6. .env check ────────────────────────────────────────────────────────────
log "Checking .env..."

if [[ ! -f "$SERVER_DIR/.env" ]]; then
    warn "No server/.env found!"
    warn "Copy server/.env.vm.example to server/.env and fill in:"
    warn "  - PUBLIC_API_URL=http://<VM_PUBLIC_IP>:8000"
    warn "  - DATABASE_URL (should match DB credentials above)"
    warn "  - JWT_SECRET_KEY (generate below)"
    warn "  - CREDENTIAL_ENCRYPTION_KEY (generate below)"
    warn "  - ANTHROPIC_API_KEY"
    warn "  - QDRANT_URL + QDRANT_API_KEY"
    echo ""
    echo "  Generate JWT_SECRET_KEY:"
    echo "    python3.12 -c \"import secrets; print(secrets.token_hex(32))\""
    echo ""
    echo "  Generate CREDENTIAL_ENCRYPTION_KEY:"
    echo "    python3.12 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    echo ""
    echo "  Then re-run this script, or manually run:"
    echo "    cd server && source .venv/bin/activate && alembic upgrade head"
    exit 1
fi

# ── 7. DB migrations ─────────────────────────────────────────────────────────
log "Running Alembic migrations..."

cd "$SERVER_DIR"
"$VENV_DIR/bin/alembic" upgrade head
ok "Database migrations applied"

# ── 8. systemd service ───────────────────────────────────────────────────────
log "Installing systemd service..."

SERVICE_SRC="$SERVER_DIR/sqat.service"
SERVICE_DST="/etc/systemd/system/sqat.service"

# Substitute the real username into the service file
CURRENT_USER="$(whoami)"
CURRENT_HOME="$(eval echo ~"$CURRENT_USER")"

sed \
    -e "s|__USER__|$CURRENT_USER|g" \
    -e "s|__WORKDIR__|$SERVER_DIR|g" \
    -e "s|__VENV__|$VENV_DIR|g" \
    -e "s|__ENVFILE__|$SERVER_DIR/.env|g" \
    "$SERVICE_SRC.template" > /tmp/sqat.service.rendered

sudo cp /tmp/sqat.service.rendered "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl enable sqat
sudo systemctl restart sqat
sleep 2
sudo systemctl status sqat --no-pager

ok "SQAT systemd service installed and started"

# ── 9. Quick health check ────────────────────────────────────────────────────
log "Health check..."
sleep 3
if curl -sf http://localhost:8000/health | grep -q '"ok"'; then
    ok "Backend is healthy at http://localhost:8000"
else
    warn "Health check failed — check logs with: sudo journalctl -u sqat -f"
fi

echo ""
echo "============================================================"
echo " SQAT VM setup complete!"
echo "============================================================"
echo ""
echo " Internal:  curl http://localhost:8000/health"
echo " Swagger:   curl http://localhost:8000/docs"
echo " Logs:      sudo journalctl -u sqat -f"
echo " Restart:   sudo systemctl restart sqat"
echo "============================================================"
