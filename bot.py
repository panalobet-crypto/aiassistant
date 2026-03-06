"""
个人任务助理 Bot v3
====================
- 更智能自然语言（一步识别）
- 主动提醒：9AM简报、3PM逾期检查、6PM明日提醒、周一总结
- 任务分析：/analyze

指令:
  /today    — 今天到期
  /all      — 所有待办
  /week     — 本周任务
  /seo · /social · /ops · /personal — 按分类
  /who [名字] — 查某人任务
  /done [ID] — 标记完成
  /edit [ID] [字段] [新值] — 修改任务
  /date [YYYY-MM-DD] — 查指定日期
  /analyze  — 任务分析和优先级建议
  自然语言  — 直接输入任何内容
"""

import logging
import asyncio
import re
import json
from datetime import date, timedelta
from collections import Counter
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from config import TELEGRAM_BOT_TOKEN, MANAGER_CHAT_ID, DAILY_BRIEF_HOUR, TIMEZONE
from sheets import (get_my_tasks, write_my_task, mark_done, update_task,
                    get_tasks_due_today, get_tasks_this_week, get_tasks_by_date)
from agent import ask_claude_personal

logging.basicConfig(format="%(asctime)s — %(name)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

CAT_ICONS  = {"SEO": "🔍", "Social": "📱", "Ops": "⚙️", "Personal": "👤"}
PRIO_ICONS = {"HIGH": "🔴", "MED": "🟡", "LOW": "🟢"}
PRIO_LABEL = {"HIGH": "🔴 紧急", "MED": "🟡 普通", "LOW": "🟢 不急"}


# ─── HELPERS ────────────────────────────────────────────

def format_task_list(tasks: list, title: str) -> str:
    if not tasks:
        return f"✅ {title}：没有任务。"
    lines = [f"📋 *{title}* ({len(tasks)} 项)\n"]
    for t in tasks:
        cat      = str(t.get("Category", "Ops"))
        prio     = str(t.get("Priority", "MED"))
        due      = str(t.get("Due Date", ""))
        assignee = str(t.get("Assignee", "Me"))
        tid      = str(t.get("Task ID", ""))
        ttl      = str(t.get("Title", ""))
        today    = date.today().isoformat()
        overdue  = " ⚠️逾期" if due and due < today else ""
        due_str  = f" · 📅{due}{overdue}" if due else ""
        a_str    = f" · 👤{assignee}" if assignee and assignee != "Me" else ""
        lines.append(f"{CAT_ICONS.get(cat,'📌')} {PRIO_ICONS.get(prio,'🟡')} `{tid}` {ttl}{due_str}{a_str}")
    return "\n".join(lines)


def next_weekday(weekday: int) -> str:
    """返回下一个指定weekday的日期 (0=周一)"""
    today = date.today()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


# ─── COMMANDS ───────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *你好！我是你的个人任务助理 v3*\n\n"
        "直接告诉我任何任务，我自动记录分类：\n"
        "💬 _'明天跟进 John 付款，委派给 Suman'_\n"
        "💬 _'周五前完成 IPL SEO 规划，高优先级'_\n"
        "💬 _'下周四发 PBC 的 EDM，委派给 Trisha'_\n"
        "💬 _'下周一有什么任务？'_\n\n"
        "*指令：*\n"
        "/today · /all · /week · /analyze\n"
        "/seo · /social · /ops · /personal\n"
        "/who [名字] · /date [日期]\n"
        "/done [ID] · /edit [ID] [字段] [新值]",
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
        await update.message.reply_text("例如：`/who Suman`", parse_mode="Markdown")
        return
    name = " ".join(context.args)
    tasks = get_my_tasks("pending", assignee=name)
    await update.message.reply_text(format_task_list(tasks, f"👤 {name} 的任务"), parse_mode="Markdown")


async def cmd_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("例如：`/date 2026-03-09`", parse_mode="Markdown")
        return
    target = context.args[0]
    tasks = get_tasks_by_date(target)
    await update.message.reply_text(format_task_list(tasks, f"📅 {target} 的任务"), parse_mode="Markdown")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("例如：`/done P001`", parse_mode="Markdown")
        return
    task_id = context.args[0].upper()
    if mark_done(task_id):
        await update.message.reply_text(f"✅ 任务 *{task_id}* 已完成！", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ 找不到 *{task_id}*，用 /all 查看列表。", parse_mode="Markdown")


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "用法：`/edit [ID] [字段] [新值]`\n\n"
            "字段：`title` · `assignee` · `due` · `priority` · `category` · `notes`\n\n"
            "例子：\n"
            "`/edit P001 assignee Suman`\n"
            "`/edit P001 due 2026-03-15`\n"
            "`/edit P001 priority HIGH`",
            parse_mode="Markdown"
        )
        return
    task_id = context.args[0].upper()
    field   = context.args[1].lower()
    value   = " ".join(context.args[2:])
    if update_task(task_id, field, value):
        await update.message.reply_text(
            f"✅ `{task_id}` 已更新\n*{field}* → `{value}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚠️ 更新失败，请确认任务 ID 和字段名。",
            parse_mode="Markdown"
        )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 分析中...", parse_mode="Markdown")
    all_tasks = get_my_tasks("pending")
    if not all_tasks:
        await update.message.reply_text("✅ 没有待办任务！", parse_mode="Markdown")
        return
    reply = ask_claude_personal(
        f"今天是 {date.today()}（{['周一','周二','周三','周四','周五','周六','周日'][date.today().weekday()]}）。\n\n"
        f"所有待办任务：\n{json.dumps(all_tasks, ensure_ascii=False, indent=2)}\n\n"
        f"请分析：\n"
        f"1) 🔴 本周最重要的3件事（考虑截止日期+优先级）\n"
        f"2) ⚠️ 有没有冲突（同一天多个HIGH，或某人任务过载）\n"
        f"3) 💡 建议：哪些可以延期，哪些可以委派给别人\n"
        f"简洁直接，用中文回答。"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")


# ─── NATURAL LANGUAGE ───────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    today     = date.today()
    tomorrow  = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    next_mon  = next_weekday(0)
    next_tue  = next_weekday(1)
    next_wed  = next_weekday(2)
    next_thu  = next_weekday(3)
    next_fri  = next_weekday(4)
    weekday_name = ['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]

    extract_prompt = f"""今天是 {today}（{weekday_name}）。
下周一={next_mon}, 下周二={next_tue}, 下周三={next_wed}, 下周四={next_thu}, 下周五={next_fri}, 明天={tomorrow}

用户说："{user_text}"

请返回 JSON（只返回 JSON，不要其他文字）：

新任务：
{{"type":"task","title":"简洁标题","category":"SEO/Social/Ops/Personal","assignee":"负责人(没有填Me)","due":"YYYY-MM-DD或null","priority":"HIGH/MED/LOW","notes":""}}

查询任务：
{{"type":"query","filter":"today/all/week/overdue/[人名]/[分类]/[YYYY-MM-DD]"}}

分析请求：
{{"type":"analyze"}}

普通对话：
{{"type":"chat","reply":"回复内容"}}

分类：SEO=搜索优化, Social=社交媒体平台, Ops=运营/服务器/报告/会议, Personal=私人
优先级：紧急/今天/马上=HIGH, 一般=MED, 不急=LOW
委派：'委派给X'/'交给X'/'让X做'/'assign to X' → assignee=X"""

    raw = ask_claude_personal(extract_prompt)

    try:
        clean = re.sub(r'```json|```', '', raw).strip()
        data  = json.loads(clean)
        t     = data.get("type")

        if t == "task":
            task_id = write_my_task(data)
            if task_id:
                cat      = data.get("category", "Ops")
                assignee = data.get("assignee", "Me")
                a_str    = f"\n👤 {assignee}" if assignee != "Me" else ""
                due_str  = f"\n📅 {data.get('due')}" if data.get("due") else ""
                await update.message.reply_text(
                    f"✅ *已记录* `{task_id}`\n\n"
                    f"{CAT_ICONS.get(cat,'📌')} [{cat}] {data.get('title')}"
                    f"{due_str}{a_str}\n"
                    f"⚡ {PRIO_LABEL.get(data.get('priority','MED'))}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ 记录失败，请重试。")

        elif t == "query":
            f = data.get("filter", "all")
            if f == "today":
                tasks, title = get_tasks_due_today(), "今日任务"
            elif f == "overdue":
                all_t  = get_my_tasks("pending")
                tasks  = [x for x in all_t if str(x.get("Due Date","")).strip() and str(x.get("Due Date","")).strip() < today.isoformat()]
                title  = "逾期任务"
            elif f == "week":
                tasks, title = get_tasks_this_week(), "本周任务"
            elif f in ["SEO","Social","Ops","Personal"]:
                tasks, title = get_my_tasks("pending", category=f), f"{f} 任务"
            elif f and f[0].isdigit():
                tasks, title = get_tasks_by_date(f), f"{f} 的任务"
            else:
                # 人名或其他 — 尝试 assignee 匹配
                tasks = get_my_tasks("pending", assignee=f) if f != "all" else get_my_tasks("pending")
                title = f"{f} 的任务" if f != "all" else "所有待办"

            # 让 Claude 解读数据并回答
            all_tasks = get_my_tasks("pending")
            reply = ask_claude_personal(
                f"今天是 {today}。用户问：\"{user_text}\"\n\n"
                f"相关任务：\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
                f"所有待办：\n{json.dumps(all_tasks, ensure_ascii=False, indent=2)}\n\n"
                f"请直接回答用户问题，给出具体建议。"
            )
            await update.message.reply_text(reply, parse_mode="Markdown")

        elif t == "analyze":
            await cmd_analyze(update, context)

        else:
            # 普通对话 — 自动带入任务数据
            all_tasks = get_my_tasks("pending")
            if all_tasks:
                reply = ask_claude_personal(
                    f"今天是 {today}（{weekday_name}）。\n\n"
                    f"用户的所有待办任务：\n{json.dumps(all_tasks, ensure_ascii=False, indent=2)}\n\n"
                    f"用户说：\"{user_text}\"\n\n"
                    f"根据以上任务数据，直接回答用户的问题或给出建议。"
                )
            else:
                reply = data.get("reply") or "目前没有待办任务，直接告诉我新任务，我帮你记录！"
            await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"handle_message error: {e}, raw: {raw}")
        await update.message.reply_text("⚠️ 没听清楚，再说一次？", parse_mode="Markdown")


# ─── SCHEDULED JOBS ─────────────────────────────────────

async def job_daily_brief(app):
    """9AM 每日简报"""
    try:
        tasks       = get_tasks_due_today()
        all_pending = get_my_tasks("pending")
        overdue     = [t for t in all_pending
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
                a = t.get("Assignee","Me")
                lines.append(f"  • `{t['Task ID']}` {t['Title']}{f' → {a}' if a != 'Me' else ''}")
        if not overdue and not tasks:
            lines.append("✅ 今天没有到期任务，继续加油！")
        lines.append(f"\n📊 总待办：{len(all_pending)} 项")
        await app.bot.send_message(chat_id=MANAGER_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"daily brief failed: {e}")


async def job_overdue_check(app):
    """3PM 逾期检查"""
    try:
        all_pending = get_my_tasks("pending")
        overdue = [t for t in all_pending
                   if str(t.get("Due Date","")).strip() and
                   str(t.get("Due Date","")).strip() < date.today().isoformat()]
        if not overdue:
            return
        lines = ["🚨 *下午逾期提醒*\n"]
        for t in overdue:
            a = t.get("Assignee","Me")
            lines.append(f"• `{t['Task ID']}` {t['Title']} (应于 {t['Due Date']} 完成){f' → {a}' if a != 'Me' else ''}")
        lines.append("\n`/done [ID]` 标记完成  ·  `/edit [ID] due [新日期]` 延期")
        await app.bot.send_message(chat_id=MANAGER_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"overdue check failed: {e}")


async def job_tomorrow_reminder(app):
    """6PM 明日提醒"""
    try:
        tomorrow  = (date.today() + timedelta(days=1)).isoformat()
        all_pending = get_my_tasks("pending")
        tmr_tasks = [t for t in all_pending if str(t.get("Due Date","")).strip() == tomorrow]
        if not tmr_tasks:
            return
        lines = ["⏰ *明日到期提醒*\n"]
        for t in tmr_tasks:
            a = t.get("Assignee","Me")
            lines.append(f"• `{t['Task ID']}` {t['Title']}{f' → {a}' if a != 'Me' else ''}")
        await app.bot.send_message(chat_id=MANAGER_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"tomorrow reminder failed: {e}")


async def job_weekly_summary(app):
    """周一 9AM 每周总结"""
    try:
        all_tasks  = get_my_tasks("all")
        done       = [t for t in all_tasks if str(t.get("Status","")).lower() == "done"]
        pending    = [t for t in all_tasks if str(t.get("Status","")).lower() != "done"]
        week_tasks = get_tasks_this_week()
        cat_count  = Counter(str(t.get("Category","Ops")) for t in pending)
        lines = [f"📊 *每周任务总结* — {date.today()}\n",
                 f"✅ 已完成：{len(done)} 项",
                 f"⏳ 待办：{len(pending)} 项\n",
                 "*按分类：*"]
        for cat, count in cat_count.most_common():
            lines.append(f"  {CAT_ICONS.get(cat,'📌')} {cat}：{count} 项")
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

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("all",      cmd_all))
    app.add_handler(CommandHandler("week",     cmd_week))
    app.add_handler(CommandHandler("seo",      cmd_seo))
    app.add_handler(CommandHandler("social",   cmd_social))
    app.add_handler(CommandHandler("ops",      cmd_ops))
    app.add_handler(CommandHandler("personal", cmd_personal))
    app.add_handler(CommandHandler("who",      cmd_who))
    app.add_handler(CommandHandler("date",     cmd_date))
    app.add_handler(CommandHandler("done",     cmd_done))
    app.add_handler(CommandHandler("edit",     cmd_edit))
    app.add_handler(CommandHandler("analyze",  cmd_analyze))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(lambda: asyncio.ensure_future(job_daily_brief(app)),
                      "cron", hour=DAILY_BRIEF_HOUR, minute=0)
    scheduler.add_job(lambda: asyncio.ensure_future(job_overdue_check(app)),
                      "cron", hour=15, minute=0)
    scheduler.add_job(lambda: asyncio.ensure_future(job_tomorrow_reminder(app)),
                      "cron", hour=18, minute=0)
    scheduler.add_job(lambda: asyncio.ensure_future(job_weekly_summary(app)),
                      "cron", day_of_week="mon", hour=DAILY_BRIEF_HOUR, minute=0)
    scheduler.start()

    logger.info("个人任务助理 Bot v3 启动...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
