# 谷歌云盘上传机器人（Telegram Bot）

> 🤖 自动接收链接并上传至 Google Drive 的中文部署手册  
> 本项目基于 Python 开发，适合部署在 Ubuntu 18.04 + Python 3.10 环境，并通过 systemd 长期运行。

---

## 目录
- [功能简介](#功能简介)
- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置环境变量](#配置环境变量)
- [首次授权流程](#首次授权流程)
- [systemd 持久化运行](#systemd-持久化运行)
- [一键部署脚本](#一键部署脚本)
- [常用命令与调试](#常用命令与调试)
- [注意事项](#注意事项)

---

## 功能简介
- 通过 Telegram 接收直链、Dropbox、Mega 等常见文件链接。
- 自动下载并上传到指定的 Google Drive 文件夹（支持团队盘）。
- 支持中文交互，内置 `/start`、`/auth`、`/help`、`/revoke`、`/ping` 等指令。
- 使用 `.env` 管理敏感配置，凭证文件持久化保存到 `token.json`。
- 提供一键部署脚本与 systemd 服务，便于后台常驻运行。

---

## 项目结构
```
.
├── bot.py                # Telegram 主程序
├── upload.py             # Google Drive 上传逻辑
├── creds.py              # 环境变量加载及校验
├── plugins/              # 机器人提示文案、解析插件
│   ├── TEXT.py
│   ├── dpbox.py
│   ├── tok_rec.py
│   └── wdl.py
├── mega/                 # Mega 链接下载库
├── requirements.txt      # Python 依赖列表
├── Procfile              # Heroku 等平台的进程声明
├── deploy.sh             # 一键部署脚本（systemd）
└── .env                  # 本地环境变量（需自行创建）
```

---

## 环境要求
- Ubuntu 18.04（其他发行版需按需调整命令）
- Python 3.10（建议通过 `deadsnakes` 仓库安装）
- Git、systemd、curl 等常用工具
- Telegram 机器人 Token、Google API 凭证

> 如果当前系统尚未安装 Python 3.10，可执行：
> ```bash
> sudo add-apt-repository ppa:deadsnakes/ppa -y
> sudo apt update
> sudo apt install python3.10 python3.10-venv python3.10-dev -y
> ```

---

## 快速开始
1. **获取项目代码**
   ```bash
   git clone <your-repo-url> /home/ubuntu/telegram-bot
   cd /home/ubuntu/telegram-bot
   ```

2. **创建虚拟环境**
   ```bash
   python3.10 -m venv venv
   source venv/bin/activate
   ```

3. **安装依赖**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **准备 Google API 凭证**
   - 前往 [Google Cloud Console](https://console.cloud.google.com/apis/credentials) 创建 OAuth 客户端并下载 JSON。
   - 将文件重命名为 `client_secrets.json`，放到项目根目录。

5. **创建 `.env` 并填写必需变量**（详见下文）。

6. **测试运行**
   ```bash
   source venv/bin/activate
   python3 bot.py
   ```
   终端应输出类似：
   ```
   2025-11-01 21:03:00 - INFO - 🤖 机器人启动中……
   2025-11-01 21:03:01 - INFO - ✅ 机器人已成功启动！
   🚀 机器人正在运行。按 Ctrl+C 可停止。
   📡 等待 Telegram 消息中……
   ```

---

## 配置环境变量
项目使用 [python-dotenv](https://pypi.org/project/python-dotenv/) 从 `.env` 读取配置，示例：
```
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Google API
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_DRIVE_FOLDER_ID=your_teamdrive_folder_id_here
GOOGLE_TOKEN_FILE=/home/ubuntu/telegram-bot/token.json

# Logging
LOG_LEVEL=INFO
```

> `.env` 含敏感信息，请勿提交到 Git 仓库，可在 `.gitignore` 中确保忽略。

---

## 首次授权流程
1. Telegram 输入 `/auth`，机器人会返回 Google 授权链接。
2. 浏览器完成授权，将生成的验证码发送给机器人。
3. 机器人提示 “授权成功” 后，凭证会保存在 `.env` 中指定的 `token.json` 路径。
4. 推荐限制凭证文件访问权限：
   ```bash
    chmod 600 /home/ubuntu/telegram-bot/token.json
   ```

---

## systemd 持久化运行
1. 创建服务文件 `/etc/systemd/system/telegram-bot.service`：
   ```
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

2. 重新加载并启用服务：
   ```bash
   sudo systemctl daemon-reload
  sudo systemctl enable telegram-bot
   sudo systemctl start telegram-bot
   sudo systemctl status telegram-bot
   ```

3. 实时查看日志：
   ```bash
   sudo journalctl -u telegram-bot -f
   ```

---

## 一键部署脚本
项目提供 `deploy.sh`，集成常用的升级/安装/重启流程：
```bash
chmod +x deploy.sh
./deploy.sh
```
脚本功能包括：
- 系统更新、创建虚拟环境。
- 安装 `requirements.txt`。
- 检查 `.env` 是否存在。
- 重新加载并重启 `systemd` 服务。
- 自动检测服务状态并给出日志查看命令。

> 需要 `sudo` 权限执行系统更新与服务操作。

---

## 常用命令与调试
- **Telegram 指令**：`/start`、`/auth`、`/help`、`/revoke`、`/ping`
- **查看服务状态**：`sudo systemctl status telegram-bot`
- **实时日志**：`sudo journalctl -u telegram-bot -f`
- **停止服务**：`sudo systemctl stop telegram-bot`
- **重启服务**：`sudo systemctl restart telegram-bot`
- **本地调试**：`source venv/bin/activate && python3 bot.py`

---

## 注意事项
- 本项目仍基于较旧的 Telegram 运行逻辑（`Updater` + `use_context=True`），若升级到 `python-telegram-bot 20.x`，请参考官方文档迁移到 `ApplicationBuilder` 接口。
- `.env` 与 `token.json` 均包含敏感信息，务必妥善保护。
- 若将服务部署在多个实例或容器中，请确保 `token.json` 存储在共享存储或数据库中，以便同步授权状态。
- 由于部分下载源（如 Mega）速度较慢，建议准备较大的磁盘和稳定的网络。

---

欢迎根据自身需求继续扩展功能，如：加入更多下载源、完善错误监控、对接 Webhook 等。祝部署顺利 🎉！
