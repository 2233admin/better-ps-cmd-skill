"""LLM 快报生成 - 通过 clawapi-manager 调用 LLM"""

from datetime import datetime

import httpx
from loguru import logger

from .indicators import get_latest_indicators
from .knowledge import search_knowledge
from ..data.store import get_store

# clawapi-manager / OpenClaw 代理端点
LLM_ENDPOINT = "http://127.0.0.1:18789/v1/chat/completions"
LLM_MODEL = "volcengine/doubao-seed-2.0-code"  # medium 级，控成本


BRIEFING_PROMPT = """你是一位资深宏观分析师。根据以下最新宏观指标数据和相关知识，生成一份简洁的宏观快报。

## 最新指标数据
{indicators}

## 相关知识背景
{knowledge}

## 要求
1. 用 3-5 段话概述当前宏观环境
2. 分析各指标间的关联和趋势
3. 给出对A股市场的影响判断（利多/利空/中性）
4. 给出资产配置建议（股票/债券/现金/商品 各占比建议）
5. 语言简洁专业，适合交易员快速阅读

请用中文输出。"""


async def generate_briefing(
    focus_category: str | None = None,
    custom_prompt: str | None = None,
) -> dict:
    """生成宏观快报"""
    # 获取最新指标
    indicators = get_latest_indicators(category=focus_category)
    if not indicators:
        return {"error": "no_data", "message": "暂无宏观指标数据，请先执行采集"}

    # 格式化指标
    indicator_text = "\n".join(
        f"- {ind['name']}: {ind['value']}{ind['unit']} ({ind['date']})"
        for ind in indicators
    )

    # 搜索相关知识
    knowledge_items = search_knowledge("宏观经济 货币政策 市场趋势", n_results=3)
    knowledge_text = "\n".join(
        f"- [{k['source']}] {k['text'][:200]}" for k in knowledge_items
    ) or "暂无相关知识"

    prompt = (custom_prompt or BRIEFING_PROMPT).format(
        indicators=indicator_text,
        knowledge=knowledge_text,
    )

    # 调用 LLM
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                LLM_ENDPOINT,
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        # 降级: 生成简单统计摘要
        content = _fallback_briefing(indicators)

    # 保存快报
    briefing = {
        "content": content,
        "generated_at": datetime.now().isoformat(),
        "indicator_count": len(indicators),
        "model": LLM_MODEL,
        "focus": focus_category or "all",
    }
    _save_briefing(briefing)
    return briefing


def _fallback_briefing(indicators: list[dict]) -> str:
    """降级: 无 LLM 时生成简单摘要"""
    lines = ["## 宏观指标速览\n"]
    by_cat: dict[str, list] = {}
    for ind in indicators:
        by_cat.setdefault(ind["category"], []).append(ind)

    cat_names = {
        "monetary": "货币政策", "price": "价格指标",
        "growth": "经济增长", "trade": "贸易", "sentiment": "市场情绪",
    }
    for cat, items in by_cat.items():
        lines.append(f"\n### {cat_names.get(cat, cat)}")
        for item in items:
            lines.append(f"- {item['name']}: {item['value']}{item['unit']} ({item['date']})")

    lines.append("\n> 注: LLM 不可用，以上为原始数据摘要")
    return "\n".join(lines)


def _save_briefing(briefing: dict):
    """保存快报到 DuckDB"""
    store = get_store()
    store.conn.execute(
        """INSERT INTO macro_briefings (content, generated_at, indicator_count, model, focus)
           VALUES (?, ?, ?, ?, ?)""",
        [briefing["content"], briefing["generated_at"],
         briefing["indicator_count"], briefing["model"], briefing["focus"]],
    )


def get_briefing_history(limit: int = 10) -> list[dict]:
    """获取历史快报"""
    store = get_store()
    rows = store.conn.execute(
        "SELECT content, generated_at, indicator_count, model, focus FROM macro_briefings ORDER BY generated_at DESC LIMIT ?",
        [limit],
    ).fetchall()
    return [
        {"content": r[0], "generated_at": r[1], "indicator_count": r[2], "model": r[3], "focus": r[4]}
        for r in rows
    ]
