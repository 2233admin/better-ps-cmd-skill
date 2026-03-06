"""宏观快报 API 路由"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/macro", tags=["macro"])


# === Request/Response Models ===

class FetchRequest(BaseModel):
    codes: list[str] | None = None
    category: str | None = None


class KnowledgeAddRequest(BaseModel):
    text: str
    source: str = "manual"
    category: str = "general"
    metadata: dict | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str
    n_results: int = 5
    category: str | None = None


class BriefingRequest(BaseModel):
    focus_category: str | None = None
    custom_prompt: str | None = None


class AllocationRequest(BaseModel):
    asset_class: str
    target_pct: float
    actual_pct: float
    value: float = 0
    notes: str = ""


class AttributionRequest(BaseModel):
    start_date: str
    end_date: str


# === 指标采集 ===

@router.post("/indicators/fetch")
async def fetch_indicators(req: FetchRequest = FetchRequest()):
    from ..macro.indicators import fetch_all_indicators, fetch_by_codes, fetch_by_category
    if req.codes:
        data = fetch_by_codes(req.codes)
    elif req.category:
        data = fetch_by_category(req.category)
    else:
        data = fetch_all_indicators()
    total = sum(len(v) for v in data.values())
    return {"indicators_fetched": len(data), "total_records": total, "codes": list(data.keys())}


@router.get("/indicators")
async def get_indicators(category: str | None = None):
    from ..macro.indicators import get_latest_indicators
    data = get_latest_indicators(category=category)
    return {"data": data, "count": len(data)}


@router.get("/indicators/{code}/history")
async def get_indicator_history(code: str, limit: int = 24):
    from ..macro.indicators import get_indicator_history
    data = get_indicator_history(code, limit)
    return {"code": code, "data": data, "count": len(data)}


@router.get("/indicators/registry")
async def get_registry():
    from ..macro.config import INDICATOR_REGISTRY, CATEGORIES
    return {
        "indicators": [
            {"code": i.code, "name": i.name, "category": i.category,
             "frequency": i.frequency, "unit": i.unit, "description": i.description}
            for i in INDICATOR_REGISTRY
        ],
        "categories": CATEGORIES,
    }


# === 知识库 ===

@router.post("/knowledge")
async def add_knowledge(req: KnowledgeAddRequest):
    from ..macro.knowledge import add_knowledge
    doc_id = add_knowledge(req.text, req.source, req.category, req.metadata)
    return {"doc_id": doc_id, "status": "ok"}


@router.post("/knowledge/search")
async def search_knowledge(req: KnowledgeSearchRequest):
    from ..macro.knowledge import search_knowledge
    results = search_knowledge(req.query, req.n_results, req.category)
    return {"data": results, "count": len(results)}


@router.get("/knowledge/stats")
async def knowledge_stats():
    from ..macro.knowledge import get_knowledge_stats
    return get_knowledge_stats()


# === 快报 ===

@router.post("/briefing/generate")
async def generate_briefing(req: BriefingRequest = BriefingRequest()):
    from ..macro.briefing import generate_briefing
    result = await generate_briefing(req.focus_category, req.custom_prompt)
    return result


@router.get("/briefing/history")
async def briefing_history(limit: int = 10):
    from ..macro.briefing import get_briefing_history
    data = get_briefing_history(limit)
    return {"data": data, "count": len(data)}


# === 资产配置 ===

@router.post("/allocation")
async def update_allocation(req: AllocationRequest):
    from ..macro.allocation import update_allocation
    result = update_allocation(req.asset_class, req.target_pct, req.actual_pct, req.value, req.notes)
    return result


@router.get("/allocation")
async def get_allocation():
    from ..macro.allocation import get_current_allocation
    data = get_current_allocation()
    return {"data": data, "count": len(data)}


@router.get("/allocation/history")
async def allocation_history(asset_class: str | None = None, limit: int = 30):
    from ..macro.allocation import get_allocation_history
    data = get_allocation_history(asset_class, limit)
    return {"data": data, "count": len(data)}


@router.post("/allocation/attribution")
async def attribution(req: AttributionRequest):
    from ..macro.allocation import calculate_attribution
    return calculate_attribution(req.start_date, req.end_date)
