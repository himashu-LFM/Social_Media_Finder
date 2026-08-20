#!/usr/bin/env bash
# One-time EC2 setup for the Curator AI API. Amazon Linux 2023.
#
#   curl -fsSL https://raw.githubusercontent.com/himashu-LFM/Social_Media_Finder/main/deploy/setup-ec2.sh | bash
# or, having cloned the repo already:
#   bash deploy/setup-ec2.sh
#
# Installs dependencies, creates the virtualenv, and installs the systemd unit
# and Caddy config. It deliberately does NOT start the service: it cannot,
# because /home/ec2-user/curator.env does not exist yet and the API refuses to
# boot in production without DATABASE_URL. Fill that in, then start it.
#
# Safe to re-run.

set -euo pipefail

REPO_URL="https://github.com/himashu-LFM/Social_Media_Finder.git"
APP_DIR="/home/ec2-user/Social_Media_Finder"
ENV_FILE="/home/ec2-user/curator.env"

echo "==> Installing system packages"
sudo dnf install -y python3.11 python3.11-pip git

echo "==> Installing Caddy (TLS termination)"
if ! command -v caddy >/dev/null 2>&1; then
    sudo dnf install -y 'dnf-command(copr)'
    sudo dnf copr enable -y @caddy/caddy
    sudo dnf install -y caddy
fi

echo "==> Fetching the application"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Python environment"
# 3.11 explicitly: the repo is tested there and apprunner.yaml pins the same.
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Config files"
sudo cp "$APP_DIR/deploy/curator-api.service" /etc/systemd/system/
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
if [ ! -f /etc/caddy/Caddyfile.orig ]; then
    sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.orig 2>/dev/null || true
fi
sudo cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable caddy

# The env file holds every secret. Created empty with owner-only permissions so
# a later `cat` by another user cannot read the database password.
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'TEMPLATE'
# Curator AI — production environment. chmod 600. NEVER commit this.
APP_ENV=production
AUTH_REQUIRED=1

# Required. The API refuses to start without these.
DATABASE_URL=postgresql://USER:PASSWORD@YOUR-RDS-ENDPOINT:5432/curator
CORS_ORIGINS=https://main.d15jtcsaaactv4.amplifyapp.com

# Pipeline keys.
SERPER_API_KEY=
ANTROPIC_API_KEY=
OPENAI_API_KEY=

# Optional.
# APIFY_API_TOKEN=
# APIFY_ACTOR_ID=
# GOOGLE_CLIENT_ID=
# GOOGLE_ALLOWED_DOMAINS=listenfirstmedia.com
TEMPLATE
    chmod 600 "$ENV_FILE"
    echo "==> Created $ENV_FILE (chmod 600) — fill it in before starting."
else
    echo "==> $ENV_FILE already exists, left untouched."
fi

cat <<'NEXT'

==> Setup complete. Remaining steps, in order:

  1. nano ~/curator.env                     # fill in DATABASE_URL and the keys
  2. sudo nano /etc/caddy/Caddyfile         # replace api.example.com with your domain
  3. Point that domain's DNS A record at this instance's Elastic IP, and WAIT
     for it to resolve — Caddy cannot get a certificate before it does.
  4. sudo systemctl restart caddy
  5. sudo systemctl enable --now curator-api
  6. sudo systemctl status curator-api      # confirm it is running
     journalctl -u curator-api -n 50        # FATAL CONFIG lines name any missing var

  Then verify from your laptop:
     curl -i https://YOUR-DOMAIN/api/health          # expect 200
     curl -i https://YOUR-DOMAIN/api/results/latest  # expect 401, NOT data

NEXT
