#!/bin/bash
# ===============================================
# 🤖 Telegram + Google Drive Bot Deployment Script
# For Ubuntu 18.04 + Python 3.10 + systemd
# ===============================================

APP_DIR="/home/ubuntu/telegram-bot"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="telegram-bot"

echo "🚀 Starting deployment of Telegram Google Drive Bot..."

# -----------------------------------------------
# 1️⃣ Update system and dependencies
# -----------------------------------------------
echo "📦 Updating system..."
sudo apt update -y && sudo apt upgrade -y

# -----------------------------------------------
# 🔐 Optionally rebuild client_secrets.json from Base64
# -----------------------------------------------
if [ -n "$GOOGLE_CLIENT_SECRETS_B64" ]; then
    echo "🧾 Recreating client_secrets.json from GOOGLE_CLIENT_SECRETS_B64..."
    echo "$GOOGLE_CLIENT_SECRETS_B64" | base64 --decode > "$APP_DIR/client_secrets.json"
    chmod 600 "$APP_DIR/client_secrets.json"
elif [ ! -f "$APP_DIR/client_secrets.json" ]; then
    echo "⚠️ client_secrets.json not found. Provide GOOGLE_CLIENT_SECRETS_B64 or place the file manually."
fi

# -----------------------------------------------
# 2️⃣ Check and create virtual environment
# -----------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    echo "🐍 Creating virtual environment..."
    python3.10 -m venv "$VENV_DIR"
fi

echo "⚙️ Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# -----------------------------------------------
# 3️⃣ Install dependencies
# -----------------------------------------------
echo "📚 Installing Python dependencies..."
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"

# -----------------------------------------------
# 4️⃣ Ensure .env exists
# -----------------------------------------------
if [ ! -f "$APP_DIR/.env" ]; then
    echo "⚠️ .env file not found. Please create it and fill in your environment variables."
    echo "Example:"
    echo "TELEGRAM_BOT_TOKEN=your_Telegram_token"
    echo "GOOGLE_CLIENT_ID=your_Google_client_ID"
    echo "GOOGLE_CLIENT_SECRET=your_Google_client_secret"
    echo "GOOGLE_DRIVE_FOLDER_ID=your_folder_ID"
    exit 1
fi

# -----------------------------------------------
# 5️⃣ Restart systemd service
# -----------------------------------------------
echo "🔁 Reloading systemd..."
sudo systemctl daemon-reload

echo "🧹 Stopping any existing bot service..."
sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true

echo "▶️ Starting bot service..."
sudo systemctl start "$SERVICE_NAME"

# -----------------------------------------------
# 6️⃣ Check running status
# -----------------------------------------------
sleep 3
STATUS=$(sudo systemctl is-active "$SERVICE_NAME")
if [ "$STATUS" = "active" ]; then
    echo "✅ Bot started successfully!"
else
    echo "❌ Failed to start. View logs with:"
    echo "   sudo journalctl -u $SERVICE_NAME -f"
    exit 1
fi

# -----------------------------------------------
# 7️⃣ Display log command
# -----------------------------------------------
echo "📜 View live logs with:"
echo "   sudo journalctl -u $SERVICE_NAME -f"

echo "🎉 Deployment complete! The bot is now running in the background."
