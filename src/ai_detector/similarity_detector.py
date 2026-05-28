"""相似度检测模块"""
import os
from pathlib import Path

import cv2
import numpy as np

from .metadata_detector import detect_metadata_ai_trace
from .image_io import read_image

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_ROOT_MARKERS = ("requirements.txt", "pyproject.toml", ".git")
_SKIP_DIRS = {".git", ".venv", "__pycache__", ".cache", "node_modules"}
_MAX_SCAN_IMAGES = 1500


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS


def _phash_bits(path: Path) -> np.ndarray | None:
    img = read_image(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None

    img = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(img))[:8, :8]
    med = float(np.median(dct[1:, :]))
    bits = (dct > med).astype(np.uint8).flatten()
    return bits


def _hamming_distance(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


def _find_project_root(start_dir: Path) -> Path:
    """向上查找项目根目录，用于目录缺样本时的回退检索。"""
    current = start_dir.resolve()
    for candidate in [current, *current.parents]:
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return current


def _iter_directory_images(target_path: Path):
    """迭代目标图片同目录的候选图片。"""
    for candidate in target_path.parent.iterdir():
        if candidate.resolve() == target_path:
            continue
        if not _is_image(candidate):
            continue
        yield candidate


def _iter_project_images(project_root: Path, target_path: Path):
    """在项目范围迭代候选图片（跳过常见大目录）。"""
    count = 0
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        root_path = Path(root)

        for name in files:
            candidate = root_path / name
            if candidate.resolve() == target_path:
                continue
            if not _is_image(candidate):
                continue

            yield candidate
            count += 1
            if count >= _MAX_SCAN_IMAGES:
                return


def _collect_candidates(target_path: Path):
    """收集候选图片，先同目录，若没有则回退到项目范围。"""
    directory_candidates = list(_iter_directory_images(target_path))
    if directory_candidates:
        return directory_candidates, "同目录"

    project_root = _find_project_root(target_path.parent)
    project_candidates = list(_iter_project_images(project_root, target_path))
    return project_candidates, "项目范围"


def _display_candidate(target_path: Path, candidate: Path, scope: str) -> str:
    """生成用于日志展示的候选名称。"""
    if scope == "同目录":
        return candidate.name

    root = _find_project_root(target_path.parent)
    try:
        return str(candidate.resolve().relative_to(root)).replace("\\", "/")
    except Exception:
        return candidate.name


def _distance_to_confidence(distance: int, max_distance: int) -> float:
    """将 pHash 距离映射到 [0, 1] 相似置信度。"""
    if max_distance <= 0:
        return 0.0
    if distance > max_distance:
        return 0.0
    # 距离越小，置信度越高。
    return round(0.7 + (max_distance - distance) / max_distance * 0.28, 4)


def detect_near_duplicate(img_path: str, max_distance: int = 10) -> tuple[bool, float, str]:
    """检测当前图是否与候选范围中的任意图片高度相似。"""
    target_path = Path(img_path).resolve()
    if not target_path.exists() or not _is_image(target_path):
        return False, 0.0, "图片无效"

    target_hash = _phash_bits(target_path)
    if target_hash is None:
        return False, 0.0, "无法计算感知哈希"

    min_dist = 10**9
    min_name = ""
    candidates, scope = _collect_candidates(target_path)
    if not candidates:
        return False, 0.0, "目录中无可比对图片"

    for candidate in candidates:
        cand_hash = _phash_bits(candidate)
        if cand_hash is None:
            continue

        dist = _hamming_distance(target_hash, cand_hash)
        if dist < min_dist:
            min_dist = dist
            min_name = _display_candidate(target_path, candidate, scope)

    if min_dist == 10**9:
        return False, 0.0, f"{scope}无可比对图片"

    if min_dist <= max_distance:
        confidence = _distance_to_confidence(min_dist, max_distance)
        return True, confidence, f"与 {min_name} 高相似（{scope}，pHash距离={min_dist}）"

    return False, 0.0, f"最近图片距离较大（{scope}，pHash距离={min_dist}）"


def detect_similarity_to_ai_reference(img_path: str, max_distance: int = 10) -> tuple[bool, float, str]:
    """检测当前图是否与带 AI 元数据痕迹的参考图高度相似。"""
    target_path = Path(img_path).resolve()
    if not target_path.exists() or not _is_image(target_path):
        return False, 0.0, "图片无效"

    target_hash = _phash_bits(target_path)
    if target_hash is None:
        return False, 0.0, "无法计算感知哈希"

    min_dist = 10**9
    min_name = ""
    candidates, scope = _collect_candidates(target_path)
    if not candidates:
        return False, 0.0, "目录中无AI参考图"

    for candidate in candidates:
        meta_hit, _, _ = detect_metadata_ai_trace(str(candidate))
        if not meta_hit:
            continue

        cand_hash = _phash_bits(candidate)
        if cand_hash is None:
            continue

        dist = _hamming_distance(target_hash, cand_hash)
        if dist < min_dist:
            min_dist = dist
            min_name = _display_candidate(target_path, candidate, scope)

    if min_dist == 10**9:
        return False, 0.0, f"{scope}无AI参考图"

    if min_dist <= max_distance:
        confidence = _distance_to_confidence(min_dist, max_distance)
        return True, confidence, f"与 {min_name} 高相似（{scope}，pHash距离={min_dist}）"

    return False, 0.0, f"与最近AI参考图距离较大（{scope}，pHash距离={min_dist}）"
