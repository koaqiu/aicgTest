"""元数据检测模块"""
from PIL import Image

# 常见扩散模型工作流会在 PNG 文本中留下这些字段/关键词。
_METADATA_KEYS = {
    "parameters",
    "prompt",
    "negative_prompt",
    "workflow",
    "sd-metadata",
}

_METADATA_HINTS = (
    "stable diffusion",
    "automatic1111",
    "a1111",
    "comfyui",
    "webui",
    "sdxl",
    "lora",
    "negative prompt",
    "steps:",
    "sampler:",
    "cfg scale:",
    "seed:",
)


def detect_metadata_ai_trace(img_path: str) -> tuple[bool, float, str]:
    """检测图片元数据中的 AI 生成痕迹。"""
    try:
        img = Image.open(img_path)
    except Exception:
        return False, 0.0, "读取失败"

    info = img.info or {}
    if not info:
        return False, 0.0, "无元数据"

    normalized = {str(k).strip().lower(): v for k, v in info.items()}
    for key in _METADATA_KEYS:
        if key in normalized:
            value = str(normalized[key]).lower()
            if key == "parameters":
                return True, 1.0, "命中 parameters 字段"
            if any(hint in value for hint in _METADATA_HINTS):
                return True, 0.95, f"命中字段 {key} 的生成参数"
            return True, 0.8, f"命中字段 {key}"

    text_blob = "\n".join(f"{k}:{v}" for k, v in normalized.items()).lower()
    if any(hint in text_blob for hint in _METADATA_HINTS):
        return True, 0.85, "命中生成器关键词"

    return False, 0.0, "无AI元数据痕迹"
