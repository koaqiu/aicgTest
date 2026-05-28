"""频域检测模块 (FFT)"""
import cv2
import numpy as np

from .config import THRESHOLD_FFT, MAX_ANALYSIS_SIDE
from .image_io import read_image


def detect_fft(img_path: str) -> tuple[bool, float]:
    """
    使用频域特征检测AI生成图像
    原理：AI图在频域有规律的高频能量，真实图更随机

    Args:
        img_path: 图片路径

    Returns:
        (是否疑似AI, 频域特征值)
    """
    img = read_image(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False, 0.0

    h, w = img.shape
    longest = max(h, w)
    if longest > MAX_ANALYSIS_SIDE:
        scale = MAX_ANALYSIS_SIDE / float(longest)
        new_w = max(64, int(w * scale))
        new_h = max(64, int(h * scale))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    h, w = img.shape

    # 傅里叶变换
    fft = np.fft.fft2(img)
    fft_shift = np.fft.fftshift(fft)
    magnitude = np.log1p(np.abs(fft_shift))

    # 提取中心高频区域
    center_h, center_w = h // 2, w // 2
    band = max(12, min(50, min(h, w) // 8))
    high_freq = magnitude[center_h - band:center_h + band, center_w - band:center_w + band]
    freq_score = float(np.mean(high_freq))

    return freq_score > THRESHOLD_FFT, round(freq_score, 2)
