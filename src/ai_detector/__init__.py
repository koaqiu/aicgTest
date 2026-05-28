"""AI图像检测工具包"""
from .detector import AIDetector
from .config import USE_OPENVINO

__version__ = "1.0.0"
__all__ = ["AIDetector", "USE_OPENVINO"]
