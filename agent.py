"""
agent.py — Claude API for Personal Task Assistant v6
带记忆的智能助理
"""

import logging
import anthropic
from config import CLAUDE_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)
_client = None

BASE_SYSTEM_PROMPT = """你是 HY Kee 的私人任务助理。

用户背景：
- 管理多个数字营销团队（SEO、社交媒体、运营）
- 主要品牌：Panalobet（菲律宾）、PBC88（孟加拉）、PBV88/MVPVIVA（越南）
- 常见团队成员：Suman（SEO）、Trisha（菲律宾社媒）、Gopi（孟加拉）、Kanhana（越南）、Jovan（工程师）、Michael（设计）

任务分类：
- SEO = 关键词、排名、网站、内容、GSC、域名、外链、T1/T2/T3、PBN
- Social = Facebook、IG、TikTok、Telegram、YouTube、WhatsApp、EDM、KPI、粉丝
- Ops = 服务器、付款、发票、报告、会议、客户、续费、团队管理
- Personal = 私人、家庭、银行、健康、旅行

规则：
- 跟着用户的语言走（中文说中文，英文说英文）
- 回复简洁直接，用 emoji
- 主动发现问题和冲突，不只是被动记录
- 优先级：🔴 HIGH / 🟡 MED / 🟢 LOW
"""


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    return _client


def build_system_prompt_with_memory(memories: list) -> str:
    """把记忆注入 system prompt"""
    if not memories:
        return BASE_SYSTEM_PROMPT

    mem_lines = []
    type_labels = {
        "habit":   "📌 用户习惯",
        "pattern": "📊 观察到的规律",
        "insight": "💡 建议",
        "user":    "✏️ 用户告知",
    }
    for m in memories[-20:]:  # 最近20条记忆
        label = type_labels.get(str(m.get("Type","")), "📝")
        mem_lines.append(f"  {label}：{m.get('Content','')}")

    memory_section = "\n【你对这个用户的了解】\n" + "\n".join(mem_lines)
    return BASE_SYSTEM_PROMPT + memory_section


def ask_claude_personal(prompt: str, memories: list = None) -> str:
    try:
        client = _get_client()
        system = build_system_prompt_with_memory(memories or [])
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except anthropic.APIStatusError as e:
        if "credit" in str(e).lower():
            return "⚠️ Claude API 余额不足，请前往 console.anthropic.com 充值。"
        logger.error(f"Claude API error: {e}")
        return "⚠️ AI 暂时无法回复，请稍后再试。"
    except Exception as e:
        logger.error(f"ask_claude_personal error: {e}")
        return "⚠️ 出错了，请稍后再试。"


def analyze_task_conflicts(new_task: dict, all_pending: list, memories: list) -> str:
    """分析新任务是否有冲突，返回建议（没有冲突返回空字符串）"""
    try:
        import json
        assignee = new_task.get("assignee", "Me")
        due      = new_task.get("due", "")
        priority = new_task.get("priority", "MED")

        # 检查负责人负载
        person_tasks = [t for t in all_pending
                        if assignee and assignee.lower() in str(t.get("Assignee","")).lower()]

        # 检查同一天 HIGH 任务
        same_day_high = [t for t in all_pending
                         if due and str(t.get("Due Date","")).strip() == due
                         and str(t.get("Priority","")).strip() == "HIGH"] if priority == "HIGH" else []

        if len(person_tasks) >= 4 or same_day_high:
            prompt = f"""分析以下新任务的潜在冲突：

新任务：{json.dumps(new_task, ensure_ascii=False)}
{assignee} 当前待办：{len(person_tasks)} 项
同一天的HIGH任务：{len(same_day_high)} 项

用一句话给出简短建议（如果没有问题就回复"OK"）。"""
            result = ask_claude_personal(prompt, memories)
            return "" if result.strip() == "OK" else result
        return ""
    except Exception as e:
        logger.error(f"analyze_task_conflicts error: {e}")
        return ""
