"""AI 预测 API"""

from fastapi import APIRouter

from ..ai.predict import get_predictor

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/predict/{code}")
async def predict(code: str):
    """AI价格方向预测"""
    predictor = get_predictor()
    result = predictor.predict(code)
    return result


@router.get("/factors/{code}")
async def get_factors(code: str):
    """获取因子数据"""
    predictor = get_predictor()
    factors = predictor.get_factors(code)
    return {"data": factors, "code": code}


@router.get("/model/status")
async def model_status():
    """获取模型状态"""
    predictor = get_predictor()
    return predictor.get_status()


@router.get("/kronos/status")
async def kronos_status():
    """Kronos 模型状态"""
    from ..ai.kronos_predictor import get_kronos
    return get_kronos().status()


@router.get("/kronos/predict/{pair}")
async def kronos_predict(pair: str):
    """Kronos K线预测"""
    import asyncio
    from ..ai.kronos_predictor import get_kronos

    def _predict():
        k = get_kronos()
        return k.predict_pair(pair, bar="4H", lookback=400, pred_len=12)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _predict)


@router.get("/devices")
async def device_status():
    """查看双 GPU 设备状态"""
    from ..data.device_manager import device_mgr
    return device_mgr.status()


@router.post("/model/export-onnx")
async def export_onnx():
    """将 PyTorch 模型导出为 ONNX，启用核显推理"""
    predictor = get_predictor()
    ok = predictor.export_to_onnx()
    return {"success": ok, "message": "下次启动将自动用 AMD 核显推理" if ok else "无 PyTorch 模型可导出"}
