"""
个人任务助理 Bot v2
====================
分类: SEO / Social / Ops / Personal
委派: 可指定负责人

指令:
  /today    — 今天到期
  /all      — 所有待办
  /week     — 本周任务
  /seo      — SEO 任务
  /social   — 社交媒体任务
  /ops      — 运营任务
  /personal — 个人任务
  /who [名字] — 查某人的任务
  /done [ID] — 标记完成
  自然语言   — 直接输入任何任务描述
"""

import logging
import asyncio
import re
import json
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from config import TELEGRAM_BOT_TOKEN, MANAGER_CHAT_ID, DAILY_BRIEF_HOUR, TIMEZONE
from sheets import (get_tasks, get_kpi_data, get_my_tasks, write_my_task, mark_done,
                    get_tasks_due_today, get_tasks_this_week)
from agent import ask_claude_personal

logging.basicConfig(format="%(asctime)s — %(name)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── HELPERS ────────────────────────────────────────────

def format_task_list(tasks: list, title: str) -> str:
    if not tasks:
        return f"✅ {title}：没有任务。"
    lines = [f"📋 *{title}* ({len(tasks)} 项)\n"]
    cat_icons = {"SEO": "🔍", "Social": "📱", "Ops": "⚙️", "Personal": "👤"}
    prio_icons = {"HIGH": "🔴", "MED": "🟡", "LOW": "🟢"}
    for t in tasks:
        cat = str(t.get("Category", "Ops"))
        prio = str(t.get("Priority", "MED"))
        due = str(t.get("Due Date", ""))
        assignee = str(t.get("Assignee", "Me"))
        tid = str(t.get("Task ID", ""))
        title_text = str(t.get("Title", ""))
        due_str = f" · 📅{due}" if due else ""
        assignee_str = f" · 👤{assignee}" if assignee and assignee != "Me" else ""
        lines.append(
            f"{cat_icons.get(cat,'📌')} {prio_icons.get(prio,'🟡')} `{tid}` {title_text}{due_str}{assignee_str}"
        )
    return "\n".join(lines)


# ─── COMMANDS ───────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *你好！我是你的个人任务助理。*\n\n"
        "直接告诉我任何任务，我自动记录：\n"
        "💬 _'明天跟进 John 付款，委派给 Suman'_\n"
        "💬 _'周五前完成 IPL SEO 规划，高优先级'_\n"
        "💬 _'下週四之前要發PBC的edm，委派给 Trisha'_\n\n"
        "*筛选指令：*\n"
        "/today · /all · /week\n"
        "/seo · /social · /ops · /personal\n"
        "/who [名字] — 查某人任务\n"
        "/done [ID] — 标记完成",
        parse_mode="Markdown"
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_tasks_due_today()
    await update.message.reply_text(format_task_list(tasks, "今日任务"), parse_mode="Markdown")


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_my_tasks("pending")
    await update.message.reply_text(format_task_list(tasks, "所有待办"), parse_mode="Markdown")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_tasks_this_week()
    await update.message.reply_text(format_task_list(tasks, "本周任务"), parse_mode="Markdown")


async def cmd_seo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_my_tasks("pending", category="SEO")
    await update.message.reply_text(format_task_list(tasks, "🔍 SEO 任务"), parse_mode="Markdown")


async def cmd_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_my_tasks("pending", category="Social")
    await update.message.reply_text(format_task_list(tasks, "📱 社交媒体任务"), parse_mode="Markdown")


async def cmd_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_my_tasks("pending", category="Ops")
    await update.message.reply_text(format_task_list(tasks, "⚙️ 运营任务"), parse_mode="Markdown")


async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_my_tasks("pending", category="Personal")
    await update.message.reply_text(format_task_list(tasks, "👤 个人任务"), parse_mode="Markdown")


async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("请输入名字，例如：`/who Suman`", parse_mode="Markdown")
        return
    name = " ".join(context.args)
    tasks = get_my_tasks("pending", assignee=name)
    await update.message.reply_text(format_task_list(tasks, f"👤 {name} 的任务"), parse_mode="Markdown")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("请输入任务 ID，例如：`/done P001`", parse_mode="Markdown")
        return
    task_id = context.args[0].upper()
    if mark_done(task_id):
        await update.message.reply_text(f"✅ 任务 *{task_id}* 已完成！", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ 找不到 *{task_id}*，用 /all 查看任务列表。", parse_mode="Markdown")


# ─── NATURAL LANGUAGE ───────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    extract_prompt = f"""今天是 {date.today()}。用户发来："{user_text}"

判断这是新任务还是查询。如果是新任务，提取信息返回 JSON：
{{
  "is_task": true,
  "title": "简洁的任务标题",
  "category": "SEO 或 Social 或 Ops 或 Personal",
  "assignee": "负责人名字（没有就填 Me）",
  "due": "YYYY-MM-DD（没有就填 null）",
  "priority": "HIGH 或 MED 或 LOW",
  "notes": ""
}}

如果是查询或对话，返回：
{{"is_task": false, "reply": "你的回复内容"}}

分类规则：SEO=搜索优化相关，Social=社交媒体平台相关，Ops=运营/服务器/客户/报告，Personal=私人事务
优先级规则：紧急/今天/马上=HIGH，一般=MED，不急/低优先=LOW
委派规则：如果提到"委派给X"、"交给X"、"让X做"、"assign to X"，assignee=X的名字

只返回 JSON，不要其他文字。"""

    raw = ask_claude_personal(extract_prompt)

    try:
        clean = re.sub(r'```json|```', '', raw).strip()
        data = json.loads(clean)

        if data.get("is_task"):
            task_id = write_my_task(data)
            if task_id:
                cat_icons = {"SEO": "🔍", "Social": "📱", "Ops": "⚙️", "Personal": "👤"}
                prio_map = {"HIGH": "🔴 紧急", "MED": "🟡 普通", "LOW": "🟢 不急"}
                cat = data.get("category", "Ops")
                assignee = data.get("assignee", "Me")
                assignee_str = f"\n👤 负责人：{assignee}" if assignee and assignee != "Me" else ""
                due_str = f"\n📅 截止：{data.get('due')}" if data.get("due") else ""
                await update.message.reply_text(
                    f"✅ *已记录！* `{task_id}`\n\n"
                    f"{cat_icons.get(cat,'📌')} [{cat}] {data.get('title')}"
                    f"{due_str}"
                    f"{assignee_str}\n"
                    f"⚡ {prio_map.get(data.get('priority','MED'), '🟡 普通')}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ 记录失败，请重试。")
        else:
            reply = data.get("reply") or ask_claude_personal(
                f"用户说：\"{user_text}\"，用中文简短回答。当前待办任务：{get_my_tasks('pending')}"
            )
            await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"handle_message error: {e}, raw: {raw}")
        await update.message.reply_text(
            "⚠️ 无法解析，请换个方式，例如：\n"
            "_'周五前完成 SEO 报告，委派给 Suman'_",
            parse_mode="Markdown"
        )


# ─── SCHEDULED JOBS ─────────────────────────────────────

async def job_daily_brief(app):
    try:
        tasks = get_tasks_due_today()
        all_pending = get_my_tasks("pending")
        overdue = [t for t in all_pending
                   if str(t.get("Due Date","")).strip() and
                   str(t.get("Due Date","")).strip() < date.today().isoformat()]
        lines = [f"🌅 *今日任务简报* — {date.today()}\n"]
        if overdue:
            lines.append(f"🚨 *逾期未完成 ({len(overdue)} 项)*")
            for t in overdue[:5]:
                lines.append(f"  • `{t['Task ID']}` {t['Title']} (逾期：{t['Due Date']})")
        if tasks:
            lines.append(f"\n📋 *今天到期 ({len(tasks)} 项)*")
            for t in tasks[:5]:
                assignee = t.get("Assignee","Me")
                a_str = f" → {assignee}" if assignee != "Me" else ""
                lines.append(f"  • `{t['Task ID']}` {t['Title']}{a_str}")
        if not overdue and not tasks:
            lines.append("✅ 今天没有到期任务，继续加油！")
        lines.append(f"\n📊 总待办：{len(all_pending)} 项")
        await app.bot.send_message(chat_id=MANAGER_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"daily brief failed: {e}")


async def job_weekly_summary(app):
    try:
        all_tasks = get_my_tasks("all")
        done = [t for t in all_tasks if str(t.get("Status","")).lower() == "done"]
        pending = [t for t in all_tasks if str(t.get("Status","")).lower() != "done"]
        week_tasks = get_tasks_this_week()

        # 按分类统计
        from collections import Counter
        cat_count = Counter(str(t.get("Category","Ops")) for t in pending)

        lines = [f"📊 *每周任务总结* — {date.today()}\n",
                 f"✅ 上周完成：{len(done)} 项",
                 f"⏳ 待办总数：{len(pending)} 项\n",
                 "*按分类分布：*"]
        icons = {"SEO": "🔍", "Social": "📱", "Ops": "⚙️", "Personal": "👤"}
        for cat, count in cat_count.most_common():
            lines.append(f"  {icons.get(cat,'📌')} {cat}：{count} 项")
        if week_tasks:
            lines.append(f"\n*本周到期 ({len(week_tasks)} 项)*")
            for t in week_tasks[:5]:
                lines.append(f"  • `{t['Task ID']}` {t['Title']} ({t.get('Due Date','')})")
        await app.bot.send_message(chat_id=MANAGER_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"weekly summary failed: {e}")


# ─── MAIN ────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("seo", cmd_seo))
    app.add_handler(CommandHandler("social", cmd_social))
    app.add_handler(CommandHandler("ops", cmd_ops))
    app.add_handler(CommandHandler("personal", cmd_personal))
    app.add_handler(CommandHandler("who", cmd_who))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(lambda: asyncio.ensure_future(job_daily_brief(app)),
                      "cron", hour=DAILY_BRIEF_HOUR, minute=0)
    scheduler.add_job(lambda: asyncio.ensure_future(job_weekly_summary(app)),
                      "cron", day_of_week="mon", hour=DAILY_BRIEF_HOUR, minute=0)
    scheduler.start()

    logger.info("个人任务助理 Bot v2 启动...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
