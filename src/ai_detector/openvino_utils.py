"""OpenVINO 优化工具模块"""
import torch
import numpy as np
import os
from pathlib import Path

_openvino_available = None
_openvino_device = None
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CACHE_ROOT = _PROJECT_ROOT / ".cache" / "openvino"
_ONNX_CACHE_PATH = _CACHE_ROOT / "dire_reconstructor.onnx"
_OV_BLOB_CACHE_DIR = _CACHE_ROOT / "blob"


def _get_openvino_core_class():
    """兼容 OpenVINO 2024/2026 的 Core 导入路径。"""
    try:
        from openvino.runtime import Core
        return Core
    except Exception:
        try:
            import openvino

            if hasattr(os, "add_dll_directory"):
                ov_base = Path(list(openvino.__path__)[0])
                for dll_dir in (ov_base / "libs", ov_base / "lib"):
                    if dll_dir.exists():
                        os.add_dll_directory(str(dll_dir))

            from openvino._ov_api import Core
            return Core
        except Exception as e:
            raise ImportError(f"无法导入 OpenVINO Core: {e}") from e


def check_openvino_status(preferred_device: str | None = None):
    """检查OpenVINO状态并返回可用设备和原因。"""
    global _openvino_available, _openvino_device

    try:
        Core = _get_openvino_core_class()
        core = Core()

        available_devices = core.available_devices
        print(f"[OpenVINO] 可用设备: {available_devices}")

        if preferred_device:
            target = preferred_device.upper()
            if any(device.startswith(target) for device in available_devices):
                _openvino_device = target
                _openvino_available = True
                print(f"[OpenVINO] 将使用 {target} 加速")
                return True, target
            print(f"[OpenVINO] 请求设备不可用: {target}")
            _openvino_available = False
            _openvino_device = None
            return False, None

        if any(device.startswith("GPU") for device in available_devices):
            _openvino_device = "GPU"
            _openvino_available = True
            print(f"[OpenVINO] 将使用 GPU 加速")
            return True, "GPU"
        elif any(device.startswith("NPU") for device in available_devices):
            _openvino_device = "NPU"
            _openvino_available = True
            print(f"[OpenVINO] 将使用 NPU 加速")
            return True, "NPU"
        elif any(device.startswith("CPU") for device in available_devices):
            _openvino_device = "CPU"
            _openvino_available = True
            print(f"[OpenVINO] 将使用 CPU 加速")
            return True, "CPU"
        else:
            _openvino_available = False
            _openvino_device = None
            print(f"[OpenVINO] 未找到可用的加速设备")
            return False, None

    except ImportError:
        print("[OpenVINO] 未安装 OpenVINO")
        _openvino_available = False
        _openvino_device = None
        return False, None
    except Exception as e:
        print(f"[OpenVINO] 初始化失败: {e}")
        _openvino_available = False
        _openvino_device = None
        return False, None


def export_to_onnx(model, output_path="temp_model.onnx", input_shape=(1, 3, 256, 256)):
    """
    将PyTorch模型导出为ONNX格式

    Args:
        model: PyTorch模型
        output_path: 输出路径
        input_shape: 输入形状
    """
    model.eval()
    dummy_input = torch.randn(input_shape)

    torch.onnx.export(
        model,
        dummy_input,
        output_path,
        export_params=True,
        # torch>=2.6 默认导出路径常会先生成较新opset；直接对齐到18可避免降级转换失败
        opset_version=18,
        # 该模型在 dynamo=True 的新导出器下会生成 OpenVINO 不兼容的 Reshape 图
        dynamo=False,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['output']
    )
    return output_path


def create_openvino_inference_engine(model, device_name="GPU"):
    """
    创建OpenVINO推理引擎

    Args:
        model: PyTorch模型
        device_name: 设备名称

    Returns:
        OpenVINO编译后的模型和推理请求
    """
    Core = _get_openvino_core_class()

    requested_device = device_name.upper() if device_name else None
    is_available, preferred_device = check_openvino_status(preferred_device=requested_device)
    if not is_available:
        if requested_device:
            raise RuntimeError(f"OpenVINO 设备不可用: {requested_device}")
        raise RuntimeError("OpenVINO 不可用")

    actual_device = device_name or preferred_device

    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    _OV_BLOB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not _ONNX_CACHE_PATH.exists():
        print(f"[OpenVINO] 首次运行，创建 ONNX 缓存: {_ONNX_CACHE_PATH}")
        export_to_onnx(model, str(_ONNX_CACHE_PATH))
    else:
        print(f"[OpenVINO] 使用 ONNX 缓存: {_ONNX_CACHE_PATH}")

    core = Core()
    core.set_property({"CACHE_DIR": str(_OV_BLOB_CACHE_DIR)})
    ov_model = core.read_model(str(_ONNX_CACHE_PATH))
    compiled_model = core.compile_model(ov_model, actual_device)
    infer_request = compiled_model.create_infer_request()

    return infer_request, compiled_model


def run_openvino_inference(infer_request, input_tensor):
    """
    运行OpenVINO推理

    Args:
        infer_request: OpenVINO推理请求
        input_tensor: 输入张量 (numpy array 或 torch tensor)

    Returns:
        输出结果 (numpy array)
    """
    if isinstance(input_tensor, torch.Tensor):
        input_np = input_tensor.cpu().numpy()
    else:
        input_np = input_tensor

    result = infer_request.infer(inputs={0: input_np})
    return next(iter(result.values()))
