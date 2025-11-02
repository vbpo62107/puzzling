# Telegram Google Drive Uploader Bot / 谷歌云盘上传机器人

> A bilingual deployment guide for the fully asynchronous Telegram → Google Drive uploader bot.  
> 一份适用于 Telegram → Google 云端硬盘异步上传机器人的中英双语部署手册。

---

## Overview / 项目概览

| EN | 中文 |
| --- | --- |
| A Telegram bot that accepts direct download links (HTTP, Dropbox, Mega, etc.), downloads the files, and uploads them to Google Drive or a Team Drive folder. Built with Python 3.10+, `python-telegram-bot` v20, and PyDrive2. | 该机器人可接收直链（HTTP、Dropbox、Mega 等），自动下载后上传到 Google Drive 或团队盘。项目基于 Python 3.10+、`python-telegram-bot` v20 与 PyDrive2。 |

- Fully asynchronous handlers powered by `ApplicationBuilder` (PTB v20).
- Credentials loaded from `.env` via `python-dotenv`; Google token persisted to disk.
- Ships with a one-click deployment script and systemd unit template for Ubuntu 18.04 LTS.
- 中文提示信息与日志，便于日常运维。

---

## Repository Layout / 项目结构

```
.
├── bot.py                # Telegram 主程序 / Main bot entry point (async handlers)
├── upload.py             # Google Drive 上传逻辑 / Drive upload helpers
├── creds.py              # 环境变量加载 / Environment loader
├── plugins/
│   ├── TEXT.py           # 中文提示文本 / Chinese prompts
│   ├── dpbox.py          # Dropbox 链接转换 / Dropbox helper
│   ├── tok_rec.py        # 授权 token 校验 / Token recognizer
│   └── wdl.py            # wget/requests 下载封装 / Generic downloader
├── mega/                 # Mega.nz SDK (vendor)
├── requirements.txt      # Python 依赖 / Dependencies
├── deploy.sh             # 一键部署脚本 / Deployment script
├── Procfile              # Heroku 兼容声明 / Procfile (optional)
└── README.md             # 当前文档 / This manual
```

---

## Requirements / 环境要求

| EN | 中文 |
| --- | --- |
| Ubuntu 18.04 LTS (or compatible) | Ubuntu 18.04 LTS（或兼容系统） |
| Python 3.10 (installed via `deadsnakes` PPA recommended) | Python 3.10（推荐通过 `deadsnakes` PPA 安装） |
| Telegram Bot token from BotFather | 使用 BotFather 创建的 Telegram Bot Token |
| Google Cloud **OAuth Client** credentials (`client_secrets.json`) | Google Cloud **OAuth Client** 凭证（保存为 `client_secrets.json`） |
| Adequate disk space & network bandwidth for downloads/uploads | 保证足够磁盘空间与网络带宽 |

Install Python 3.10 on Ubuntu 18.04 / 在 Ubuntu 18.04 安装 Python 3.10：
```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install python3.10 python3.10-venv python3.10-dev -y
```

---

## Quick Start (Local) / 本地快速开始

```bash
# 1. Clone repository / 克隆仓库
git clone <your-repo-url> /home/ubuntu/telegram-bot
cd /home/ubuntu/telegram-bot

# 2. Create virtualenv / 创建虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 3. Install dependencies / 安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 4. Place Google OAuth JSON / 放置 Google OAuth JSON 文件
mv <downloaded_client_json>.json client_secrets.json

# 5. Create .env / 创建 .env
cp .env.example .env   # 如果已有模板；否则参考下文手动创建

# 6. Run locally / 本地运行测试
python3 bot.py
```

Expected console output / 预期控制台输出：
```
2025-11-01 21:03:00 - INFO - 🤖 机器人启动中……
2025-11-01 21:03:01 - INFO - ✅ 机器人已成功启动！
🚀 机器人正在运行。按 Ctrl+C 可停止。
📡 等待 Telegram 消息中……
```
Stop with `Ctrl+C`. 通过 `Ctrl+C` 停止服务。

---

## Environment Variables / 环境变量

Create `.env` (excluded from Git) with the following keys / `.env`（已加入 `.gitignore`）示例：

```
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Google API
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_DRIVE_FOLDER_ID=your_teamdrive_folder_id_here   # 可为空表示上传到个人盘根目录
GOOGLE_TOKEN_FILE=~/.config/google-drive-uploader/token.json  # 凭证默认保存位置，可覆盖
# Optional: embed client_secrets.json as Base64
# GOOGLE_CLIENT_SECRETS_B64=base64_of_client_secrets_json

# Logging
LOG_LEVEL=INFO
```

- **Do not commit** `.env`; use `.env.example` to share templates.  
  `.env` 含敏感信息，严禁提交；可保留 `.env.example` 作为模板。
- Set permissions / 设置权限：`chmod 600 .env token.json`
- Optional: set `GOOGLE_CLIENT_SECRETS_B64` with the Base64-encoded `client_secrets.json` so deployment scripts & Docker automatically recreate the file.  
  可选：设置 `GOOGLE_CLIENT_SECRETS_B64`，部署脚本与 Docker 会自动生成 `client_secrets.json`。

---

## First-Time Authorization / 首次授权流程

| EN | 中文 |
| --- | --- |
| 1. In Telegram chat, send `/auth`. | 1. 在 Telegram 中发送 `/auth`。 |
| 2. Bot returns a Google OAuth link. | 2. 机器人返回 Google OAuth 授权链接。 |
| 3. Complete browser flow, copy the verification code. | 3. 在浏览器完成授权并复制验证码。 |
| 4. Send the code back to the bot; it saves `token.json`. | 4. 将验证码发送给机器人，完成授权并写入 `token.json`。 |
| 5. Protect the credential file (`chmod 600`). | 5. 使用 `chmod 600 token.json` 限制访问。 |

Revoke with `/revoke`; reauthorize as needed. 使用 `/revoke` 可撤销授权并重新绑定。

---

## Production Deployment with systemd / 使用 systemd 持久化部署

Create `/etc/systemd/system/telegram-bot.service`：
```ini
[Unit]
Description=Telegram GoogleDrive Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/telegram-bot
ExecStart=/home/ubuntu/telegram-bot/venv/bin/python3 /home/ubuntu/telegram-bot/bot.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/telegram-bot/.env

[Install]
WantedBy=multi-user.target
```

Reload & enable service / 重新加载并启用服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
sudo systemctl status telegram-bot
```

Check logs / 查看日志：`sudo journalctl -u telegram-bot -f`

---

## One-Click Deployment Script / 一键部署脚本

The repository includes `deploy.sh` for automated updates, dependency installation, and service restart.  
仓库内提供 `deploy.sh` 以自动更新系统、安装依赖并重启 service。

```bash
chmod +x deploy.sh
./deploy.sh
```

Script steps / 脚本主要流程：
1. `apt update && apt upgrade`
2. Create virtualenv if missing (`python3.10 -m venv`)
3. Activate venv & install `requirements.txt`
4. Rebuild `client_secrets.json` automatically when `GOOGLE_CLIENT_SECRETS_B64` is provided (otherwise warn)
5. Ensure `.env` exists
6. Reload systemd, stop old service, start new instance
7. Report status & log command

Requires sudo privileges. 运行脚本需要 `sudo` 权限。

---

## Docker Deployment / Docker 化部署

| EN | 中文 |
| --- | --- |
| Build once with the provided Dockerfile. Mount a volume and set `GOOGLE_TOKEN_FILE=/data/token.json` so Google Drive credentials survive container restarts. | 借助仓库自带的 Dockerfile 可一次构建、随处运行。挂载数据卷并设置 `GOOGLE_TOKEN_FILE=/data/token.json`，即可在容器重启后保留 Google 授权凭证。 |

### Build & Run Locally / 本地构建与运行
```bash
# Build image / 构建镜像
docker build -t telegram-drive-bot .

# Run container with persistent credential volume
docker run -d \
  --name telegram-drive-bot \
  --env-file .env \
  -e GOOGLE_TOKEN_FILE=/data/token.json \
  -v $(pwd)/data:/data \
  telegram-drive-bot
```
- `.env` provides runtime secrets (same format as above).
  `.env` 用于提供运行时密钥。
- `-e GOOGLE_TOKEN_FILE=/data/token.json` directs the bot to persist tokens inside the mounted volume.
  通过设置 `GOOGLE_TOKEN_FILE=/data/token.json`，可将凭证保存到挂载的数据卷。
- `-v $(pwd)/data:/data` stores `token.json` and other persistent data locally.
  将 `$(pwd)/data` 挂载到 `/data`，即可在宿主机持久化 `token.json` 等文件。
- If `GOOGLE_CLIENT_SECRETS_B64` is supplied, the container entrypoint recreates `client_secrets.json` automatically.
  若提供 `GOOGLE_CLIENT_SECRETS_B64`，容器入口脚本会自动生成 `client_secrets.json`。

### Deploy on Render / 部署到 Render
1. Log in at [render.com](https://render.com) → **New +** → **Web Service**。
   登录 Render，依次选择 “New +” → “Web Service”。
2. Connect your GitHub repository containing this project。
   关联包含本项目的 GitHub 仓库。
3. Configure:
   - **Environment**: Docker
   - **Build / Start Command**: leave blank (Dockerfile handles it)
   - **Persistent Disk**: Name `botdata`, Mount Path `/data`, Size ≥ 1 GB
4. Add environment variables in the dashboard:
   `TELEGRAM_BOT_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_TOKEN_FILE=/data/token.json`
5. Deploy; Render builds the image from `Dockerfile` and starts the bot. Pushing to `main` triggers automatic rebuilds.
   部署后 Render 会根据 Dockerfile 构建镜像并启动机器人，后续推送到 `main` 会自动触发重建。

### Deploy on Railway / 部署到 Railway
1. Sign in at [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**。
   登录 Railway，选择 “New Project” → “Deploy from GitHub repo”。
2. Ensure “Dockerfile” is detected; no custom build command is required。
   确认平台识别到 Dockerfile，无需额外构建命令。
3. Add environment variables as above (include `GOOGLE_TOKEN_FILE=/data/token.json` and, optionally, `GOOGLE_CLIENT_SECRETS_B64`)。
   添加与上方相同的环境变量（包含 `GOOGLE_TOKEN_FILE=/data/token.json`，可选添加 `GOOGLE_CLIENT_SECRETS_B64`）。
4. Mount a persistent volume named `data` to `/data` for credential storage。
   新建名为 `data` 的持久化卷，并挂载到 `/data`。
5. Deploy; logs will show the familiar startup messages。
   部署后查看日志，可见机器人启动的提示信息。

### Verification / 验证
- Dashboard logs should display `🤖 机器人启动中…` 和 `📡 等待 Telegram 消息中…`。
- Run `/auth` in Telegram; `token.json` will appear inside `/data`, confirming persistence.
  在 Telegram 中执行 `/auth` 后，可在 `/data` 目录看到 `token.json`，验证授权已持久化。

---
---

## Security Best Practices / 安全实践

- `.env`, `token.json`, `__pycache__/` are ignored by `.gitignore` to avoid accidental commits.
- Restrict permissions on secret files: `chmod 600 .env token.json`.
- Consider rotating tokens regularly; revoke compromised credentials immediately.
- Keep Base64 secrets such as `GOOGLE_CLIENT_SECRETS_B64` in managed secret stores (Render/Railway env vars, Vault, etc.), never committed to Git.
- Optional secret scanners (选用)：`git-secrets`, `trufflehog`, `gitleaks` 等。
- For multi-instance deployments, back up `token.json` in encrypted storage or a shared secret manager.

---

## Telegram Commands / Telegram 指令列表

| Command | Description (EN) | 中文说明 |
| --- | --- | --- |
| `/start` | Welcome message & quick guide | 欢迎提示与快捷说明 |
| `/help` | Display command overview | 查看完整帮助信息 |
| `/auth` | Generate Google auth link | 生成 Google 授权链接 |
| `/revoke` | Delete local `token.json` | 撤销授权并删除本地凭证 |
| `/ping` | Health check | 心跳检测 |
| (send any download link) | Trigger upload pipeline | 发送链接触发下载上传流程 |

---

## Service Operations & Troubleshooting / 运维与故障排查

| Command | 中文说明 |
| --- | --- |
| `sudo systemctl status telegram-bot` | 查看服务状态 |
| `sudo systemctl restart telegram-bot` | 重启服务 |
| `sudo journalctl -u telegram-bot -f` | 持续查看日志 |
| `source venv/bin/activate && python3 bot.py` | 在前台调试运行 |
| `python -m compileall bot.py upload.py creds.py plugins mega` | 快速语法检查 |

- Ensure network connectivity for both Telegram and Google APIs.  
  确保服务器能访问 Telegram 与 Google API。
- Mega downloads may be slow; allow sufficient time.  
  Mega 下载较慢，请耐心等待。
- If upload fails, inspect logs for codes `UPX11`, `UXP12/13` etc.  
  上传失败时，注意日志中的错误代码便于定位问题。

---

## Roadmap & Notes / 后续计划与提示

- Migrate any remaining legacy plugins to async/I/O friendly implementations as needed.  
  视情况继续异步化剩余插件。
- Extend link support (zippyshare, mediafire, etc.) by adapting existing plugin structure.  
  可扩展更多下载源，只需复用插件框架。
- Consider containerization (Docker) or CI/CD pipelines for larger deployments.  
  大规模部署可考虑 Docker 或 CI/CD。

---

## License & Credits / 许可与鸣谢

- Original inspiration: [CyberBoySumanjay / driveuploadbot](https://github.com/cyberboysumanjay/driveuploadbot).  
  项目灵感来源：上述开源项目。
- Unless otherwise stated, this repository follows GPLv3 (inherit from upstream).  
  若无特殊说明，遵循原项目 GPLv3 许可。

---

Happy uploading! 如果有改进建议或问题，欢迎提交 issue 或 PR。👏
