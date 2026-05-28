"""综合AI图像检测主模块"""
import math
import re
from dataclasses import dataclass

from .fft_detector import detect_fft
from .dire_detector import detect_dire, set_runtime_mode
from .metadata_detector import detect_metadata_ai_trace
from .artifact_detector import detect_artifact_signature
from .config import THRESHOLD_FINAL, THRESHOLD_DIRE


@dataclass
class DetectionSignals:
    """检测器原始信号与归一化置信度。"""
    freq_score: float
    dire_score: float
    fft_confidence: float
    dire_confidence: float
    meta_confidence: float
    meta_hit: bool
    meta_reason: str
    artifact_confidence: float
    artifact_hit: bool
    artifact_reason: str
    artifact_grad95: float
    artifact_min_corr: float
    artifact_entropy: float
    artifact_sat: float
    graphic_penalty: float


class AIDetector:
    """AI图像检测器"""

    def __init__(self, device_mode: str = "auto"):
        set_runtime_mode(device_mode)

    def detect(self, img_path: str, verbose: bool = True) -> tuple[str, float]:
        details = self.detect_detailed(img_path, verbose=verbose)
        return details["result"], details["confidence"]

    def detect_detailed(self, img_path: str, verbose: bool = True) -> dict:
        """
        检测图像是否为AI生成

        Args:
            img_path: 图片路径
            verbose: 是否打印详细信息

        Returns:
            包含结果、置信度与标签的详细信息字典
        """
        if verbose:
            print("=" * 50)
            print(f"正在检测图片：{img_path}")
            print("=" * 50)

        _, freq_score = detect_fft(img_path)
        _, dire_score = detect_dire(img_path)
        meta_hit, meta_conf, meta_reason = detect_metadata_ai_trace(img_path)
        artifact_hit, artifact_conf, artifact_reason = detect_artifact_signature(img_path)

        signals = self._build_signals(
            freq_score,
            dire_score,
            meta_hit,
            meta_conf,
            meta_reason,
            artifact_hit,
            artifact_conf,
            artifact_reason,
        )
        ai_confidence = self._fuse_confidence(signals)
        final_result = self._decide_label(ai_confidence)
        tags = self._derive_tags(signals, ai_confidence)

        if verbose:
            print(f"频域特征值：{freq_score}（值越高越可疑）")
            print(f"重建误差值：{dire_score}（<{THRESHOLD_DIRE}为AI）")
            print(f"元数据证据：{'命中' if signals.meta_hit else '未命中'}（{signals.meta_reason}）")
            print(f"单图取证证据：{'命中' if signals.artifact_hit else '未命中'}（{signals.artifact_reason}）")
            print(f"解释标签：{', '.join(tags) if tags else '无'}")
            print(f"AI置信度：{ai_confidence * 100}%")
            print(f"最终判定：{final_result}")
            print("=" * 50 + "\n")

        return {
            "result": final_result,
            "confidence": ai_confidence,
            "tags": tags,
            "signals": signals,
        }

    @staticmethod
    def _build_signals(
        freq_score: float,
        dire_score: float,
        meta_hit: bool,
        meta_confidence: float,
        meta_reason: str,
        artifact_hit: bool,
        artifact_confidence: float,
        artifact_reason: str,
    ) -> DetectionSignals:
        """构建标准化信号，便于后续融合。"""
        fft_confidence = AIDetector._calc_fft_confidence(freq_score)
        dire_confidence = AIDetector._calc_dire_confidence(dire_score)
        return DetectionSignals(
            freq_score=freq_score,
            dire_score=dire_score,
            fft_confidence=fft_confidence,
            dire_confidence=dire_confidence,
            meta_confidence=meta_confidence,
            meta_hit=meta_hit,
            meta_reason=meta_reason,
            artifact_confidence=artifact_confidence,
            artifact_hit=artifact_hit,
            artifact_reason=artifact_reason,
            artifact_grad95=AIDetector._extract_metric(artifact_reason, "grad95", 999.0),
            artifact_min_corr=AIDetector._extract_metric(artifact_reason, "minCorr", 0.0),
            artifact_entropy=AIDetector._extract_metric(artifact_reason, "entropy", 0.0),
            artifact_sat=AIDetector._extract_metric(artifact_reason, "sat", 0.0),
            graphic_penalty=AIDetector._calc_graphic_penalty(artifact_reason),
        )

    @staticmethod
    def _fuse_confidence(signals: DetectionSignals) -> float:
        """融合多信号并对冲突场景做仲裁。"""
        ela_value = AIDetector._extract_metric(signals.artifact_reason, "ELA", 0.0)
        strong_artifact_ai = (
            signals.artifact_grad95 >= 260.0
            and signals.artifact_min_corr <= 0.82
            and ela_value >= 4.2
            and signals.artifact_entropy >= 7.6
        )
        upscale_old_video_ai = (
            signals.artifact_confidence >= 0.52
            and signals.artifact_grad95 >= 240.0
            and signals.artifact_min_corr >= 0.965
            and signals.artifact_entropy >= 7.2
            and ela_value <= 1.25
            and signals.artifact_sat <= 0.03
            and 0.45 <= signals.dire_confidence <= 0.62
        )

        score = (
            signals.artifact_confidence * 0.72
            + signals.fft_confidence * 0.18
            + signals.dire_confidence * 0.10
        )

        # 单图强证据（平滑且跨通道一致）时，直接抬升到 AI 区间。
        if (
            signals.artifact_confidence >= 0.76
            and 30.0 <= signals.artifact_grad95 <= 120.0
            and signals.artifact_min_corr >= 0.95
            and signals.artifact_entropy >= 6.8
            and signals.artifact_sat <= 0.20
        ):
            score = max(score, 0.72)

        # 高频重采样痕迹明显时抬升（常见于 AI 处理/增强截图）。
        if (
            120.0 <= signals.artifact_grad95 <= 190.0
            and signals.artifact_min_corr <= 0.93
            and signals.artifact_entropy >= 6.3
            and ela_value >= 1.8
            and signals.dire_confidence <= 0.18
        ):
            score = max(score, 0.64)

        # 面部替换/编辑类截图常见于该组合：中低熵 + 中梯度 + 低通道相关 + 低重建支持。
        if (
            45.0 <= signals.artifact_grad95 <= 95.0
            and 0.86 <= signals.artifact_min_corr <= 0.93
            and 6.35 <= signals.artifact_entropy <= 6.90
            and 0.70 <= ela_value <= 1.40
            and signals.fft_confidence >= 0.58
            and signals.dire_confidence <= 0.08
        ):
            score = max(score, 0.63)

        # 老视频源被 AI 高清化时，常见“高梯度 + 高通道相关 + 低 ELA”的平滑增强特征。
        if upscale_old_video_ai:
            score = max(score, 0.64)

        # 高伪影+低通道相关的样本更偏 AI 增强/生成链路。
        if strong_artifact_ai:
            score = max(score, 0.62)

        # 自然截图分布保护：避免被“平滑+高熵”误抬成 AI。
        if (
            not signals.meta_hit
            and 40.0 <= signals.artifact_grad95 <= 95.0
            and signals.artifact_min_corr <= 0.985
            and signals.artifact_entropy >= 7.2
        ):
            if 0.7 <= ela_value <= 1.5:
                score = min(score, 0.44)

        # 实拍照片保护：高相关但频域不支持时，限制为非 AI。
        if (
            not signals.meta_hit
            and signals.artifact_min_corr >= 0.98
            and 70.0 <= signals.artifact_grad95 <= 135.0
            and 0.8 <= ela_value <= 1.3
            and signals.fft_confidence <= 0.35
        ):
            score = min(score, 0.46)

        # 弱纹理截图保护：避免低梯度截图被误判疑似。
        if (
            not signals.meta_hit
            and signals.artifact_grad95 < 35.0
            and signals.artifact_min_corr < 0.40
            and signals.artifact_entropy < 7.1
            and signals.dire_confidence < 0.10
        ):
            score = min(score, 0.42)

        # 元数据只作为辅助，但当视觉也有中等支持时可抬升到 AI。
        if signals.meta_hit and (signals.fft_confidence >= 0.65 or signals.artifact_confidence >= 0.50):
            score = max(score, 0.66)
        elif signals.meta_hit and 0.45 <= score < 0.60:
            score = min(score + 0.04, 0.60)

        # 对图标/二维码/极端锐利图形做通用抑制，降低误报。
        score -= signals.graphic_penalty

        # 高伪影样本在惩罚后保留最低 AI 分数，避免被过度压制。
        if strong_artifact_ai:
            score = max(score, 0.62)

        # 老视频源高清化命中后，避免被保护项或图形抑制完全压回真实。
        if upscale_old_video_ai:
            score = max(score, 0.64)

        return round(min(max(score, 0.0), 1.0), 4)

    @staticmethod
    def _decide_label(ai_confidence: float) -> str:
        """根据融合置信度给出最终判定。"""
        if ai_confidence >= THRESHOLD_FINAL:
            return "AI生成图片（SD/扩散模型）"
        if ai_confidence >= max(THRESHOLD_FINAL - 0.15, 0.50):
            return "疑似AI生成图片（建议复检）"
        return "真实图片"

    @staticmethod
    def _calc_fft_confidence(freq_score: float) -> float:
        """将 FFT 分数映射到 [0, 1] 置信度。"""
        # 经过内存优化后频域分值改到 0~15 左右，此处做重新标定。
        # 在当前尺度下，分值偏低通常意味着更平滑的高频结构（更可疑）。
        center = 11.2
        scale = 0.75
        return 1.0 / (1.0 + math.exp((freq_score - center) / scale))

    @staticmethod
    def _calc_dire_confidence(dire_score: float) -> float:
        """将 DIRE 重建误差映射到 [0, 1] 置信度（误差越小越偏AI）。"""
        ai_edge = THRESHOLD_DIRE
        real_edge = THRESHOLD_DIRE * 4
        if dire_score <= ai_edge:
            return 1.0
        if dire_score >= real_edge:
            return 0.0
        return (real_edge - dire_score) / (real_edge - ai_edge)

    @staticmethod
    def _extract_metric(reason: str, key: str, default: float) -> float:
        match = re.search(rf"{re.escape(key)}=([0-9]+(?:\.[0-9]+)?)", reason or "")
        if not match:
            return default
        try:
            return float(match.group(1))
        except Exception:
            return default

    @staticmethod
    def _calc_graphic_penalty(reason: str) -> float:
        """通用图形素材抑制项：减少二维码/图标/极端图形误报。"""
        grad95 = AIDetector._extract_metric(reason, "grad95", 999.0)
        min_corr = AIDetector._extract_metric(reason, "minCorr", 0.0)
        ela = AIDetector._extract_metric(reason, "ELA", 0.0)
        entropy = AIDetector._extract_metric(reason, "entropy", 0.0)
        sat = AIDetector._extract_metric(reason, "sat", 0.0)

        penalty = 0.0
        # 过度锐利边缘（常见于二维码/线稿）
        if grad95 >= 180.0:
            penalty += 0.20
        # 近乎纯色或扁平图标
        if grad95 <= 8.0 and min_corr >= 0.97:
            penalty += 0.20
        # 极低压缩误差 + 高通道一致性常见于图形素材
        if ela <= 0.22 and min_corr >= 0.95:
            penalty += 0.10
        # 低熵更接近普通拍摄照片纹理，降低误报。
        if entropy <= 5.9:
            penalty += 0.12
        # 极端高亮/纯黑占比高的素材常见于非自然照片。
        if sat >= 0.45:
            penalty += 0.12
        # 极端通道相关且重建证据弱，常见于真实旧照/简单图像。
        if min_corr >= 0.995 and AIDetector._extract_metric(reason, "ELA", 0.0) <= 1.1:
            penalty += 0.12

        return min(penalty, 0.36)

    @staticmethod
    def _derive_tags(signals: DetectionSignals, ai_confidence: float) -> list[str]:
        """生成可解释标签，用于结果展示。"""
        tags = []
        ela_value = AIDetector._extract_metric(signals.artifact_reason, "ELA", 0.0)

        if signals.meta_hit:
            tags.append("元数据命中")
        if signals.fft_confidence >= 0.70:
            tags.append("频域异常")
        if signals.dire_confidence >= 0.35:
            tags.append("重建误差偏低")
        if signals.artifact_confidence >= 0.72:
            tags.append("纹理一致性异常")

        if (
            signals.artifact_grad95 >= 260.0
            and signals.artifact_min_corr <= 0.82
            and ela_value >= 4.2
            and signals.artifact_entropy >= 7.6
        ):
            tags.append("高伪影编辑痕迹")

        if (
            45.0 <= signals.artifact_grad95 <= 95.0
            and 0.86 <= signals.artifact_min_corr <= 0.93
            and 6.35 <= signals.artifact_entropy <= 6.90
            and 0.70 <= ela_value <= 1.40
        ):
            tags.append("换脸/编辑截图特征")

        if signals.graphic_penalty >= 0.20:
            tags.append("图形素材抑制")

        if (
            signals.artifact_min_corr >= 0.98
            and 70.0 <= signals.artifact_grad95 <= 135.0
            and 0.8 <= ela_value <= 1.3
            and signals.fft_confidence <= 0.35
        ):
            tags.append("疑似翻拍/旧照特征")

        if (
            signals.artifact_confidence >= 0.52
            and signals.artifact_grad95 >= 240.0
            and signals.artifact_min_corr >= 0.965
            and signals.artifact_entropy >= 7.2
            and ela_value <= 1.25
            and signals.artifact_sat <= 0.03
            and 0.45 <= signals.dire_confidence <= 0.62
        ):
            tags.append("老视频源高清化特征")

        if 0.50 <= ai_confidence < THRESHOLD_FINAL:
            tags.append("建议复检")

        return tags

    def __del__(self):
        """清理资源"""
        from .dire_detector import cleanup
        try:
            cleanup()
        except Exception:
            pass
