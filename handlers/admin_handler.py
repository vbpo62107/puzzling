import html
import logging
from datetime import datetime, timezone
from typing import Optional, Set

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import (
    build_log_query_parser,
    parse_field_filters,
    query_structured_logs,
    summarize_logs,
    tail_logs,
)
from permissions import (
    DEFAULT_SUPER_ADMINS,
    get_super_admin_whitelist,
    list_users,
    reload_admin_whitelist,
    remove_user,
    require_role,
    set_user_role,
)
from puzzling.token_cleanup import TokenIssue, run_cleanup

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


@require_role("admin")
async def search_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parser = build_log_query_parser(prog="/search_logs", add_help=False)
    try:
        args = parser.parse_args(context.args)
    except SystemExit:
        usage = (
            "❌ 参数错误。示例：/search_logs --uid 123456 --cmd /start --since 2024-01-01 "
            "--field status=ok --limit 20"
        )
        if update.message:
            await update.message.reply_text(usage)
        elif update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, usage)
        return

    extra_filters = parse_field_filters(args.field)
    query_limit: Optional[int]
    if args.summary:
        query_limit = None
    else:
        query_limit = (args.limit + 1) if args.limit is not None else None

    results = query_structured_logs(
        log_type=args.log,
        user_id=args.uid,
        command=args.cmd,
        since=args.since,
        until=args.until,
        extra_filters=extra_filters,
        limit=query_limit,
    )

    if args.summary:
        stats = summarize_logs(results)
        count = stats["count"]
        time_range = stats["time_range"]
        start_ts = time_range[0] or "-"
        end_ts = time_range[1] or "-"
        top_users = stats["top_users"]
        top_commands = stats["top_commands"]

        lines = ["📊 日志统计（当前筛选）", f"• 匹配条目：{count}"]
        lines.append(f"• 时间范围：{start_ts} ~ {end_ts}")
        if top_users:
            users_text = ", ".join(f"{uid}×{cnt}" for uid, cnt in top_users)
            lines.append(f"• 用户 TOP3：{users_text}")
        if top_commands:
            cmd_text = ", ".join(f"{cmd}×{cnt}" for cmd, cnt in top_commands)
            lines.append(f"• 指令 TOP5：{cmd_text}")
        if not results:
            lines.append("• 没有符合条件的日志记录。")
        message = "\n".join(lines)
    else:
        if not results:
            message = "🔍 未找到符合条件的日志记录。"
        else:
            import json

            limit = args.limit if args.limit is not None else len(results)
            display = results[:limit]
            has_more = len(results) > len(display)
            lines = [
                f"🔍 已检索到 {len(display)} 条日志记录。",
            ]
            for entry in display:
                ts = entry.get("timestamp") or entry.get("time") or "-"
                uid = entry.get("user_id", "-")
                cmd = entry.get("command") or entry.get("action") or "-"
                summary = json.dumps(entry, ensure_ascii=False)
                lines.append(f"• {ts} | uid={uid} | cmd={cmd}\n  {summary}")
            if has_more:
                lines.append("… 结果已截断，使用 --limit 调整显示数量。")
            message = "\n".join(lines)

    await _send_large_text(update, context, message)


async def _send_large_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message: str, *, limit: int = 3800
) -> None:
    if not message:
        return

    chat = update.effective_chat
    if not chat:
        return

    chunks = _split_message_chunks(message, limit)
    if update.message:
        await update.message.reply_text(chunks[0])
        for chunk in chunks[1:]:
            await context.bot.send_message(chat.id, chunk)
    else:
        for chunk in chunks:
            await context.bot.send_message(chat.id, chunk)


def _split_message_chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    lines = text.splitlines()
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    def flush_current() -> None:
        nonlocal current_lines, current_length
        if current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0

    for line in lines:
        line_length = len(line)
        additional = line_length if not current_lines else line_length + 1
        if additional > limit:
            # Flush current lines before splitting the long line.
            flush_current()
            start = 0
            while start < line_length:
                end = min(start + limit, line_length)
                chunks.append(line[start:end])
                start = end
            continue

        if current_length + additional > limit:
            flush_current()

        current_lines.append(line)
        current_length = current_length + additional

    flush_current()

    return chunks or [""]


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


def _format_issue(issue: TokenIssue) -> str:
    timestamp = issue.deleted_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"• {issue.path.name} ({timestamp}) - {issue.reason}"


def _gather_super_admin_ids() -> Set[int]:
    ids: Set[int] = {
        int(uid)
        for uid, data in list_users().items()
        if data.get("role") == "super_admin" and str(uid).isdigit()
    }
    ids.update(DEFAULT_SUPER_ADMINS)
    return ids


@require_role("admin")
async def cleanup_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None

    report = run_cleanup(full=True)
    summary = report.summary()

    logging.info("Token cleanup requested by %s: %s", user_id, summary)
    for issue in report.deleted_files:
        logging.info(
            "Deleted token file %s at %s (%s)",
            issue.path,
            issue.deleted_at.isoformat(),
            issue.reason,
        )
    for error in report.errors:
        logging.error("Token cleanup error: %s", error)

    lines = [
        "🧹 Token cleanup 已完成（full 模式）",
        f"• 基础目录：{report.base_dir}",
        f"• 总文件数：{report.total_files}",
        f"• 删除文件数：{report.deleted_count}",
        f"• 保留文件数：{report.kept_files}",
    ]

    if report.deleted_files:
        lines.append("• 删除详情：")
        lines.extend(_format_issue(issue) for issue in report.deleted_files)
    if report.errors:
        lines.append("• 错误：")
        lines.extend(f"  - {error}" for error in report.errors)

    message = "\n".join(lines)

    if update.message:
        await update.message.reply_text(message)
    elif chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=message)

    if report.deleted_files:
        dm_lines = [
            "⚠️ Token cleanup 删除了以下凭据：",
            *(_format_issue(issue) for issue in report.deleted_files),
        ]
        dm_text = "\n".join(dm_lines)

        for admin_id in _gather_super_admin_ids():
            if admin_id is None:
                continue
            try:
                await context.bot.send_message(chat_id=admin_id, text=dm_text)
            except Exception as exc:  # pragma: no cover - defensive
                logging.warning("Failed to notify super admin %s: %s", admin_id, exc)


@require_role("super_admin")
async def reload_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reloaded = reload_admin_whitelist(force=True, source="command")
    whitelist = sorted(get_super_admin_whitelist())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    whitelist_text = ", ".join(str(uid) for uid in whitelist) if whitelist else "（空）"
    status = "✅" if reloaded else "ℹ️"
    lines = [
        f"{status} 管理员白名单已重新加载。",
        f"• 时间：{timestamp}",
        f"• 当前白名单：{whitelist_text}",
    ]
    if not reloaded:
        lines.append("• 提示：未检测到文件变更。")
    message = "\n".join(lines)

    if update.message:
        await update.message.reply_text(message)
    elif update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, message)
