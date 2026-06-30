from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).with_name("images")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1600, 900
BG = "#ffffff"
INK = "#172033"
MUTED = "#5f6b7a"
BLUE = "#2563eb"
BLUE_LIGHT = "#eff6ff"
GREEN = "#16a34a"
GREEN_LIGHT = "#ecfdf5"
RED = "#dc2626"
RED_LIGHT = "#fef2f2"
AMBER = "#d97706"
AMBER_LIGHT = "#fffbeb"
GRAY = "#f3f4f6"
LINE = "#cbd5e1"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


F_TITLE = font(54, True)
F_H1 = font(42, True)
F_H2 = font(32, True)
F_BODY = font(26)
F_SMALL = font(22)


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), BG)
    return im, ImageDraw.Draw(im)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def center_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt, fill=INK) -> None:
    x1, y1, x2, y2 = box
    tw, th = text_size(draw, text, fnt)
    draw.text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2 - 2), text, font=fnt, fill=fill)


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        test = word if not cur else f"{cur} {word}"
        if text_size(draw, test, fnt)[0] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def paragraph(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, width: int, fnt=F_BODY, fill=MUTED, line_gap=10) -> int:
    for line in wrap_lines(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += text_size(draw, line, fnt)[1] + line_gap
    return y


def card(draw: ImageDraw.ImageDraw, xy, title: str, body: str = "", fill=GRAY, outline=LINE, accent=BLUE) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=20, fill=fill, outline=outline, width=3)
    draw.rectangle((x1, y1, x1 + 10, y2), fill=accent)
    draw.text((x1 + 34, y1 + 28), title, font=F_H2, fill=INK)
    if body:
        paragraph(draw, x1 + 34, y1 + 82, body, x2 - x1 - 68, F_SMALL, MUTED, 8)


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill=BLUE, width=5) -> None:
    draw.line((start, end), fill=fill, width=width)
    ex, ey = end
    sx, sy = start
    if ex >= sx:
        pts = [(ex, ey), (ex - 18, ey - 10), (ex - 18, ey + 10)]
    else:
        pts = [(ex, ey), (ex + 18, ey - 10), (ex + 18, ey + 10)]
    draw.polygon(pts, fill=fill)


def footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    draw.line((80, 820, 1520, 820), fill=LINE, width=2)
    center_text(draw, (80, 830, 1520, 875), text, F_SMALL, MUTED)


def save(im: Image.Image, name: str) -> None:
    im.save(OUT / name, "PNG", optimize=True)


def draw_cover() -> None:
    im, d = canvas()
    d.text((90, 95), "LLM trading agent", font=F_TITLE, fill=INK)
    paragraph(d, 92, 175, "Autonomous MOEX sandbox trading system: data, model, risk layer and execution.", 1050, F_BODY)
    boxes = [
        (120, 360, 420, 520, "Market data", BLUE_LIGHT, BLUE),
        (505, 360, 805, 520, "LLM graph", AMBER_LIGHT, AMBER),
        (890, 360, 1190, 520, "Risk officer", GREEN_LIGHT, GREEN),
        (1275, 360, 1515, 520, "Orders", RED_LIGHT, RED),
    ]
    for x1, y1, x2, y2, title, fill, accent in boxes:
        card(d, (x1, y1, x2, y2), title, "", fill, LINE, accent)
    for a, b in [((420, 440), (505, 440)), ((805, 440), (890, 440)), ((1190, 440), (1275, 440))]:
        arrow(d, a, b)
    footer(d, "Main lesson: the model proposes, deterministic controls decide")
    save(im, "01-cover-trading-llm-agent.png")


def draw_hackathon() -> None:
    im, d = canvas()
    d.text((80, 70), "MOEX AI Hackathon: constraints", font=F_TITLE, fill=INK)
    data = [
        ("Online", "Remote format"),
        ("50 teams", "Selected teams"),
        ("1M RUB", "Virtual portfolio"),
        ("20 stocks", "Trading universe"),
        ("Autonomy", "No manual control"),
        ("Risk", "Part of evaluation"),
    ]
    x, y = 90, 210
    for i, (title, body) in enumerate(data):
        cx = x + (i % 3) * 490
        cy = y + (i // 3) * 240
        card(d, (cx, cy, cx + 390, cy + 155), title, body, "#ffffff", LINE, BLUE if i % 2 == 0 else GREEN)
    footer(d, "The score depended not only on PnL, but also on autonomy, architecture, risk and turnover")
    save(im, "02-hackathon-conditions.png")


def draw_architecture() -> None:
    im, d = canvas()
    d.text((80, 60), "Solution architecture", font=F_TITLE, fill=INK)
    boxes = [
        (80, 260, 300, 420, "Data", "Candles, news, portfolio", BLUE_LIGHT, BLUE),
        (380, 260, 600, 420, "Prefilter", "Cheap rule gate", BLUE_LIGHT, BLUE),
        (680, 260, 900, 420, "LLM graph", "Analyst, news, debate", AMBER_LIGHT, AMBER),
        (980, 260, 1200, 420, "Risk", "Limits and exits", GREEN_LIGHT, GREEN),
        (1280, 260, 1500, 420, "ArenaGo", "Market orders", RED_LIGHT, RED),
    ]
    for x1, y1, x2, y2, title, body, fill, accent in boxes:
        card(d, (x1, y1, x2, y2), title, body, fill, LINE, accent)
    for start_x in [300, 600, 900, 1200]:
        arrow(d, (start_x, 340), (start_x + 80, 340))
    card(d, (380, 570, 690, 710), "Journal", "Decision trail", "#ffffff", LINE, MUTED)
    card(d, (770, 570, 1080, 710), "Reflection", "Trade lessons", "#ffffff", LINE, MUTED)
    card(d, (1160, 570, 1470, 710), "Grafana", "Runtime logs", "#ffffff", LINE, MUTED)
    for a, b in [((690, 640), (770, 640)), ((1080, 640), (1160, 640))]:
        arrow(d, a, b, MUTED)
    footer(d, "Data enters on the left; every order must pass the deterministic risk layer")
    save(im, "03-agent-architecture.png")


def draw_llm_pipeline() -> None:
    im, d = canvas()
    d.text((80, 60), "LLM decision pipeline", font=F_TITLE, fill=INK)
    steps = [
        ("Prefilter", "Skip weak setups"),
        ("Tech analyst", "Indicators and price"),
        ("News analyst", "Recent events"),
        ("Bull / Bear", "Arguments both ways"),
        ("Trader", "Final JSON decision"),
    ]
    x = 80
    for i, (title, body) in enumerate(steps):
        fill = BLUE_LIGHT if i < 3 else AMBER_LIGHT
        accent = BLUE if i < 3 else AMBER
        card(d, (x, 310, x + 240, 480), title, body, fill, LINE, accent)
        if i < len(steps) - 1:
            arrow(d, (x + 240, 395), (x + 305, 395))
        x += 305
    card(d, (1020, 120, 1480, 235), "Live portfolio context", "cash, NAV, positions, exposure", GREEN_LIGHT, LINE, GREEN)
    arrow(d, (1250, 235), (1250, 310), GREEN)
    card(d, (1020, 600, 1480, 720), "Structured output", "BUY / SELL / HOLD + confidence", "#ffffff", LINE, MUTED)
    arrow(d, (1250, 480), (1250, 600), MUTED)
    footer(d, "The model sees portfolio state before it recommends changing portfolio state")
    save(im, "04-llm-pipeline.png")


def draw_risk() -> None:
    im, d = canvas()
    d.text((80, 60), "Risk officer", font=F_TITLE, fill=INK)
    card(d, (90, 280, 360, 450), "Decision", "Signal from trader", AMBER_LIGHT, LINE, AMBER)
    card(d, (520, 215, 1080, 515), "Deterministic checks", "confidence, edge, size, cash, concentration, drawdown, spread, no-flip", GREEN_LIGHT, LINE, GREEN)
    card(d, (1240, 205, 1500, 345), "PASS", "Submit order", GREEN_LIGHT, LINE, GREEN)
    card(d, (1240, 430, 1500, 570), "BLOCK", "Write reason", RED_LIGHT, LINE, RED)
    arrow(d, (360, 365), (520, 365))
    arrow(d, (1080, 320), (1240, 275), GREEN)
    arrow(d, (1080, 410), (1240, 500), RED)
    card(d, (520, 585, 1080, 755), "Exit bypass", "close long / cover short bypass opening gates", "#ffffff", LINE, BLUE)
    footer(d, "Opening risk is strict; de-risking exits have a shorter path")
    save(im, "05-risk-officer.png")


def draw_timeline() -> None:
    im, d = canvas()
    d.text((80, 60), "Engineering timeline", font=F_TITLE, fill=INK)
    steps = ["Data", "NAV", "Risk", "10m bars", "No flip", "Logs", "Hardening"]
    y = 420
    d.line((140, y, 1460, y), fill=LINE, width=6)
    for i, label in enumerate(steps):
        x = 140 + i * 220
        d.ellipse((x - 24, y - 24, x + 24, y + 24), fill=BLUE if i % 2 == 0 else GREEN)
        center_text(d, (x - 105, y - 140, x + 105, y - 80), f"{i+1:02}", F_H2, MUTED)
        center_text(d, (x - 105, y + 55, x + 105, y + 115), label, F_H2, INK)
    card(d, (200, 615, 1400, 735), "Pattern", "Most fixes were not prompt tweaks. They fixed boundaries: data freshness, portfolio context, risk gates and observability.", "#ffffff", LINE, AMBER)
    footer(d, "The useful story is not the first launch; it is the sequence of system-level fixes")
    save(im, "06-team-timeline.png")


def draw_loop() -> None:
    im, d = canvas()
    d.text((80, 60), "Production loop", font=F_TITLE, fill=INK)
    top = [
        (100, 245, 360, 385, "Observe", "market + portfolio"),
        (510, 245, 770, 385, "Think", "LLM graph"),
        (920, 245, 1180, 385, "Check", "risk layer"),
    ]
    bottom = [
        (920, 540, 1180, 680, "Trade", "ArenaGo"),
        (510, 540, 770, 680, "Learn", "journal + reflection"),
        (100, 540, 360, 680, "Report", "Grafana + logs"),
    ]
    for item in top + bottom:
        x1, y1, x2, y2, title, body = item
        card(d, (x1, y1, x2, y2), title, body, "#ffffff", LINE, BLUE)
    arrow(d, (360, 315), (510, 315), MUTED, 4)
    arrow(d, (770, 315), (920, 315), MUTED, 4)
    arrow(d, (1050, 385), (1050, 540), MUTED, 4)
    arrow(d, (920, 610), (770, 610), MUTED, 4)
    arrow(d, (510, 610), (360, 610), MUTED, 4)
    arrow(d, (230, 540), (230, 385), MUTED, 4)
    card(d, (1260, 350, 1500, 540), "Agent", "runs this loop every tick", BLUE_LIGHT, LINE, BLUE)
    footer(d, "A trading agent is a loop, not a single model call")
    save(im, "07-production-loop.png")


def main() -> None:
    draw_cover()
    draw_hackathon()
    draw_architecture()
    draw_llm_pipeline()
    draw_risk()
    draw_timeline()
    draw_loop()


if __name__ == "__main__":
    main()
