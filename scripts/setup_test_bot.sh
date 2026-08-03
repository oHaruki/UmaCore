#!/usr/bin/env bash
#
# Set up a second, isolated UmaCore instance on the same VPS as production.
#
#   sudo git clone -b umamoe-live-data <repo-url> /opt/umacore-test
#   sudo bash /opt/umacore-test/scripts/setup_test_bot.sh <TEST_DISCORD_TOKEN>
#
# Run it from the checkout it is going to configure. Safe to re-run.
#
# Everything the two instances would otherwise fight over is separated:
#
#   production                     test
#   ----------------------------   ----------------------------
#   /opt/umacore                   /opt/umacore-test
#   umacore-bot (systemd)          umacore-test-bot (systemd)
#   port 7890 (internal API)       port 7891
#   database <name>                database <name>_test
#   its own .env                   its own .env, own API secret
#   backups/ under its own dir     backups/ under its own dir
#
# It never reads, writes or restarts anything under /opt/umacore, and never
# touches the production database.
#
# The uma.moe rate limiter is per-process, so two instances sharing one API key
# would together exceed the cap. The test instance is therefore configured with a
# small slice of the budget (see UMAMOE_RATE_PER_MIN below).
set -euo pipefail

PROD_DIR="${PROD_DIR:-/opt/umacore}"
TEST_DIR="${TEST_DIR:-/opt/umacore-test}"
SERVICE="umacore-test-bot"
TEST_PORT="${TEST_PORT:-7891}"
TEST_RATE="${TEST_RATE:-15}"          # uma.moe calls/min for the test instance
RUN_USER="${RUN_USER:-umacore}"

TOKEN="${1:-}"
BRANCH="${2:-umamoe-live-data}"

die() { echo "❌ $*" >&2; exit 1; }
say() { echo -e "\n\033[1m▶ $*\033[0m"; }

[[ -n "$TOKEN" ]] || die "Usage:
  sudo git clone -b umamoe-live-data <repo-url> /opt/umacore-test
  sudo bash /opt/umacore-test/scripts/setup_test_bot.sh <TEST_DISCORD_TOKEN>

Create a SECOND Discord application first — reusing the production token would
run the same bot twice and double every report."
[[ -d "$PROD_DIR" ]] || die "No production install at $PROD_DIR (override with PROD_DIR=...)"
[[ -f "$PROD_DIR/.env" ]] || die "No $PROD_DIR/.env to copy settings from"

if [[ -e "$TEST_DIR" && ! -d "$TEST_DIR/.git" ]]; then
    die "$TEST_DIR exists but is not a git checkout. Remove it, or set TEST_DIR=..."
fi

# --- work out the test database URL from the production one ------------------
PROD_URL="$(grep -E '^DATABASE_URL=' "$PROD_DIR/.env" | head -1 | cut -d= -f2-)"
[[ -n "$PROD_URL" ]] || die "Could not read DATABASE_URL from $PROD_DIR/.env"

read -r TEST_URL DB_HOST DB_NAME <<<"$(python3 - "$PROD_URL" <<'PY'
import sys
from urllib.parse import urlparse, urlunparse
u = urlparse(sys.argv[1])
name = (u.path or "/umacore").lstrip("/").split("?")[0]
test_name = f"{name}_test"
path = "/" + test_name
print(urlunparse((u.scheme, u.netloc, path, u.params, u.query, u.fragment)),
      u.hostname or "localhost", test_name)
PY
)"

say "Production : $PROD_DIR  (untouched)"
echo   "Test dir   : $TEST_DIR"
echo   "Service    : $SERVICE"
echo   "API port   : $TEST_PORT"
echo   "Database   : $DB_NAME on $DB_HOST"
echo   "Branch     : $BRANCH"
echo   "uma.moe    : $TEST_RATE calls/min (production keeps the rest)"

# --- 1. code ------------------------------------------------------------------
# Normally you clone straight into TEST_DIR and run this script from inside it,
# so there is nothing to do here. Cloning is only for the case where the script
# was obtained some other way.
if [[ -d "$TEST_DIR/.git" ]]; then
    say "Using the existing checkout at $TEST_DIR"
    echo "  branch: $(git -C "$TEST_DIR" rev-parse --abbrev-ref HEAD)"
    echo "  commit: $(git -C "$TEST_DIR" rev-parse --short HEAD)"
else
    say "Cloning the repo into $TEST_DIR"
    REMOTE="$(git -C "$PROD_DIR" remote get-url origin)"
    git clone --branch "$BRANCH" "$REMOTE" "$TEST_DIR"
fi

# --- 2. database --------------------------------------------------------------
say "Creating the test database"
case "$DB_HOST" in
  localhost|127.0.0.1|::1)
      OWNER="$(python3 -c "
from urllib.parse import urlparse,unquote
import sys; print(unquote(urlparse('$PROD_URL').username or 'umacore'))")"
      if sudo -u postgres psql -lqt | cut -d'|' -f1 | grep -qw "$DB_NAME"; then
          echo "  $DB_NAME already exists, leaving it alone"
      else
          sudo -u postgres psql -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$OWNER\";"
          echo "  created $DB_NAME"
      fi
      ;;
  *)
      echo "  ⚠️  $DB_HOST is a managed database — create \"$DB_NAME\" there yourself"
      echo "     (on Neon, a branch of the production database works well)"
      ;;
esac

# --- 3. env -------------------------------------------------------------------
say "Writing $TEST_DIR/.env"
if [[ -f "$TEST_DIR/.env" ]]; then
    cp "$TEST_DIR/.env" "$TEST_DIR/.env.bak.$(date +%s)"
    echo "  existing .env backed up"
fi
# Start from production so API keys and anything else carry over, then override
# every value that must differ.
grep -vE '^(DISCORD_TOKEN|DATABASE_URL|BOT_API_PORT|BOT_API_SECRET|UMAMOE_RATE_PER_MIN|LOG_LEVEL)=' \
    "$PROD_DIR/.env" > "$TEST_DIR/.env" || true
cat >> "$TEST_DIR/.env" <<EOF

# ---- test instance overrides ----
DISCORD_TOKEN=$TOKEN
DATABASE_URL=$TEST_URL
BOT_API_PORT=$TEST_PORT
BOT_API_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
UMAMOE_RATE_PER_MIN=$TEST_RATE
LOG_LEVEL=DEBUG
EOF
chown "$RUN_USER":"$RUN_USER" "$TEST_DIR/.env"
chmod 600 "$TEST_DIR/.env"

# --- 4. venv ------------------------------------------------------------------
say "Creating the virtualenv"
[[ -x "$TEST_DIR/venv/bin/python" ]] || python3 -m venv "$TEST_DIR/venv"
"$TEST_DIR/venv/bin/pip" install --quiet --upgrade pip
"$TEST_DIR/venv/bin/pip" install --quiet -r "$TEST_DIR/requirements.txt"

# --- 5. systemd ---------------------------------------------------------------
say "Installing the $SERVICE service"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=UmaCore Quota Tracker Bot (TEST)
After=network.target postgresql.service

[Service]
User=$RUN_USER
WorkingDirectory=$TEST_DIR
ExecStart=$TEST_DIR/venv/bin/python main.py
Restart=on-failure
RestartSec=10
EnvironmentFile=$TEST_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

chown -R "$RUN_USER":"$RUN_USER" "$TEST_DIR"
systemctl daemon-reload
systemctl enable --now "$SERVICE"

sleep 3
say "Done"
systemctl --no-pager --lines=15 status "$SERVICE" || true

cat <<EOF

────────────────────────────────────────────────────────────────
Test instance is up and completely separate from production.

  logs      journalctl -u $SERVICE -f
  restart   systemctl restart $SERVICE
  update    cd $TEST_DIR && git pull && systemctl restart $SERVICE
  remove    systemctl disable --now $SERVICE \\
            && rm -rf $TEST_DIR /etc/systemd/system/$SERVICE.service \\
            && sudo -u postgres psql -c 'DROP DATABASE "$DB_NAME";'

Still to do by hand:
  1. Invite the test bot to a server (its own, ideally)
  2. Add a club with /add_club — point it at TEST channels, not your real ones,
     or you'll get two reports for the same club
  3. /backup status to confirm pg_dump is available

Production was not modified. Its bot is still running on port 7890 with its own
database, and this script never touched $PROD_DIR.
────────────────────────────────────────────────────────────────
EOF
