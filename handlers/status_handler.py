from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.upload_handler import get_user_status, request_cancel
from monitoring import get_today_stats
from permissions import require_role
from plugins import TEXT


def _render_progress_bar(progress: int, width: int = 12) -> str:
    progress = max(0, min(100, int(progress)))
    filled = int(round(width * progress / 100))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _format_elapsed(updated_at: object) -> str:
    if not isinstance(updated_at, datetime):
        return ""
    delta = datetime.utcnow() - updated_at
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} 秒前"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分钟前"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时前"


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=TEXT.HELP,
        parse_mode=ParseMode.HTML,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.from_user is None:
        return
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=TEXT.START.format(update.message.from_user.first_name),
        parse_mode=ParseMode.HTML,
    )


async def updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=TEXT.UPDATE,
            parse_mode=ParseMode.HTML,
        )


@require_role("admin")
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else chat_id
    task = get_user_status(user_id)

    sections = []

    if task is not None:
        filename = task.get("filename") or "未命名文件"
        stage = task.get("stage") or "处理中"
        progress = int(task.get("progress", 0))
        progress_bar = _render_progress_bar(progress)
        updated_text = _format_elapsed(task.get("updated_at"))

        lines = [
            "📊 当前上传任务状态：",
            f"• 文件：{filename}",
            f"• 状态：{stage}",
            f"• 进度：{progress_bar} {progress}%",
        ]
        if updated_text:
            lines.append(f"• 最近更新：{updated_text}")
        sections.append("\n".join(lines))

    stats = get_today_stats()
    sections.append(
        "📊 今日运行统计：\n"
        f"• 日期：{stats['date']}\n"
        f"• 上传次数：{stats['upload_count']}\n"
        f"• 总上传量：{stats['total_size_mb']} MB"
    )

    message = "\n\n".join(sections)

    if update.message:
        await update.message.reply_text(message)
    elif chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=message)


async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else chat_id
    task = get_user_status(user_id)

    if task is None:
        message = "ℹ️ 当前没有进行中的上传任务。"
    else:
        filename = task.get("filename") or "未命名文件"
        stage = task.get("stage") or "处理中"
        progress = int(task.get("progress", 0))
        progress_bar = _render_progress_bar(progress)
        updated_text = _format_elapsed(task.get("updated_at"))

        lines = [
            "📊 当前上传任务状态：",
            f"• 文件：{filename}",
            f"• 状态：{stage}",
            f"• 进度：{progress_bar} {progress}%",
        ]
        if updated_text:
            lines.append(f"• 最近更新：{updated_text}")
        message = "\n".join(lines)

    if update.message:
        await update.message.reply_text(message)
    elif chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=message)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else chat_id

    if user_id is None:
        return

    if request_cancel(user_id):
        message = "🛑 上传任务已终止。"
    else:
        message = "ℹ️ 当前没有进行中的上传任务。"

    if update.message:
        await update.message.reply_text(message)
    elif chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=message)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🏓 机器人在线！",
        )
