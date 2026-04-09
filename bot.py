"""
个人任务助理 Bot v4
====================
- 记录任务时自动询问缺失字段（分类/负责人/截止日期/优先级）
- 更智能自然语言
- 主动提醒：9AM简报、3PM逾期检查、6PM明日提醒、周一总结
- /analyze 任务分析
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

PENDING_TASK = "pending_task"
PENDING_STEP = "pending_step"


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
        overdue  = " ⚠️逾期" if due and due < date.today().isoformat() else ""
        due_str  = f" · 📅{due}{overdue}" if due else ""
        a_str    = f" · 👤{assignee}" if assignee and assignee != "Me" else ""
        lines.append(f"{CAT_ICONS.get(cat,'📌')} {PRIO_ICONS.get(prio,'🟡')} `{tid}` {ttl}{due_str}{a_str}")
    return "\n".join(lines)


def next_weekday(weekday: int) -> str:
    today = date.today()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def confirm_task_text(task_data: dict) -> str:
    cat      = task_data.get("category", "?")
    assignee = task_data.get("assignee", "Me")
    due      = task_data.get("due") or "无截止日期"
    prio     = task_data.get("priority", "MED")
    return (
        f"✅ *已记录！*\n\n"
        f"{CAT_ICONS.get(cat,'📌')} [{cat}] {task_data.get('title')}\n"
        f"📅 {due}\n"
        f"👤 {assignee}\n"
        f"⚡ {PRIO_LABEL.get(prio,'🟡 普通')}"
    )


# ─── MULTI-STEP TASK FLOW ───────────────────────────────

async def ask_next_field(update, context, task_data: dict) -> bool:
    """问缺失字段。返回 True = 还在问，False = 所有字段齐全可以记录。"""
    today    = date.today()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    next_fri = next_weekday(4)
    next_mon = next_weekday(0)

    if not task_data.get("category"):
        context.user_data[PENDING_TASK] = task_data
        context.user_data[PENDING_STEP] = "category"
        await update.message.reply_text(
            f"📌 *任务：{task_data.get('title')}*\n\n这个任务属于哪个分类？\n\n"
            f"1️⃣ 🔍 SEO\n2️⃣ 📱 Social（社交媒体）\n3️⃣ ⚙️ Ops（运营）\n4️⃣ 👤 Personal（个人）",
            parse_mode="Markdown"
        )
        return True

    if not task_data.get("assignee"):
        context.user_data[PENDING_TASK] = task_data
        context.user_data[PENDING_STEP] = "assignee"
        await update.message.reply_text(
            f"👤 *谁负责这个任务？（可多选，用逗号分隔）*\n\n"
            f"1️⃣ 我自己\n2️⃣ Suman\n3️⃣ Trisha\n4️⃣ Gopi\n5️⃣ Kanhana\n6️⃣ Jovan\n"
            f"7️⃣ 其他（直接输入名字）\n\n"
            f"_例：输入 `2,3` = Suman 和 Trisha 一起负责_",
            parse_mode="Markdown"
        )
        return True

    if not task_data.get("due"):
        context.user_data[PENDING_TASK] = task_data
        context.user_data[PENDING_STEP] = "due"
        await update.message.reply_text(
            f"📅 *截止日期？*\n\n"
            f"1️⃣ 今天（{today}）\n2️⃣ 明天（{tomorrow}）\n"
            f"3️⃣ 本周五（{next_fri}）\n4️⃣ 下周一（{next_mon}）\n"
            f"5️⃣ 无截止日期\n6️⃣ 其他（输入日期如 2026-03-20）",
            parse_mode="Markdown"
        )
        return True

    if not task_data.get("priority"):
        context.user_data[PENDING_TASK] = task_data
        context.user_data[PENDING_STEP] = "priority"
        await update.message.reply_text(
            f"⚡ *优先级？*\n\n1️⃣ 🔴 HIGH（紧急）\n2️⃣ 🟡 MED（普通）\n3️⃣ 🟢 LOW（不急）",
            parse_mode="Markdown"
        )
        return True

    return False


async def handle_pending_step(update, context, user_text: str) -> bool:
    """处理用户对缺失字段的回答。返回 True = 已处理此消息。"""
    task_data = context.user_data.get(PENDING_TASK)
    step      = context.user_data.get(PENDING_STEP)
    if not task_data or not step:
        return False

    t = user_text.strip()

    if step == "category":
        mapping = {"1":"SEO","2":"Social","3":"Ops","4":"Personal",
                   "seo":"SEO","social":"Social","ops":"Ops","personal":"Personal",
                   "社交":"Social","运营":"Ops","个人":"Personal"}
        cat = mapping.get(t.lower()) or (t if t in ["SEO","Social","Ops","Personal"] else None)
        if not cat:
            await update.message.reply_text("请选择 1-4 或输入分类名（SEO/Social/Ops/Personal）")
            return True
        task_data["category"] = cat

    elif step == "assignee":
        mapping = {"1":"Me","2":"Suman","3":"Trisha","4":"Gopi",
                   "5":"Kanhana","6":"Jovan","我自己":"Me","自己":"Me","me":"Me"}
        parts = [p.strip() for p in re.split(r"[,，、]+", t) if p.strip()]
        names = []
        for p in parts:
            resolved = mapping.get(p) or mapping.get(p.lower())
            names.append(resolved if resolved else p)
        task_data["assignee"] = ", ".join(names) if names else "Me"

    elif step == "due":
        today    = date.today()
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        mapping  = {"1":today.isoformat(),"2":tomorrow,"3":next_weekday(4),
                    "4":next_weekday(0),"5":None,"无":None,"没有":None}
        if t in mapping:
            task_data["due"] = mapping[t]
        elif re.match(r'\d{4}-\d{2}-\d{2}', t):
            task_data["due"] = t
        else:
            await update.message.reply_text("请选择 1-6 或输入日期（格式：2026-03-20）")
            return True

    elif step == "priority":
        mapping = {"1":"HIGH","2":"MED","3":"LOW",
                   "high":"HIGH","med":"MED","low":"LOW",
                   "紧急":"HIGH","普通":"MED","不急":"LOW"}
        prio = mapping.get(t.lower())
        if not prio:
            await update.message.reply_text("请选择 1-3 或输入 HIGH/MED/LOW")
            return True
        task_data["priority"] = prio

    context.user_data[PENDING_TASK] = task_data
    context.user_data[PENDING_STEP] = None

    # 检查是否还有缺失字段
    still_missing = await ask_next_field(update, context, task_data)
    if still_missing:
        return True

    # 全部字段齐全，记录任务
    task_id = write_my_task(task_data)
    context.user_data[PENDING_TASK] = None
    context.user_data[PENDING_STEP] = None

    if task_id:
        task_data["task_id"] = task_id
        await update.message.reply_text(
            f"✅ *已记录* `{task_id}`\n\n" +
            confirm_task_text(task_data),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ 记录失败，请重试。")
    return True


# ─── COMMANDS ───────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *你好！我是你的个人任务助理 v4*\n\n"
        "直接告诉我任何任务，我自动记录：\n"
        "💬 _'明天跟进 John 付款'_\n"
        "💬 _'周五前完成 IPL SEO 规划，高优先级，委派给Suman'_\n"
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
    tasks = get_tasks_by_date(context.args[0])
    await update.message.reply_text(format_task_list(tasks, f"📅 {context.args[0]} 的任务"), parse_mode="Markdown")

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
            "例子：\n`/edit P001 assignee Suman`\n`/edit P001 due 2026-03-15`",
            parse_mode="Markdown"
        )
        return
    task_id = context.args[0].upper()
    field   = context.args[1].lower()
    value   = " ".join(context.args[2:])
    if update_task(task_id, field, value):
        await update.message.reply_text(f"✅ `{task_id}` 已更新\n*{field}* → `{value}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ 更新失败，请确认任务 ID 和字段名。", parse_mode="Markdown")

async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 分析中...", parse_mode="Markdown")
    all_tasks = get_my_tasks("pending")
    if not all_tasks:
        await update.message.reply_text("✅ 没有待办任务！", parse_mode="Markdown")
        return
    reply = ask_claude_personal(
        f"今天是 {date.today()}（{['周一','周二','周三','周四','周五','周六','周日'][date.today().weekday()]}）。\n\n"
        f"所有待办任务：\n{json.dumps(all_tasks, ensure_ascii=False, indent=2)}\n\n"
        f"请分析：\n1) 🔴 本周最重要的3件事\n2) ⚠️ 有没有冲突或某人任务过载\n3) 💡 哪些可以延期或委派\n简洁直接。"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")


# ─── NATURAL LANGUAGE ───────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    # 先处理待完成的对话步骤
    if context.user_data.get(PENDING_STEP):
        handled = await handle_pending_step(update, context, user_text)
        if handled:
            return

    today        = date.today()
    tomorrow     = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    next_mon     = next_weekday(0)
    next_tue     = next_weekday(1)
    next_wed     = next_weekday(2)
    next_thu     = next_weekday(3)
    next_fri     = next_weekday(4)
    weekday_name = ['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]

    extract_prompt = f"""今天是 {today}（{weekday_name}）。
下周一={next_mon}, 下周二={next_tue}, 下周三={next_wed}, 下周四={next_thu}, 下周五={next_fri}, 明天={tomorrow}

用户说："{user_text}"

请返回 JSON（只返回 JSON，不要其他文字）：

新任务：
{{"type":"task","title":"简洁标题","category":"SEO/Social/Ops/Personal或null","assignee":"负责人或null","due":"YYYY-MM-DD或null","priority":"HIGH/MED/LOW或null","notes":""}}

查询任务：
{{"type":"query","filter":"today/all/week/overdue/[人名]/[分类]/[YYYY-MM-DD]"}}

分析请求：
{{"type":"analyze"}}

普通对话：
{{"type":"chat","reply":"回复内容"}}

重要：如果用户没有明确说明某字段，填 null，不要猜测。
分类：SEO=搜索优化, Social=社交媒体, Ops=运营/服务器/报告/会议, Personal=私人
优先级：紧急/今天/马上=HIGH, 一般=MED, 不急=LOW
委派：'委派给X'/'交给X'/'让X做' → assignee=X"""

    raw = ask_claude_personal(extract_prompt)

    try:
        clean = re.sub(r'```json|```', '', raw).strip()
        data  = json.loads(clean)
        t     = data.get("type")

        if t == "task":
            still_missing = await ask_next_field(update, context, data)
            if still_missing:
                return
            task_id = write_my_task(data)
            if task_id:
                data["task_id"] = task_id
                await update.message.reply_text(
                    f"✅ *已记录* `{task_id}`\n\n" + confirm_task_text(data),
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ 记录失败，请重试。")

        elif t == "query":
            f = data.get("filter", "all")
            if f == "today":
                tasks = get_tasks_due_today()
            elif f == "overdue":
                all_t = get_my_tasks("pending")
                tasks = [x for x in all_t if str(x.get("Due Date","")).strip() and str(x.get("Due Date","")).strip() < today.isoformat()]
            elif f == "week":
                tasks = get_tasks_this_week()
            elif f in ["SEO","Social","Ops","Personal"]:
                tasks = get_my_tasks("pending", category=f)
            elif f and f[0].isdigit():
                tasks = get_tasks_by_date(f)
            else:
                tasks = get_my_tasks("pending", assignee=f) if f != "all" else get_my_tasks("pending")

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
            all_tasks = get_my_tasks("pending")
            if all_tasks:
                reply = ask_claude_personal(
                    f"今天是 {today}（{weekday_name}）。\n\n"
                    f"用户的所有待办任务：\n{json.dumps(all_tasks, ensure_ascii=False, indent=2)}\n\n"
                    f"用户说：\"{user_text}\"\n\n"
                    f"根据任务数据，直接回答用户的问题或给出建议。"
                )
            else:
                reply = data.get("reply") or "目前没有待办任务，直接告诉我新任务，我帮你记录！"
            await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"handle_message error: {e}, raw: {raw}")
        await update.message.reply_text("⚠️ 没听清楚，再说一次？", parse_mode="Markdown")


# ─── SCHEDULED JOBS ─────────────────────────────────────

async def job_daily_brief(app):
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
    try:
        tomorrow    = (date.today() + timedelta(days=1)).isoformat()
        all_pending = get_my_tasks("pending")
        tmr_tasks   = [t for t in all_pending if str(t.get("Due Date","")).strip() == tomorrow]
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

    logger.info("个人任务助理 Bot v4 启动...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
