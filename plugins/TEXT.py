# -*- coding: utf-8 -*-
"""统一的中文提示文案。"""

drive_folder_name = "GDriveUploaderBot"

MEGA_EMAIL = "bearyan8@yandex.com"
MEGA_PASSWORD = "bearyan8@yandex.com"

START = (
    "你好，{}！我是你的谷歌云盘上传助手 🤖。\n"
    "我可以自动接收文件、上传到 Google Drive，并提供进度和日志。\n\n"
    "发送 /help 查看常用指令和示例。"
)

HELP = (
    "📖 <b>使用指南</b>\n"
    "• /auth  获取授权链接，绑定 Google 账号\n"
    "• /revoke  取消本地绑定，下次需重新授权\n"
    "• 直接发送文件或转发消息，我会自动上传到云端\n"
    "• /status  查看当前任务进度\n"
    "• /cancel  终止进行中的上传\n"
    "• /logs  （管理员）查看近期日志\n"
    "• /adduser  （超级管理员）管理用户权限\n\n"
    "使用中遇到问题，可直接回复说明，我会尽快协助。"
)

DP_DOWNLOAD = "📥 已收到 Dropbox 链接，开始下载…"
OL_DOWNLOAD = "📥 已收到 Openload 链接，正在下载，可能耗时较长…"
PROCESSING = "🚀 正在处理你的请求，请稍候…"
DOWN_TWO = True
DOWNLOAD = "📥 正在下载文件，请耐心等待…"
DOWN_MEGA = "📥 正在下载 Mega 文件，可能会稍慢，请耐心等待…"
DOWN_COMPLETE = "✅ 下载完成"
NOT_AUTH = "❌ 你尚未授权，请先发送 /auth 完成授权。"
REVOKE_FAIL = "❌ 未找到可撤销的授权，或已被删除。"
AUTH_SUCC = "✅ 授权成功！现在可以发送链接或文件让我帮你上传到 Google Drive。"
ALREADY_AUTH = "ℹ️ 你已经完成授权，如需切换账号，请使用 /revoke。"
AUTH_URL = '<a href="{}">🔗 点击此处完成 Google Drive 授权</a>\n复制生成的验证码并发送给我。'
UPLOADING = "☁️ 下载完成，正在上传到 Google Drive……"
REVOKE_TOK = "🔒 授权已撤销，如需重新使用请发送 /auth 完成授权。"
DOWN_PATH = "Downloads/"
DOWNLOAD_URL = (
    "✅ 文件上传成功！\n\n"
    "<b>文件名</b>：{}\n"
    "<b>文件大小</b>：{} MB\n"
    "<b>下载链接</b>：{}"
)
AUTH_ERROR = "❌ 授权失败，请确认验证码是否正确，或重新执行 /auth。"
OPENLOAD = True
DROPBOX = True
MEGA = True

UPDATE = (
    "🆕 <b>近期更新</b>\n"
    "• 全量中文提示与进度条提示\n"
    "• 新增管理日志与权限控制的基础支持\n"
    "• 正在逐步完善监控与统计能力"
)
