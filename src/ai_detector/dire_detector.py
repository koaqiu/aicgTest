"""DIRE重建检测模块"""
import torch
import numpy as np
from PIL import Image
from torchvision import transforms

from .config import DEVICE, THRESHOLD_DIRE, IMAGE_SIZE, USE_OPENVINO, MAX_ANALYSIS_SIDE
from .models.reconstructor import SimpleDDIMReconstructor
from .openvino_utils import check_openvino_status, create_openvino_inference_engine, run_openvino_inference

_model = None
_infer_request = None
_compiled_model = None
_use_openvino = USE_OPENVINO
_openvino_device = None
_runtime_mode = "auto"


def set_runtime_mode(mode: str = "auto"):
    """设置运行设备模式：auto/gpu/cpu。"""
    global _runtime_mode
    normalized = (mode or "auto").strip().lower()
    if normalized not in {"auto", "gpu", "cpu"}:
        raise ValueError(f"不支持的设备模式: {mode}")
    _runtime_mode = normalized


def _get_torch_device() -> torch.device:
    if _runtime_mode == "cpu":
        return torch.device("cpu")
    if _runtime_mode == "gpu" and torch.cuda.is_available():
        return torch.device("cuda")
    return DEVICE


def _init_acceleration():
    """初始化加速引擎（自动检测OpenVINO或CUDA）"""
    global _use_openvino, _openvino_device

    _use_openvino = USE_OPENVINO
    _openvino_device = None

    if _runtime_mode == "cpu":
        _use_openvino = False
        print("[DIRE] 设备模式: CPU（已禁用 GPU/OpenVINO）")
        return True

    if _use_openvino:
        preferred = "GPU" if _runtime_mode == "gpu" else None
        is_available, device = check_openvino_status(preferred_device=preferred)
        if is_available:
            _openvino_device = device
            print(f"[DIRE] 使用 {device} 加速")
            return True
        else:
            if _runtime_mode == "gpu":
                print("[DIRE] OpenVINO GPU 不可用，尝试 CUDA")
            else:
                print("[DIRE] OpenVINO 不可用，fallback 到 PyTorch")
            _use_openvino = False

    if torch.cuda.is_available():
        if _runtime_mode in {"auto", "gpu"}:
            print(f"[DIRE] 使用 CUDA 加速")
            return True

    if _runtime_mode == "gpu":
        print("[DIRE] GPU 不可用，fallback 到 CPU")
        return True

    print("[DIRE] 使用 CPU")
    return True


def _get_model():
    """获取或初始化模型（单例模式）"""
    global _model, _infer_request, _compiled_model, _use_openvino, _openvino_device

    if _model is None:
        _model = SimpleDDIMReconstructor()
        _init_acceleration()

        if _use_openvino and _openvino_device:
            try:
                _infer_request, _compiled_model = create_openvino_inference_engine(
                    _model, device_name=_openvino_device
                )
                print(f"[DIRE] OpenVINO 推理引擎初始化成功 (设备: {_openvino_device})")
            except Exception as e:
                print(f"[DIRE] OpenVINO 初始化失败: {e}，fallback 到 PyTorch")
                _model = _model.to(_get_torch_device()).eval()
                _use_openvino = False
                _infer_request = None
                _compiled_model = None
        else:
            _model = _model.to(_get_torch_device()).eval()

    return _model


def detect_dire(img_path: str, use_openvino: bool = None) -> tuple[bool, float]:
    """
    使用DIRE（扩散重建误差）检测AI生成图像
    原理：SD生成图容易被重建（误差小），真实图难重建（误差大）

    Args:
        img_path: 图片路径
        use_openvino: 是否使用OpenVINO加速（None=自动检测）

    Returns:
        (是否疑似AI, 重建误差值)
    """
    transform = transforms.Compose([
        transforms.Resize(IMAGE_SIZE),
        transforms.ToTensor(),
        transforms.Normalize([0.5] * 3, [0.5] * 3)
    ])

    try:
        img = Image.open(img_path)
        if hasattr(img, "draft"):
            img.draft("RGB", (MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE))
        img = img.convert("RGB")
        img.thumbnail((MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE), Image.Resampling.BILINEAR)
        img_tensor = transform(img).unsqueeze(0)
    except Exception:
        return False, 1.0

    model = _get_model()

    effective_use_openvino = _use_openvino if use_openvino is None else use_openvino

    if effective_use_openvino and _infer_request is not None:
        try:
            recon_np = run_openvino_inference(_infer_request, img_tensor.numpy())
            recon_tensor = torch.from_numpy(recon_np)
        except Exception:
            img_tensor = img_tensor.to(_get_torch_device())
            with torch.no_grad():
                recon_tensor = model(img_tensor)
    else:
        img_tensor = img_tensor.to(_get_torch_device())
        with torch.no_grad():
            recon_tensor = model(img_tensor)

    mse_loss = float(torch.mean((img_tensor - recon_tensor) ** 2).item())
    return mse_loss < THRESHOLD_DIRE, round(mse_loss, 4)


def cleanup():
    """清理模型内存"""
    global _model, _infer_request, _compiled_model
    if _compiled_model is not None:
        del _compiled_model
        _compiled_model = None
    if _model is not None:
        del _model
        _model = None
    _infer_request = None
    torch.cuda.empty_cache()
