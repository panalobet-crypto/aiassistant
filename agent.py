"""
agent.py — Claude API for Personal Task Assistant
"""

import logging
import anthropic
from config import CLAUDE_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)
_client = None

SYSTEM_PROMPT = """你是 HY Kee 的私人任务助理。

用户背景：
- 管理多个数字营销团队（SEO、社交媒体、运营）
- 主要品牌：Panalobet、PBC88
- 市场：菲律宾、孟加拉、越南
- 常见团队成员：Suman（SEO负责人）、Trisha（菲律宾社媒）、Gopi（孟加拉）、Kanhana（越南）、Jovan（工程师）、Michael（设计）

任务分类：
- SEO = 关键词、排名、网站、内容、GSC、域名、外链、T1/T2/T3、PBN
- Social = Facebook、IG、TikTok、Telegram、YouTube、WhatsApp、EDM、KPI、粉丝、帖子
- Ops = 服务器、付款、发票、报告、会议、客户、续费、团队管理、招聘
- Personal = 私人、家庭、银行、健康、旅行、个人事务

你的工作：
1. 从自然语言提取任务信息
2. 回答任务查询
3. 分析优先级和冲突
4. 提供任务管理建议

规则：
- 跟着用户的语言走（中文说中文，英文说英文，混合也可以）
- 回复简洁直接，用 emoji
- 任务 ID 格式：P001, P002...
- 优先级：🔴 HIGH（今天/紧急）/ 🟡 MED（本周）/ 🟢 LOW（不急）
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
