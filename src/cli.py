#!/usr/bin/env python3
"""AI图像检测工具 - 主入口"""
import argparse
import math
import sys
import time
import warnings
from pathlib import Path

from PIL import Image

from ai_detector import AIDetector
from ai_detector.dire_detector import cleanup
from ai_detector.reporting import generate_html_report


warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
warnings.filterwarnings(
    "ignore",
    message="Palette images with Transparency expressed in bytes should be converted to RGBA images",
    category=UserWarning,
)


DISCLAIMER_TEXT = (
    "免责声明：检测结果仅供参考，不保证准确性。"
    "复杂场景（压缩/转码/截图/局部嵌入）请务必人工复核。"
)


def _collect_images(target_dir: Path) -> list[Path]:
    image_extensions = [".jpg", ".jpeg", ".png", ".bmp", ".webp"]
    image_files = []
    seen = set()

    for ext in image_extensions:
        for img_path in target_dir.glob(f"*{ext}"):
            key = str(img_path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                image_files.append(img_path)
        for img_path in target_dir.glob(f"*{ext.upper()}"):
            key = str(img_path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                image_files.append(img_path)

    return sorted(image_files)


def _render_progress(current: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + "-" * width + "] 0/0 0.0%"
    ratio = current / total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{total} {ratio * 100:5.1f}%"


def _label_to_bucket(label: str) -> str:
    if "疑似AI" in label:
        return "suspect"
    if "AI生成" in label:
        return "ai"
    return "real"


def _bucket_to_tag(bucket: str) -> str:
    if bucket == "ai":
        return "AI"
    if bucket == "suspect":
        return "SUSPECT"
    return "REAL"


def _short_name(name: str, max_len: int = 36) -> str:
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


def _short_tags(tags: list[str], max_tags: int = 2) -> str:
    if not tags:
        return ""
    return " | ".join(tags[:max_tags])


def _print_report(summary: dict, elapsed: float):
    total = summary["total"]
    processed = summary["processed"]
    errors = summary["errors"]

    avg_conf = 0.0
    if summary["confidences"]:
        avg_conf = sum(summary["confidences"]) / len(summary["confidences"])

    print("\n" + "=" * 60)
    print("检测报告")
    print("=" * 60)
    print(f"总文件数: {total}")
    print(f"完成检测: {processed}")
    print(f"AI生成: {summary['ai']}")
    print(f"疑似AI: {summary['suspect']}")
    print(f"真实图片: {summary['real']}")
    print(f"疑似包含AI内容: {len(summary['content_hits'])}")
    print(f"其中真图嵌入AI内容: {len(summary['embedded'])}")
    print(f"失败数量: {len(errors)}")
    print(f"平均AI置信度: {avg_conf * 100:.2f}%")
    print(f"总耗时: {elapsed:.2f}s")

    if summary["top_ai"]:
        print("\n高置信AI样本 Top3:")
        for name, conf in sorted(summary["top_ai"], key=lambda x: x[1], reverse=True)[:3]:
            print(f"- {name}: {conf * 100:.2f}%")

    if errors:
        print("\n失败明细:")
        for name, reason in errors:
            print(f"- {name}: {reason}")

    print("=" * 60)
    print(DISCLAIMER_TEXT)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="AI图像检测工具")
    parser.add_argument("image_path", nargs="?", help="要检测的图片路径")
    parser.add_argument("-d", "--dir", dest="directory", help="批量检测目录下的所有图片")
    parser.add_argument(
        "--device",
        choices=["auto", "gpu", "cpu"],
        default="auto",
        help="推理设备模式：auto/gpu/cpu（默认: auto）",
    )
    parser.add_argument(
        "-t",
        "--template",
        default="default",
        help="报告模板名或模板文件路径（目录模式生效，默认: default）",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式，不打印详细信息")

    args = parser.parse_args(argv)
    detector = AIDetector(device_mode=args.device)
    print(DISCLAIMER_TEXT)

    try:
        if args.directory:
            target_dir = Path(args.directory).resolve()
            if not target_dir.exists():
                print(f"错误：目录不存在 - {target_dir}")
                sys.exit(1)
            if not target_dir.is_dir():
                print(f"错误：路径不是目录 - {target_dir}")
                sys.exit(1)

            image_files = _collect_images(target_dir)

            if not image_files:
                print(f"在目录 {target_dir} 中没有找到图片文件")
                sys.exit(0)

            print(f"找到 {len(image_files)} 个图片文件\n")

            start = time.perf_counter()
            summary = {
                "total": len(image_files),
                "processed": 0,
                "ai": 0,
                "suspect": 0,
                "real": 0,
                "content_hits": [],
                "embedded": [],
                "errors": [],
                "confidences": [],
                "top_ai": [],
                "flagged": [],
            }

            for idx, img_path in enumerate(image_files, start=1):
                content_result = "未见明显AI内容"
                try:
                    details = detector.detect_detailed(str(img_path), verbose=False)
                    result = details["result"]
                    confidence = details["confidence"]
                    tags = details.get("tags", [])
                    bucket = _label_to_bucket(result)
                    summary[bucket] += 1
                    summary["processed"] += 1
                    content_result = details.get("content_result", content_result)
                    if isinstance(confidence, (int, float)) and math.isfinite(confidence):
                        summary["confidences"].append(float(confidence))
                    if bucket == "ai":
                        summary["top_ai"].append((img_path.name, confidence))
                    if bucket in {"ai", "suspect"}:
                        summary["flagged"].append(
                            {
                                "name": img_path.name,
                                "path": img_path.resolve(),
                                "confidence": float(confidence),
                                "bucket": bucket,
                                "label": "AI" if bucket == "ai" else "SUSPECT",
                                "tags": tags,
                            }
                        )
                        summary["flagged"][-1]["content_label"] = content_result
                    if content_result != "未见明显AI内容":
                        summary["content_hits"].append(
                            {
                                "name": img_path.name,
                                "path": img_path.resolve(),
                                "confidence": float(confidence),
                                "bucket": bucket,
                                "label": result,
                                "tags": tags,
                                "content_label": content_result,
                            }
                        )
                    if bucket == "real" and content_result != "未见明显AI内容":
                        summary["embedded"].append(
                            {
                                "name": img_path.name,
                                "path": img_path.resolve(),
                                "confidence": float(confidence),
                                "bucket": bucket,
                                "label": content_result,
                                "tags": tags,
                                "content_label": content_result,
                            }
                        )

                    status = (
                        f"{_render_progress(idx, len(image_files))} | "
                        f"当前: {_short_name(img_path.name)} | 结果: {_bucket_to_tag(bucket)} | "
                        f"置信度: {confidence * 100:.2f}%"
                    )
                    tag_text = _short_tags(tags)
                    if tag_text:
                        status += f" | 标签: {tag_text}"
                    if content_result != "未见明显AI内容":
                        status += f" | 内容: {content_result}"
                    sys.stdout.write("\r" + status.ljust(140))
                    sys.stdout.flush()
                except Exception as e:
                    summary["errors"].append((img_path.name, str(e)))
                    status = (
                        f"{_render_progress(idx, len(image_files))} | "
                        f"当前: {img_path.name} | 结果: 失败"
                    )
                    sys.stdout.write("\r" + status.ljust(140))
                    sys.stdout.flush()

            sys.stdout.write("\n")
            elapsed = time.perf_counter() - start
            _print_report(summary, elapsed)
            report_path = generate_html_report(target_dir, summary, elapsed, template=args.template)
            print(f"HTML报告: {report_path}")

        elif args.image_path:
            detector.detect(args.image_path, verbose=not args.quiet)
        else:
            print("请提供图片路径或使用 -d 参数指定目录")
            parser.print_help()

    finally:
        cleanup()


if __name__ == "__main__":
    main()
