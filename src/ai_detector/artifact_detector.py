"""单图取证检测模块（不依赖外部参考图）。"""
import io
import math

import cv2
import numpy as np
from PIL import Image

from .config import MAX_ANALYSIS_SIDE, MAX_ELA_SIDE
from .image_io import read_image


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _resize_if_needed(img: np.ndarray, max_side: int) -> np.ndarray:
    """将图片限制到指定最长边，控制分析内存。"""
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return img

    scale = max_side / float(longest)
    new_w = max(64, int(w * scale))
    new_h = max(64, int(h * scale))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _safe_corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    """安全计算相关系数，避免常量通道触发 NaN。"""
    if a.size == 0 or b.size == 0:
        return 0.0

    std_a = float(np.std(a))
    std_b = float(np.std(b))
    if std_a < 1e-8 or std_b < 1e-8:
        return 0.0

    corr = float(np.corrcoef(a, b)[0, 1])
    if not np.isfinite(corr):
        return 0.0
    return corr


def _ela_mean(path: str, quality: int = 90) -> float:
    """计算误差等级分析（ELA）均值。"""
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return 0.0

    img.thumbnail((MAX_ELA_SIDE, MAX_ELA_SIDE), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    rec = Image.open(buf).convert("RGB")

    a = np.asarray(img, dtype=np.float32)
    b = np.asarray(rec, dtype=np.float32)
    diff = np.abs(a - b)
    return float(diff.mean())


def detect_artifact_signature(img_path: str) -> tuple[bool, float, str]:
    """基于单图统计特征给出 AI 痕迹置信度。"""
    img = read_image(img_path, cv2.IMREAD_COLOR)
    if img is None:
        return False, 0.0, "读取失败"

    img = _resize_if_needed(img, MAX_ANALYSIS_SIDE)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    grad_p95 = float(np.percentile(grad, 95))

    b = img[:, :, 0].astype(np.float32).ravel()
    g = img[:, :, 1].astype(np.float32).ravel()
    r = img[:, :, 2].astype(np.float32).ravel()

    corr_bg = _safe_corrcoef(b, g)
    corr_gr = _safe_corrcoef(g, r)
    min_corr = min(corr_bg, corr_gr)

    ela_mean = _ela_mean(img_path)

    # 估计 JPEG/视频重编码后的块效应强度。值越高，说明 8x8 边界越明显。
    gray_f = gray.astype(np.float32)
    h, w = gray_f.shape[:2]
    if h >= 16 and w >= 16:
        v_boundaries = gray_f[:, 8::8]
        v_inner = gray_f[:, 4::8]
        h_boundaries = gray_f[8::8, :]
        h_inner = gray_f[4::8, :]
        if v_boundaries.size and v_inner.size:
            cols = min(v_boundaries.shape[1], v_inner.shape[1])
            blockiness_v = float(np.mean(np.abs(v_boundaries[:, :cols] - v_inner[:, :cols])))
        else:
            blockiness_v = 0.0
        if h_boundaries.size and h_inner.size:
            rows = min(h_boundaries.shape[0], h_inner.shape[0])
            blockiness_h = float(np.mean(np.abs(h_boundaries[:rows, :] - h_inner[:rows, :])))
        else:
            blockiness_h = 0.0
        blockiness = (blockiness_v + blockiness_h) / 2.0
    else:
        blockiness = 0.0

    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    prob = hist / max(float(hist.sum()), 1.0)
    entropy = float(-(prob[prob > 0] * np.log2(prob[prob > 0])).sum())
    saturation_ratio = float(((gray <= 5) | (gray >= 250)).mean())

    smooth_conf = _sigmoid((95.0 - grad_p95) / 28.0)
    corr_conf = _sigmoid((min_corr - 0.97) / 0.03)
    entropy_conf = _sigmoid((entropy - 6.7) / 0.45)
    ela_low = _sigmoid((ela_mean - 0.35) / 0.2)
    ela_high = _sigmoid((2.0 - ela_mean) / 0.3)
    ela_band_conf = ela_low * ela_high
    blockiness_conf = _sigmoid((blockiness - 14.0) / 4.5)
    sat_penalty = _sigmoid((saturation_ratio - 0.35) / 0.08)

    confidence = (
        0.26 * smooth_conf
        + 0.18 * corr_conf
        + 0.22 * entropy_conf
        + 0.14 * ela_band_conf
        + 0.12 * blockiness_conf
        + 0.08 * (1.0 - sat_penalty)
    )
    if not np.isfinite(confidence):
        confidence = 0.0
    confidence = round(float(min(max(confidence, 0.0), 1.0)), 4)

    reason = (
        f"grad95={grad_p95:.2f}, minCorr={min_corr:.3f}, ELA={ela_mean:.2f}, "
        f"entropy={entropy:.3f}, sat={saturation_ratio:.3f}, blockiness={blockiness:.2f}"
    )
    return confidence >= 0.6, confidence, reason
