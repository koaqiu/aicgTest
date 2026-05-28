"""配置参数模块"""
import os
from pathlib import Path
import torch

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def _check_openvino_available():
    """检查OpenVINO是否可用且支持GPU"""
    try:
        try:
            from openvino.runtime import Core
        except Exception:
            import openvino

            if hasattr(os, "add_dll_directory"):
                ov_base = Path(list(openvino.__path__)[0])
                for dll_dir in (ov_base / "libs", ov_base / "lib"):
                    if dll_dir.exists():
                        os.add_dll_directory(str(dll_dir))

            from openvino._ov_api import Core

        core = Core()
        devices = core.available_devices
        if any(device.startswith("GPU") for device in devices):
            return True
        if any(device.startswith("NPU") for device in devices):
            return True
        return "GPU" in str(devices) or len(devices) > 1
    except Exception:
        return False

USE_OPENVINO = _check_openvino_available()

THRESHOLD_FFT = 15.0
THRESHOLD_DIRE = 0.08
THRESHOLD_FINAL = 0.6
IMAGE_SIZE = (256, 256)

# 大图安全分析上限：避免 10k+ 分辨率导致内存峰值爆炸。
MAX_ANALYSIS_SIDE = 1536
MAX_ELA_SIDE = 1024
