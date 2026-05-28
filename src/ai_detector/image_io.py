"""图像读取工具，兼容 Windows 中文路径。"""
from pathlib import Path

import cv2
import numpy as np


def read_image(path: str, flags: int = cv2.IMREAD_COLOR):
    """使用 imdecode 读取图片，避免 cv2.imread 的 Unicode 路径问题。"""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return None

    try:
        data = np.fromfile(str(file_path), dtype=np.uint8)
    except Exception:
        return None

    if data.size == 0:
        return None

    try:
        return cv2.imdecode(data, flags)
    except Exception:
        return None
