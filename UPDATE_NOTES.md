# 项目功能更新说明

## 交互体验与提示统一
- 新增 `message_utils.py`，集中封装提示文案（信息、成功、错误、进度等），所有提示均采用中文与统一表情符号。
- 上传流程（直链与文件消息）现在会通过动态进度条展示“排队 → 下载 → 上传 → 完成”四个阶段，避免长任务造成卡顿错觉。
- `/start`、`/help`、下载/上传成功或失败等提示全部改写为规范中文文案，成功反馈会附带文件名、大小与下载链接。
- 文件消息上传（`handlers/file_handler.py`）会记录转发来源（可通过 `ENABLE_FORWARD_INFO` 开关控制），并使用统一提示格式。

## 日志与监控体系
- 新增 `monitoring.py`：
  - 使用 `TimedRotatingFileHandler` 生成 `system.log`、`activity.log`、`stats.log` 三类日志，按日轮换并可通过 `LOG_RETENTION_DAYS` 控制保留时长。
  - 提供 `log_activity`、`log_system_info`、`record_upload` 等接口，分别记录用户行为、系统状态、上传统计。
  - `/status` 在无上传任务时，若用户具备管理员权限，会展示当日上传次数与总上传量。
  - `/logs` 命令（管理员）可查看最近的系统 / 行为 / 统计日志片段。

## 权限管理机制
- 新增 `permissions.py`，支持三种角色：
  - 👤 普通用户（user）：可上传文件。
  - 🛠️ 管理员（admin）：可执行 `/logs`、查看统计等运维指令。
  - 👑 超级管理员（super_admin）：可新增/移除用户与变更角色。
- 默认超级管理员可通过环境变量 `SUPER_ADMIN_IDS` 配置（逗号分隔的 Telegram user_id）。
- 用户角色持久化于 `data/users.json`，并提供 `/adduser <id> <role>`、`/removeuser <id>`、`/users` 命令进行管理。
- 所有敏感命令使用 `@require_role` 装饰器进行权限校验，权限不足时返回统一中文提示。

## 主要命令变更一览
- `/help`：展示全中文使用指南。
- `/status`：显示当前任务进度，若无任务且用户为管理员，则返回当日统计。
- `/cancel`：终止当前上传任务。
- `/logs [system|activity|stats]`：管理员查看最近日志。
- `/adduser`、`/removeuser`、`/users`：超级管理员或管理员维护用户角色。
- `/auth`、`/revoke`、文件/链接上传等行为均使用统一提示与进度反馈。

## 环境变量与配置
- `ENABLE_FORWARD_INFO`（默认 `true`）：控制是否在反馈中展示消息转发来源。
- `CACHE_DIR`：文件缓存目录，默认 `Downloads`。
- `LOG_DIRECTORY`：日志输出目录，默认 `logs`。
- `LOG_RETENTION_DAYS`：日志保留天数，默认 7。
- `SUPER_ADMIN_IDS`：初始化超级管理员列表（必填，逗号分隔的 user_id）。
- `USER_STORE_PATH`：角色配置文件路径，默认 `data/users.json`。

## 受影响的核心文件
- 新增：`message_utils.py`、`monitoring.py`、`permissions.py`、`handlers/admin_handler.py`、`UPDATE_NOTES.md`。
- 主要修改：`bot.py`、`handlers/file_handler.py`、`handlers/upload_handler.py`、`handlers/status_handler.py`、`plugins/TEXT.py`、`creds.py` 等，适配统一提示、日志与权限模块。

> 运行前请确保：已设置超级管理员 ID、完成 Google Drive 授权所需环境变量，并根据需要调整 `.env` 与 `data/users.json`。***
