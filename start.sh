#!/bin/sh
set -eu

echo "🤖 Starting Restricted Message Bot..."

# Local development may use .env; hosted platforms inject variables directly.
if [ -f .env ]; then
  echo "📝 Loading .env"
  set -a
  . ./.env
  set +a
fi

: "${TELEGRAM_API_ID:?TELEGRAM_API_ID is required}"
: "${TELEGRAM_API_HASH:?TELEGRAM_API_HASH is required}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"

# Dependencies are installed by the Docker image/host environment. Do not
# create a virtualenv or reinstall packages on every container restart.
echo "🚀 Starting TGFlow control room..."
exec python3 server.py
