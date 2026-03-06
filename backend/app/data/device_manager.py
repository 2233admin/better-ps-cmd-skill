"""设备管理器 — 5090 CUDA + AMD 核显 DirectML 协同调度

策略:
  - 5090 (CUDA): gpu_factors 实时因子引擎 (独占，最高优先级)
  - AMD iGPU (DirectML): TFT 推理、ONNX 加速计算 (协处理器)
  - CPU: IO 密集型、轻量逻辑

用法:
    from app.data.device_manager import device_mgr
    session = device_mgr.get_onnx_session("model.onnx")  # 自动选核显
    device_mgr.status()  # 查看设备状态
"""

import os
from pathlib import Path
from loguru import logger

# 强制 ONNX Runtime 用 device 1 (AMD iGPU)，不抢 5090
# DirectML device 0 通常是主 GPU (5090)，device 1 是核显
_DML_DEVICE_ID = 1


class DeviceManager:
    """双 GPU 设备管理"""

    def __init__(self):
        self._cuda_available = False
        self._dml_available = False
        self._onnx_sessions: dict[str, object] = {}
        self._init_devices()

    def _init_devices(self):
        # 检测 CUDA (5090)
        try:
            import torch
            if torch.cuda.is_available():
                self._cuda_available = True
                name = torch.cuda.get_device_name(0)
                mem = torch.cuda.get_device_properties(0).total_memory / 1e9
                logger.info(f"[DeviceMgr] CUDA: {name} ({mem:.0f}GB) — 用于实时因子")
        except ImportError:
            pass

        # 检测 DirectML (AMD iGPU)
        try:
            import onnxruntime as ort
            if "DmlExecutionProvider" in ort.get_available_providers():
                self._dml_available = True
                logger.info(f"[DeviceMgr] DirectML: AMD iGPU (device {_DML_DEVICE_ID}) — 用于推理协处理")
        except ImportError:
            pass

        if not self._dml_available:
            logger.info("[DeviceMgr] DirectML 不可用，推理回退到 CPU")

    @property
    def has_cuda(self) -> bool:
        return self._cuda_available

    @property
    def has_dml(self) -> bool:
        return self._dml_available

    def get_onnx_session(self, model_path: str | Path):
        """获取 ONNX 推理会话，优先用核显 DirectML"""
        import onnxruntime as ort

        key = str(model_path)
        if key in self._onnx_sessions:
            return self._onnx_sessions[key]

        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        if self._dml_available:
            providers = [
                ("DmlExecutionProvider", {"device_id": _DML_DEVICE_ID}),
                "CPUExecutionProvider",
            ]
            logger.info(f"[DeviceMgr] ONNX session on AMD iGPU: {Path(model_path).name}")
        else:
            providers = ["CPUExecutionProvider"]
            logger.info(f"[DeviceMgr] ONNX session on CPU: {Path(model_path).name}")

        sess = ort.InferenceSession(str(model_path), providers=providers)
        self._onnx_sessions[key] = sess
        return sess

    def torch_to_onnx(self, model, input_shape: tuple, save_path: str | Path):
        """将 PyTorch 模型导出为 ONNX，方便核显运行"""
        import torch

        model.eval()
        dummy = torch.randn(*input_shape)
        torch.onnx.export(
            model, dummy, str(save_path),
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
            opset_version=17,
        )
        logger.info(f"[DeviceMgr] Exported ONNX: {save_path}")

    def run_on_igpu(self, func, *args, **kwargs):
        """在核显上执行 numpy 计算（通过 ONNX custom op 或直接 DirectML）
        对于简单的 numpy 运算，直接 CPU 更快，这个主要给 ONNX 模型用"""
        return func(*args, **kwargs)

    def status(self) -> dict:
        """设备状态概览"""
        info = {
            "cuda": {"available": self._cuda_available, "role": "实时因子引擎"},
            "directml": {"available": self._dml_available, "role": "推理协处理器",
                         "device_id": _DML_DEVICE_ID},
            "onnx_sessions": list(self._onnx_sessions.keys()),
        }

        if self._cuda_available:
            import torch
            info["cuda"]["device"] = torch.cuda.get_device_name(0)
            info["cuda"]["memory_used_mb"] = round(torch.cuda.memory_allocated() / 1e6, 1)
            info["cuda"]["memory_total_mb"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1e6, 1)

        return info


# 全局单例
device_mgr = DeviceManager()
