"""
个人任务助理 Bot
================
直接用自然语言丢任务，Bot 自动记录、提醒、跟进。

指令：
  直接打任何话 — "明天要跟进 John 付款"
  /today  — 今天到期的任务
  /all    — 所有待办任务
  /week   — 本周任务
  /done   — 标记任务完成
  /help   — 所有指令

自动：
  - 每天早上 9AM 发今日任务清单
  - 每周一早上发本周任务总结
"""

import logging
import asyncio
import re
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from config import TELEGRAM_BOT_TOKEN, MANAGER_CHAT_ID, DAILY_BRIEF_HOUR, TIMEZONE
from sheets import get_my_tasks, write_my_task, mark_done, get_tasks_due_today, get_tasks_this_week
from agent import ask_claude_personal

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─── COMMANDS ───────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *你好！我是你的个人任务助理。*\n\n"
        "直接告诉我任何任务，我帮你记录和提醒：\n\n"
        "💬 _'明天下午跟进 John 关于付款的事'_\n"
        "💬 _'周五前要提交 SEO 报告，高优先级'_\n"
        "💬 _'提醒我下周一开会'_\n\n"
        "*指令：*\n"
        "/today — 今天的任务\n"
        "/all — 所有待办\n"
        "/week — 本周任务\n"
        "/done [任务ID] — 标记完成\n"
        "/help — 帮助",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *指令列表*\n\n"
        "*/today* — 今天到期的任务\n"
        "*/all* — 所有未完成任务\n"
        "*/week* — 本周任务\n"
        "*/done P001* — 把任务 P001 标记为完成\n\n"
        "*自然语言示例：*\n"
        "• _'明天跟进客户A报价'_\n"
        "• _'周五前完成 IPL 内容规划，紧急'_\n"
        "• _'下周三记得续费服务器'_\n"
        "• _'今天有哪些任务？'_\n"
        "• _'帮我总结本周进度'_",
        parse_mode="Markdown"
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 查询今日任务...", parse_mode="Markdown")
    tasks = get_tasks_due_today()
    if not tasks:
        await update.message.reply_text("✅ 今天没有到期任务！", parse_mode="Markdown")
        return
    reply = ask_claude_personal(
        f"今天是 {date.today()}。以下是今天到期的任务，请用中文整理成清晰的列表，每项包括任务ID、内容、优先级。用 emoji 标注优先级（🔴高 🟡中 🟢低）：\n\n{tasks}"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 查询所有待办任务...", parse_mode="Markdown")
    tasks = get_my_tasks(status_filter="pending")
    if not tasks:
        await update.message.reply_text("✅ 没有待办任务！", parse_mode="Markdown")
        return
    reply = ask_claude_personal(
        f"今天是 {date.today()}。以下是所有未完成任务，请用中文整理，按优先级排序，标注哪些已经逾期（🔴），哪些今天到期（⚠️），哪些还有时间（✅）：\n\n{tasks}"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 查询本周任务...", parse_mode="Markdown")
    tasks = get_tasks_this_week()
    if not tasks:
        await update.message.reply_text("📭 本周没有任务。", parse_mode="Markdown")
        return
    reply = ask_claude_personal(
        f"今天是 {date.today()}。以下是本周的任务，请用中文整理成每日清单，标注完成状态和优先级：\n\n{tasks}"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "请提供任务 ID，例如：`/done P001`",
            parse_mode="Markdown"
        )
        return
    task_id = args[0].upper()
    success = mark_done(task_id)
    if success:
        await update.message.reply_text(f"✅ 任务 *{task_id}* 已标记完成！", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"⚠️ 找不到任务 *{task_id}*。用 /all 查看所有任务 ID。",
            parse_mode="Markdown"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理所有自然语言消息"""
    user_text = update.message.text
    if not user_text:
        return

    # 先让 Claude 判断意图
    intent_prompt = f"""用户发来："{user_text}"

判断这是：
A) 新任务（需要记录）
B) 查询请求（想看任务列表）
C) 其他对话

只回答 A、B 或 C，不要其他内容。"""

    intent = ask_claude_personal(intent_prompt).strip().upper()

    if "A" in intent:
        # 提取任务信息
        await update.message.reply_text("📝 记录中...", parse_mode="Markdown")
        extract_prompt = f"""从以下消息中提取任务信息，今天是 {date.today()}：

消息："{user_text}"

请只返回 JSON 格式（不要其他文字）：
{{
  "title": "任务标题（简洁）",
  "due": "截止日期 YYYY-MM-DD（没有就填 null）",
  "priority": "HIGH / MED / LOW",
  "notes": "备注（可为空）"
}}

判断规则：
- "今天/马上/紧急" → HIGH，due = 今天
- "明天" → due = 明天日期
- "周X" → 计算最近的那个周X
- "下周" → 下周同一天
- 没提到时间 → due = null，priority = MED"""

        import json
        raw = ask_claude_personal(extract_prompt)
        try:
            raw_clean = re.sub(r'```json|```', '', raw).strip()
            task_data = json.loads(raw_clean)
            saved = write_my_task(task_data)
            if saved:
                due_text = f"📅 截止：{task_data.get('due', '未设定')}" if task_data.get('due') else "📅 截止：未设定"
                prio_map = {"HIGH": "🔴 高", "MED": "🟡 中", "LOW": "🟢 低"}
                prio_text = prio_map.get(task_data.get('priority', 'MED'), '🟡 中')
                await update.message.reply_text(
                    f"✅ *已记录！*\n\n"
                    f"📌 {task_data.get('title', user_text)}\n"
                    f"{due_text}\n"
                    f"⚡ 优先级：{prio_text}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ 记录失败，请重试。")
        except Exception as e:
            logger.error(f"Task extraction failed: {e}, raw: {raw}")
            await update.message.reply_text("⚠️ 无法解析任务，请换个方式描述，例如：'明天要跟进 John 付款'")

    elif "B" in intent:
        # 查询请求
        await update.message.reply_text("🔍 查询中...", parse_mode="Markdown")
        tasks = get_my_tasks(status_filter="pending")
        reply = ask_claude_personal(
            f"今天是 {date.today()}。用户问：\"{user_text}\"\n\n"
            f"根据以下任务数据用中文回答，要具体和直接：\n\n{tasks}"
        )
        await update.message.reply_text(reply, parse_mode="Markdown")

    else:
        # 一般对话
        tasks = get_my_tasks(status_filter="pending")
        reply = ask_claude_personal(
            f"今天是 {date.today()}。用户说：\"{user_text}\"\n\n"
            f"你是用户的个人任务助理，用中文回答。当前任务数据：\n{tasks}"
        )
        await update.message.reply_text(reply, parse_mode="Markdown")


# ─── 定时任务 ────────────────────────────────────────────

async def job_daily_brief(app: Application):
    """每天早上发今日任务清单"""
    logger.info("发送每日任务简报")
    try:
        tasks = get_tasks_due_today()
        all_pending = get_my_tasks(status_filter="pending")

        reply = ask_claude_personal(
            f"今天是 {date.today()}（早上好！）。\n\n"
            f"今日到期任务：\n{tasks}\n\n"
            f"所有待办任务：\n{all_pending}\n\n"
            f"请用中文生成一份简短的早间任务简报：\n"
            f"1) 🔴 今天必须完成的任务（逾期 + 今日到期）\n"
            f"2) 📋 今天可以推进的其他任务\n"
            f"3) 一句激励的话\n"
            f"控制在 200 字内。"
        )
        await app.bot.send_message(
            chat_id=MANAGER_CHAT_ID,
            text=f"🌅 *今日任务简报*\n\n{reply}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"每日简报失败: {e}")


async def job_weekly_summary(app: Application):
    """每周一发本周任务总结"""
    logger.info("发送每周任务总结")
    try:
        tasks = get_tasks_this_week()
        all_tasks = get_my_tasks(status_filter="all")

        reply = ask_claude_personal(
            f"今天是 {date.today()}（周一）。\n\n"
            f"本周任务：\n{tasks}\n\n"
            f"所有任务（含已完成）：\n{all_tasks}\n\n"
            f"请用中文生成每周任务总结：\n"
            f"1) 上周完成了什么\n"
            f"2) 本周需要完成的重点任务\n"
            f"3) 有没有逾期未完成的任务需要处理\n"
            f"4) 本周优先级排序（前3项）"
        )
        await app.bot.send_message(
            chat_id=MANAGER_CHAT_ID,
            text=f"📊 *本周任务总结*\n\n{reply}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"每周总结失败: {e}")


# ─── MAIN ────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    # 每天 9AM 发简报
    scheduler.add_job(
        lambda: asyncio.ensure_future(job_daily_brief(app)),
        "cron", hour=DAILY_BRIEF_HOUR, minute=0
    )
    # 每周一 9AM 发总结
    scheduler.add_job(
        lambda: asyncio.ensure_future(job_weekly_summary(app)),
        "cron", day_of_week="mon", hour=DAILY_BRIEF_HOUR, minute=0
    )

    scheduler.start()
    logger.info("个人任务助理 Bot 启动中...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
