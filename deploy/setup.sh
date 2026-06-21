#!/usr/bin/env bash
# Mai VPS bootstrap. Idempotent. Run as root (or with sudo) on a fresh
# Debian/Ubuntu box:  sudo bash setup.sh
# It installs deps, creates the mai user + /opt/mai, clones the repo, builds a
# venv, generates a strong SESSION_SECRET into /opt/mai/.env (never committed),
# and installs the systemd unit + Caddy site. You still set the domain + DNS,
# upload mai.db (or harvest), and create accounts — see deploy/DEPLOY.md.
set -euo pipefail

REPO="${MAI_REPO:-https://github.com/r-log/Mai.git}"
APP=/opt/mai
DOMAIN="${MAI_DOMAIN:-mai.example.org}"

echo "==> packages (python, git, caddy)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y && apt-get install -y caddy
fi

echo "==> mai user + $APP"
id mai >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP" --shell /usr/sbin/nologin mai
mkdir -p "$APP"
chown mai:mai "$APP"

echo "==> clone/update repo"
if [ -d "$APP/.git" ]; then
  sudo -u mai git -C "$APP" pull --ff-only
else
  sudo -u mai git clone "$REPO" "$APP"
fi

echo "==> venv + install"
sudo -u mai python3 -m venv "$APP/.venv"
sudo -u mai "$APP/.venv/bin/pip" install --upgrade pip
sudo -u mai "$APP/.venv/bin/pip" install -e "$APP"

echo "==> .env (generate SESSION_SECRET if absent)"
if [ ! -f "$APP/.env" ]; then
  SECRET="$("$APP/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed "s|^SESSION_SECRET=.*|SESSION_SECRET=$SECRET|" "$APP/deploy/.env.production.example" > "$APP/.env"
  chown mai:mai "$APP/.env"
  chmod 600 "$APP/.env"
  echo "    wrote $APP/.env with a fresh secret (chmod 600)"
else
  echo "    $APP/.env already exists — left untouched"
fi

echo "==> initialise the database (no-op if it already exists with data)"
sudo -u mai bash -c "cd $APP && .venv/bin/python -m mai.cli init-db"

echo "==> systemd unit"
install -m 644 "$APP/deploy/mai.service" /etc/systemd/system/mai.service
systemctl daemon-reload
systemctl enable --now mai.service

echo "==> Caddy site (domain: $DOMAIN)"
sed "s/mai.example.org/$DOMAIN/" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

cat <<EOF

==> done. Service status:
$(systemctl --no-pager --full status mai.service | head -5)

NEXT (see deploy/DEPLOY.md):
  1. Point DNS A record for $DOMAIN at this box's public IP.
  2. (optional) Upload the populated mai.db so the board has data immediately:
       scp mai.db root@$DOMAIN:$APP/mai.db && chown mai:mai $APP/mai.db && systemctl restart mai
     OR harvest on the box (needs a registry of GitHub fork URLs + GITHUB_TOKEN).
  3. Create accounts:
       sudo -u mai bash -c "cd $APP && .venv/bin/python -m mai.cli user-add antz --maintainer"
  4. Browse https://$DOMAIN — log in, set password, use the board.
EOF
