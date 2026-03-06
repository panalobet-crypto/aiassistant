"""
agent.py — Claude API for Personal Task Assistant
"""

import logging
import anthropic
from config import CLAUDE_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)
_client = None

SYSTEM_PROMPT = """你是用户的私人任务助理。你的工作是：
1. 帮用户记录任务（从自然语言中提取任务信息）
2. 帮用户查看和整理任务列表
3. 提醒用户逾期或即将到期的任务
4. 每天早上发简报，每周一发总结

规则：
- 始终用中文回复（除非用户用英文问）
- 回复要简洁直接，不废话
- 用 emoji 让内容更易读
- 任务 ID 格式：P001, P002...
- 日期格式：YYYY-MM-DD
- 优先级：🔴 HIGH（紧急）/ 🟡 MED（普通）/ 🟢 LOW（不急）
"""


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    return _client


def ask_claude_personal(prompt: str) -> str:
    try:
        client = _get_client()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
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
