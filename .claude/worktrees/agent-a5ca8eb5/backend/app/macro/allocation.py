"""资产配置追踪 + 收益归因 (参考 Maybe)"""

from datetime import datetime, date

from loguru import logger

from ..data.store import get_store


def update_allocation(
    asset_class: str,
    target_pct: float,
    actual_pct: float,
    value: float = 0,
    notes: str = "",
) -> dict:
    """更新资产配置记录"""
    store = get_store()
    record_date = date.today().isoformat()
    store.conn.execute(
        """INSERT OR REPLACE INTO macro_allocation
           (date, asset_class, target_pct, actual_pct, value, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [record_date, asset_class, target_pct, actual_pct, value, notes],
    )
    return {
        "date": record_date,
        "asset_class": asset_class,
        "target_pct": target_pct,
        "actual_pct": actual_pct,
        "value": value,
    }


def get_current_allocation() -> list[dict]:
    """获取最新资产配置"""
    store = get_store()
    rows = store.conn.execute("""
        SELECT date, asset_class, target_pct, actual_pct, value, notes
        FROM macro_allocation
        WHERE date = (SELECT MAX(date) FROM macro_allocation)
        ORDER BY asset_class
    """).fetchall()
    return [
        {
            "date": r[0], "asset_class": r[1], "target_pct": r[2],
            "actual_pct": r[3], "value": r[4], "notes": r[5],
        }
        for r in rows
    ]


def get_allocation_history(asset_class: str | None = None, limit: int = 30) -> list[dict]:
    """获取配置历史"""
    store = get_store()
    if asset_class:
        rows = store.conn.execute(
            "SELECT date, asset_class, target_pct, actual_pct, value FROM macro_allocation WHERE asset_class = ? ORDER BY date DESC LIMIT ?",
            [asset_class, limit],
        ).fetchall()
    else:
        rows = store.conn.execute(
            "SELECT date, asset_class, target_pct, actual_pct, value FROM macro_allocation ORDER BY date DESC LIMIT ?",
            [limit],
        ).fetchall()
    return [
        {"date": r[0], "asset_class": r[1], "target_pct": r[2], "actual_pct": r[3], "value": r[4]}
        for r in rows
    ]


def calculate_attribution(start_date: str, end_date: str) -> dict:
    """计算收益归因 (简化版 Brinson 模型)"""
    store = get_store()

    # 获取期初期末配置
    start_alloc = store.conn.execute(
        "SELECT asset_class, actual_pct, value FROM macro_allocation WHERE date = ?",
        [start_date],
    ).fetchall()
    end_alloc = store.conn.execute(
        "SELECT asset_class, actual_pct, value FROM macro_allocation WHERE date = ?",
        [end_date],
    ).fetchall()

    if not start_alloc or not end_alloc:
        return {"error": "insufficient_data", "message": "缺少起止日期的配置数据"}

    start_map = {r[0]: {"pct": r[1], "value": r[2]} for r in start_alloc}
    end_map = {r[0]: {"pct": r[1], "value": r[2]} for r in end_alloc}

    total_start = sum(v["value"] for v in start_map.values()) or 1
    total_end = sum(v["value"] for v in end_map.values()) or 1
    total_return = (total_end - total_start) / total_start

    attribution = []
    for asset in set(list(start_map.keys()) + list(end_map.keys())):
        s = start_map.get(asset, {"pct": 0, "value": 0})
        e = end_map.get(asset, {"pct": 0, "value": 0})
        asset_return = (e["value"] - s["value"]) / s["value"] if s["value"] else 0
        weight = s["pct"] / 100
        contribution = weight * asset_return
        attribution.append({
            "asset_class": asset,
            "weight": round(weight, 4),
            "return": round(asset_return, 4),
            "contribution": round(contribution, 4),
        })

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_return": round(total_return, 4),
        "attribution": sorted(attribution, key=lambda x: -abs(x["contribution"])),
    }
