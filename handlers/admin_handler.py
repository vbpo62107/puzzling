import html

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import get_today_stats, tail_logs
from permissions import list_users, remove_user, require_role, set_user_role

ROLES = {"user", "admin", "super_admin"}


@require_role("admin")
async def show_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_type = "system"
    if context.args:
        candidate = context.args[0].lower()
        if candidate in {"system", "activity", "stats"}:
            log_type = candidate
    logs_text = tail_logs(log_type, lines=40)
    message = "📜 最近日志（{}）:\n<pre>{}</pre>".format(log_type, html.escape(logs_text))
    if update.message:
        await update.message.reply_text(message, parse_mode="HTML")
    elif update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, message, parse_mode="HTML")


@require_role("super_admin")
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ 使用方式：/adduser <user_id> <role>")
        return
    user_id_text, role = context.args[0], context.args[1].lower()
    if not user_id_text.isdigit() or role not in ROLES:
        await update.message.reply_text("❌ 参数无效，请确认用户 ID 与角色（user/admin/super_admin）。")
        return
    target_id = int(user_id_text)
    set_user_role(target_id, role)
    await update.message.reply_text(f"✅ 用户 {target_id} 已设置为 {role}。")


@require_role("super_admin")
async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("❌ 使用方式：/removeuser <user_id>")
        return
    user_id_text = context.args[0]
    if not user_id_text.isdigit():
        await update.message.reply_text("❌ 用户 ID 必须为数字。")
        return
    target_id = int(user_id_text)
    if remove_user(target_id):
        await update.message.reply_text(f"✅ 已移除用户 {target_id}。")
    else:
        await update.message.reply_text("ℹ️ 未找到对应用户，或该用户为默认超级管理员。")


@require_role("admin")
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = list_users()
    if not users:
        await update.message.reply_text("ℹ️ 当前未配置额外用户。")
        return
    lines = ["👥 已配置用户列表："]
    for uid, data in users.items():
        role = data.get("role", "user")
        name = data.get("name") or "-"
        lines.append(f"• {uid} -> {role}（备注：{name}）")
    await update.message.reply_text("\n".join(lines))
