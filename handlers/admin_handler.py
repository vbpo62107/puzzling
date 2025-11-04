import html
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Set, Tuple

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import (
    LogSearchRequest,
    parse_log_search_arguments,
    query_logs,
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


def _truncate_text(text: str, limit: int = 180) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return text[:1]
    return text[: limit - 1] + "…"


def _format_counter(items: Sequence[Tuple[str, int]], limit: int = 3) -> str:
    filtered = [(label or "-", count) for label, count in items if count]
    if not filtered:
        return "无"
    parts = [f"{label}×{count}" for label, count in filtered[:limit]]
    if len(filtered) > limit:
        parts.append("…")
    return "，".join(parts)


def _format_summary_block(summary: Dict[str, Any], sample_size: int) -> List[str]:
    lines = [f"📊 汇总统计（样本：{sample_size} 条）"]
    time_range = summary.get("time_range")
    if (
        isinstance(time_range, (list, tuple))
        and len(time_range) == 2
        and all(isinstance(item, str) for item in time_range)
    ):
        start, end = time_range
        if start == end:
            lines.append(f"• 时间：{start}")
        else:
            lines.append(f"• 时间范围：{start} ~ {end}")
    lines.append(f"• 类别分布：{_format_counter(summary.get('categories', []))}")
    if summary.get("commands"):
        lines.append(f"• 指令分布：{_format_counter(summary['commands'])}")
    if summary.get("levels"):
        lines.append(f"• 等级分布：{_format_counter(summary['levels'])}")
    if summary.get("user_ids"):
        lines.append(f"• 用户分布：{_format_counter(summary['user_ids'])}")
    lines.append(
        f"• 唯一用户：{summary.get('unique_users', 0)}，唯一指令：{summary.get('unique_commands', 0)}"
    )
    return lines


def _format_log_entry(entry: Dict[str, Any], index: int) -> List[str]:
    timestamp = str(entry.get("timestamp") or "-")
    category = str(entry.get("category") or "-")
    header = f"{index}. {timestamp}｜{category}"

    details: List[str] = []
    user_obj = entry.get("user")
    if isinstance(user_obj, dict):
        uid = user_obj.get("id")
        role = user_obj.get("role")
        if uid is not None:
            detail = f"UID={uid}"
            if role:
                detail += f"({role})"
            details.append(detail)
    command = entry.get("command")
    if command:
        details.append(f"指令={command}")
    level = entry.get("level")
    if level:
        details.append(f"等级={level}")
    source = entry.get("source")
    if source:
        details.append(f"来源={source}")
    tag = entry.get("tag")
    if tag:
        details.append(f"标签={tag}")
    verification = entry.get("verification")
    if verification:
        details.append(f"验证={verification}")
    duration = entry.get("duration_ms")
    if isinstance(duration, (int, float)):
        details.append(f"耗时={duration:.0f}ms")
    elif duration is not None:
        details.append(f"耗时={duration}")
    if details:
        header += "｜" + "｜".join(str(part) for part in details)

    lines = [header]

    message = entry.get("message")
    if isinstance(message, str) and message:
        lines.append(f"   📝 {_truncate_text(message, 180)}")

    event = entry.get("event")
    if isinstance(event, str) and event and event != message:
        lines.append(f"   🧩 事件：{_truncate_text(event, 180)}")

    metadata = entry.get("metadata")
    if metadata:
        metadata_text = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        lines.append(f"   📦 元数据：{_truncate_text(metadata_text, 180)}")

    extra_keys = {
        key: value
        for key, value in entry.items()
        if key
        not in {
            "timestamp",
            "category",
            "user",
            "command",
            "message",
            "event",
            "metadata",
            "level",
            "source",
            "tag",
            "verification",
            "duration_ms",
        }
        and value not in (None, "")
    }
    if extra_keys:
        extra_text = json.dumps(extra_keys, ensure_ascii=False, sort_keys=True)
        lines.append(f"   🔧 其它：{_truncate_text(extra_text, 180)}")

    return lines


def _gather_super_admin_ids() -> Set[int]:
    ids: Set[int] = {
        int(uid)
        for uid, data in list_users().items()
        if data.get("role") == "super_admin" and str(uid).isdigit()
    }
    ids.update(DEFAULT_SUPER_ADMINS)
    return ids


@require_role("admin")
async def search_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    try:
        request: LogSearchRequest = parse_log_search_arguments(args)
    except ValueError as exc:
        message = f"❌ 参数错误：{exc}"
        if update.message:
            await update.message.reply_text(message)
        elif update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, message)
        return

    DEFAULT_LIMIT = 20
    MAX_DISPLAY = 50
    MAX_FETCH = 200

    original_limit = request.query.limit
    if original_limit in (None, 0):
        display_limit = DEFAULT_LIMIT
    else:
        display_limit = max(0, min(original_limit, MAX_DISPLAY))
    truncated_limit = bool(original_limit is not None and original_limit > MAX_DISPLAY)

    if display_limit == 0:
        fetch_limit = MAX_FETCH if request.summary else 1
    else:
        fetch_limit = min(display_limit + 1, MAX_FETCH)

    request.query.limit = fetch_limit
    reverse = request.order != "asc"

    results = query_logs(request.query, reverse=reverse)

    hit_fetch_cap = fetch_limit == MAX_FETCH and len(results) == fetch_limit

    if display_limit > 0:
        display_entries = results[:display_limit]
        has_more = len(results) > display_limit
    else:
        display_entries = []
        has_more = False

    summary_entries: List[Dict[str, Any]] = []
    if request.summary:
        if display_limit == 0:
            summary_entries = results
        elif has_more:
            summary_entries = display_entries
        else:
            summary_entries = results

    summary_lines: List[str] = []
    if request.summary:
        if summary_entries:
            summary = summarize_logs(summary_entries)
            sample_size = summary.get("total", len(summary_entries))
            summary_lines = _format_summary_block(summary, sample_size)
        else:
            summary_lines = ["📊 汇总统计：暂无匹配数据。"]

    lines: List[str] = []
    order_text = "最新优先" if reverse else "时间顺序"
    header_parts = [f"排序：{order_text}"]
    if display_limit > 0:
        header_parts.append(f"展示 {len(display_entries)}/{display_limit} 条")
    else:
        header_parts.append(f"展示 {len(display_entries)} 条")
    if truncated_limit:
        header_parts.append(f"已限制为最多 {MAX_DISPLAY} 条")
    if has_more or hit_fetch_cap:
        header_parts.append("还有更多…")
    lines.append("🔎 日志搜索结果（" + "，".join(header_parts) + "）")

    if args:
        lines.append(f"🧭 条件：{' '.join(args)}")

    lines.extend(summary_lines)

    if display_entries:
        lines.append("🗂️ 匹配日志：")
        for idx, entry in enumerate(display_entries, 1):
            lines.extend(_format_log_entry(entry, idx))
    else:
        if not summary_lines:
            lines.append("ℹ️ 未找到符合条件的日志记录。")
        elif not results:
            lines.append("ℹ️ 未找到符合条件的日志记录。")
        else:
            lines.append("ℹ️ 已根据条件输出统计，可通过 --limit 调整展示数量。")

    if has_more or hit_fetch_cap:
        lines.append("⚠️ 提示：还有更多匹配记录，建议使用 CLI 工具 tools/search_logs.py 查看详情。")

    message = "\n".join(lines)
    if update.message:
        await update.message.reply_text(message)
    elif update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, message)


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


@require_role("admin")
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
