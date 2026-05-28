from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "reports"
DEFAULT_TEMPLATE_NAME = "default"


def _render_cards(items: list[dict]) -> str:
    cards = []
    for item in items:
        file_uri = item["path"].as_uri()
        tags_text = ", ".join(item.get("tags", [])) or "无"
        content_label = item.get("content_label", "未见明显AI内容")
        cards.append(
            "\n".join(
                [
                    '<article class="card">',
                    f'  <a href="{escape(file_uri)}" target="_blank" rel="noopener">',
                    f'    <img src="{escape(file_uri)}" alt="{escape(item["name"])}" loading="lazy" />',
                    "  </a>",
                    '  <div class="meta">',
                    f'    <div class="name" title="{escape(item["name"])}">{escape(item["name"])} </div>',
                    f'    <div class="tag {escape(item["bucket"])}">{escape(item["label"])} </div>',
                    f'    <div class="conf">AI置信度: {item["confidence"] * 100:.2f}%</div>',
                    f'    <div class="conf">内容判定: {escape(content_label)}</div>',
                    f'    <div class="tags">标签: {escape(tags_text)}</div>',
                    "  </div>",
                    "</article>",
                ]
            )
        )

    if not cards:
        return '<p class="empty">无样本</p>'

    return "\n".join(cards)


def _load_template(template_path: Path) -> Template:
    return Template(template_path.read_text(encoding="utf-8"))


def _resolve_template_path(template: str | Path | None) -> Path:
    if template is None:
        return TEMPLATES_DIR / f"{DEFAULT_TEMPLATE_NAME}.html"

    if isinstance(template, Path):
        return template

    candidate = Path(template)
    if candidate.suffix or candidate.parent != Path("."):
        return candidate

    return TEMPLATES_DIR / f"{template}.html"


def generate_html_report(target_dir: Path, summary: dict, elapsed: float, template: str | Path | None = None) -> Path:
    """Generate an HTML report from summary data and a template file."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = target_dir / f"ai-detect-report-{timestamp}.html"

    total = summary["total"]
    ai_count = summary["ai"]
    suspect_count = summary["suspect"]
    real_count = summary["real"]
    error_count = len(summary["errors"])

    if total > 0:
        ai_pct = ai_count * 100.0 / total
        suspect_pct = suspect_count * 100.0 / total
        real_pct = real_count * 100.0 / total
    else:
        ai_pct = 0.0
        suspect_pct = 0.0
        real_pct = 0.0

    avg_conf = 0.0
    if summary["confidences"]:
        avg_conf = sum(summary["confidences"]) / len(summary["confidences"])

    flagged = sorted(summary["flagged"], key=lambda x: x["confidence"], reverse=True)
    ai_items = [x for x in flagged if x["bucket"] == "ai"]
    suspect_items = [x for x in flagged if x["bucket"] == "suspect"]
    content_items = list(summary.get("content_hits", []))
    embedded_items = list(summary.get("embedded", []))

    resolved_template = _resolve_template_path(template)
    if not resolved_template.exists():
        raise FileNotFoundError(f"模板不存在: {resolved_template}")

    template = _load_template(resolved_template)
    html = template.substitute(
        target_dir=escape(str(target_dir)),
        generated_at=escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        total=total,
        ai_count=ai_count,
        suspect_count=suspect_count,
        real_count=real_count,
        embedded_count=len(content_items),
        embedded_real_count=len(embedded_items),
        error_count=error_count,
        avg_conf=f"{avg_conf * 100:.2f}%",
        elapsed=f"{elapsed:.2f}s",
        ai_pct=f"{ai_pct:.2f}",
        suspect_pct=f"{suspect_pct:.2f}",
        real_pct=f"{real_pct:.2f}",
        ai_cards_html=_render_cards(ai_items),
        suspect_cards_html=_render_cards(suspect_items),
        embedded_cards_html=_render_cards(embedded_items),
    )

    report_path.write_text(html, encoding="utf-8")
    return report_path
