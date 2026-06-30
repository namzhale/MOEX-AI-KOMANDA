from __future__ import annotations

import html
import re
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "moex_ai_hackathon_llm_agent_draft.md"
OUTPUT_DIR = ROOT / "output" / "pdf"
OUTPUT = OUTPUT_DIR / "moex_ai_hackathon_llm_agent_preview.pdf"

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = FONT_DIR / "arial.ttf"
FONT_BOLD = FONT_DIR / "arialbd.ttf"
FONT_ITALIC = FONT_DIR / "ariali.ttf"


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("ArticleRegular", str(FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont("ArticleBold", str(FONT_BOLD)))
    pdfmetrics.registerFont(TTFont("ArticleItalic", str(FONT_ITALIC)))


def make_styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="TitleRu",
            parent=base["Title"],
            fontName="ArticleBold",
            fontSize=24,
            leading=30,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
            spaceAfter=16,
        )
    )
    base.add(
        ParagraphStyle(
            name="H2Ru",
            parent=base["Heading2"],
            fontName="ArticleBold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#111827"),
            spaceBefore=18,
            spaceAfter=8,
        )
    )
    base.add(
        ParagraphStyle(
            name="BodyRu",
            parent=base["BodyText"],
            fontName="ArticleRegular",
            fontSize=10.5,
            leading=15,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1f2937"),
            spaceAfter=7,
        )
    )
    base.add(
        ParagraphStyle(
            name="QuoteRu",
            parent=base["BodyRu"],
            fontName="ArticleItalic",
            leftIndent=8 * mm,
            rightIndent=4 * mm,
            borderColor=colors.HexColor("#94a3b8"),
            borderWidth=0,
            borderPadding=6,
            backColor=colors.HexColor("#f8fafc"),
            textColor=colors.HexColor("#334155"),
            spaceBefore=4,
            spaceAfter=10,
        )
    )
    base.add(
        ParagraphStyle(
            name="BulletRu",
            parent=base["BodyRu"],
            leftIndent=6 * mm,
            firstLineIndent=0,
            bulletIndent=0,
            spaceAfter=3,
        )
    )
    base.add(
        ParagraphStyle(
            name="CaptionRu",
            parent=base["BodyRu"],
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#64748b"),
            spaceBefore=4,
            spaceAfter=12,
        )
    )
    base.add(
        ParagraphStyle(
            name="FooterRu",
            parent=base["BodyRu"],
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#94a3b8"),
        )
    )
    return base


def inline_markup(text: str) -> str:
    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"@@MARKUP{len(placeholders) - 1}@@"

    def replace_link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        url = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{url}" color="#2563eb"><u>{label}</u></a>')

    def replace_code(match: re.Match[str]) -> str:
        code = html.escape(match.group(1), quote=False)
        return stash(f'<font color="#0f172a">{code}</font>')

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)
    text = re.sub(r"`([^`]+)`", replace_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)

    for idx, value in enumerate(placeholders):
        text = text.replace(html.escape(f"@@MARKUP{idx}@@"), value)
    return text


def image_flowable(relative_path: str, alt: str, max_width: float, max_height: float):
    image_path = (ROOT / relative_path).resolve()
    with PILImage.open(image_path) as img:
        width, height = img.size
    scale = min(max_width / width, max_height / height, 1.0)
    flow = Image(str(image_path), width=width * scale, height=height * scale)
    flow.hAlign = "CENTER"
    return flow


def build_story(markdown: str, styles) -> list:
    story: list = []
    paragraph_lines: list[str] = []
    bullet_items: list[str] = []
    numbered_items: list[str] = []
    max_image_width = 170 * mm
    max_image_height = 105 * mm

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            text = " ".join(line.strip() for line in paragraph_lines)
            story.append(Paragraph(inline_markup(text), styles["BodyRu"]))
            paragraph_lines = []

    def flush_bullets() -> None:
        nonlocal bullet_items
        if bullet_items:
            for item in bullet_items:
                story.append(Paragraph(inline_markup(f"- {item}"), styles["BulletRu"]))
            story.append(Spacer(1, 4))
            bullet_items = []

    def flush_numbered() -> None:
        nonlocal numbered_items
        if numbered_items:
            for idx, item in enumerate(numbered_items, start=1):
                story.append(Paragraph(inline_markup(f"{idx}. {item}"), styles["BulletRu"]))
            story.append(Spacer(1, 4))
            numbered_items = []

    def flush_all() -> None:
        flush_paragraph()
        flush_bullets()
        flush_numbered()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or stripped == "<cut />":
            flush_all()
            continue

        image_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if image_match:
            flush_all()
            alt, path = image_match.group(1), image_match.group(2)
            story.append(Spacer(1, 8))
            story.append(image_flowable(path, alt, max_image_width, max_image_height))
            if alt:
                story.append(Paragraph(inline_markup(alt), styles["CaptionRu"]))
            continue

        if stripped.startswith("# "):
            flush_all()
            story.append(Paragraph(inline_markup(stripped[2:]), styles["TitleRu"]))
            continue

        if stripped.startswith("## "):
            flush_all()
            story.append(Paragraph(inline_markup(stripped[3:]), styles["H2Ru"]))
            continue

        if stripped.startswith("> "):
            flush_all()
            story.append(Paragraph(inline_markup(stripped[2:]), styles["QuoteRu"]))
            continue

        numbered_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered_match:
            flush_paragraph()
            flush_bullets()
            numbered_items.append(numbered_match.group(2))
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            flush_numbered()
            bullet_items.append(stripped[2:])
            continue

        flush_bullets()
        flush_numbered()
        paragraph_lines.append(stripped)

    flush_all()
    return story


def draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("ArticleRegular", 8)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawCentredString(A4[0] / 2, 10 * mm, f"MOEX AI Hackathon LLM Agent - preview - {doc.page}")
    canvas.restoreState()


def main() -> None:
    register_fonts()
    styles = make_styles()
    markdown = SOURCE.read_text(encoding="utf-8")
    story = build_story(markdown, styles)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="MOEX AI Hackathon LLM Agent",
        author="team-24",
    )
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
