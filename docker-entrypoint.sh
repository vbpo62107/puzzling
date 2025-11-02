#!/bin/sh
set -e

# Recreate client_secrets.json from base64 secret if provided
if [ -n "$GOOGLE_CLIENT_SECRETS_B64" ]; then
  echo "🧾 Generating client_secrets.json from GOOGLE_CLIENT_SECRETS_B64..."
  echo "$GOOGLE_CLIENT_SECRETS_B64" | base64 --decode > /app/client_secrets.json
  chmod 600 /app/client_secrets.json || true
elif [ ! -f /app/client_secrets.json ]; then
  echo "⚠️ Warning: client_secrets.json not found. Authentication may fail." >&2
fi

exec "$@"
