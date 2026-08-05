# チャンネルのアイコン・バナー生成（ロンロンの天秤 実測に基づくフラット2色スタイル）
# 使い捨てスクリプト。成果物は scratchpad/branding/ に出す。
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "branding")
os.makedirs(OUT, exist_ok=True)

HEAVY = "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc"
ROUND = "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc"
BLACK = (20, 18, 15)

random.seed(7)


def jitter_text(img, text, cx, cy, size, fill, max_rot=4, dy=5, tracking=0.02):
    """文字ごとに微回転・上下ズレを付けて手書きの素人感を出す。"""
    font = ImageFont.truetype(HEAVY, size)
    widths = []
    for ch in text:
        bbox = font.getbbox(ch)
        widths.append(bbox[2] - bbox[0] + int(size * tracking))
    total = sum(widths)
    x = cx - total // 2
    for ch, w in zip(text, widths):
        pad = size // 2
        tile = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        td.text((pad, pad), ch, font=font, fill=fill)
        tile = tile.rotate(random.uniform(-max_rot, max_rot), resample=Image.BICUBIC)
        img.paste(tile, (int(x) - pad, int(cy - size * 0.55) - pad + random.randint(-dy, dy)), tile)
        x += w


def brush_curve(d, pts, width):
    """点列に沿って円を打ち、太い筆致ふうの曲線を描く。"""
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        steps = max(2, int(math.hypot(x1 - x0, y1 - y0) / 2))
        for t in range(steps + 1):
            x = x0 + (x1 - x0) * t / steps
            y = y0 + (y1 - y0) * t / steps
            d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=BLACK)


# ---- モチーフ（黒シルエット） ----

def draw_oden(d, cx, cy, s, bg):
    """おでん串: 三角こんにゃく・ちくわ・大根を1本の串に。"""
    d.line([(cx, cy - s * 0.95), (cx, cy + s * 1.05)], fill=BLACK, width=int(s * 0.09))
    d.polygon([(cx - s * 0.52, cy - s * 0.12), (cx + s * 0.52, cy - s * 0.12), (cx, cy - s * 0.95)], fill=BLACK)
    d.rounded_rectangle([cx - s * 0.42, cy + s * 0.02, cx + s * 0.42, cy + s * 0.42], radius=s * 0.2, fill=BLACK)
    hole_w = s * 0.10
    d.ellipse([cx - hole_w, cy + s * 0.16, cx + hole_w, cy + s * 0.28], fill=bg)
    d.ellipse([cx - s * 0.40, cy + s * 0.52, cx + s * 0.40, cy + s * 1.06], fill=BLACK)


def draw_elephant(d, cx, cy, s, bg):
    """画面外から伸びてきたゾウの鼻。先端に鼻毛が1本だけ。頭は描かない。"""
    spine = [
        (cx - s * 1.15, cy - s * 1.15),
        (cx - s * 0.55, cy - s * 0.85),
        (cx - s * 0.15, cy - s * 0.35),
        (cx - s * 0.05, cy + s * 0.25),
        (cx - s * 0.25, cy + s * 0.75),
        (cx - s * 0.15, cy + s * 1.05),
    ]
    widths = [s * 0.62, s * 0.54, s * 0.44, s * 0.36, s * 0.28]
    for i in range(len(spine) - 1):
        brush_curve(d, spine[i:i + 2], widths[i])
    # 鼻のシワ（背景色の短い線を2本）
    for t, w in [(0.30, 0.20), (0.55, 0.17)]:
        x0 = cx - s * (0.28 - t * 0.1)
        y0 = cy - s * 0.6 + t * s * 1.1
        d.line([(x0 - s * w, y0), (x0 + s * w, y0)], fill=bg, width=max(3, int(s * 0.045)))
    # 鼻毛: 先端からくるんと1本
    hair = [
        (cx - s * 0.13, cy + s * 1.15),
        (cx - s * 0.02, cy + s * 1.32),
        (cx + s * 0.16, cy + s * 1.30),
        (cx + s * 0.18, cy + s * 1.14),
        (cx + s * 0.04, cy + s * 1.10),
    ]
    brush_curve(d, hair, s * 0.05)


def draw_chime(d, cx, cy, s, bg):
    """校内放送のラッパスピーカー。音の弧が途中でぶった切れている。"""
    d.polygon([(cx - s * 0.95, cy - s * 0.30), (cx - s * 0.35, cy - s * 0.16),
               (cx - s * 0.35, cy + s * 0.16), (cx - s * 0.95, cy + s * 0.30)], fill=BLACK)
    d.polygon([(cx - s * 0.35, cy - s * 0.18), (cx + s * 0.30, cy - s * 0.62),
               (cx + s * 0.30, cy + s * 0.62), (cx - s * 0.35, cy + s * 0.18)], fill=BLACK)
    d.line([(cx - s * 0.72, cy + s * 0.24), (cx - s * 0.72, cy + s * 1.05)], fill=BLACK, width=int(s * 0.09))
    for r in [0.55, 0.82]:
        box = [cx + s * 0.30 - r * s, cy - r * s, cx + s * 0.30 + r * s, cy + r * s]
        d.arc(box, start=-38, end=38, fill=BLACK, width=int(s * 0.075))
    # 音はここで終わり（停止ボタン）
    sq = s * 0.16
    d.rectangle([cx + s * 1.30 - sq, cy - sq, cx + s * 1.30 + sq, cy + sq], fill=BLACK)


def draw_showa(d, cx, cy, s, bg):
    """灰皿（縁に煙草が1本のっている）とコッペパンが並んでいるだけ。"""
    # 灰皿
    d.chord([cx - s * 1.05, cy - s * 0.10, cx - s * 0.05, cy + s * 0.80], start=0, end=180, fill=BLACK)
    d.rectangle([cx - s * 0.90, cy + s * 0.30, cx - s * 0.20, cy + s * 0.42], fill=BLACK)
    # 縁にのった煙草（先端が皿の内側を向く）
    brush_curve(d, [(cx - s * 1.02, cy - s * 0.34), (cx - s * 0.48, cy - s * 0.12)], s * 0.10)
    smoke = [(cx - s * 1.04, cy - s * 0.44), (cx - s * 1.16, cy - s * 0.66),
             (cx - s * 0.98, cy - s * 0.88), (cx - s * 1.10, cy - s * 1.10)]
    brush_curve(d, smoke, s * 0.05)
    # コッペパン（ふくらみ2つ＋短い切れ込み）
    d.ellipse([cx + s * 0.10, cy - s * 0.06, cx + s * 1.35, cy + s * 0.72], fill=BLACK)
    d.ellipse([cx + s * 0.24, cy - s * 0.16, cx + s * 0.86, cy + s * 0.28], fill=BLACK)
    d.ellipse([cx + s * 0.62, cy - s * 0.16, cx + s * 1.22, cy + s * 0.28], fill=BLACK)
    d.arc([cx + s * 0.34, cy + s * 0.06, cx + s * 1.12, cy + s * 0.60],
          start=200, end=340, fill=bg, width=max(3, int(s * 0.04)))


CHANNELS = [
    dict(key="meme", bg=(227, 181, 5), name="おでん定点観測", tiny="本日も観測は継続しています",
         motif=draw_oden),
    dict(key="trivia", bg=(167, 198, 217), name="ゾウの鼻毛", tiny="※ゾウに鼻毛が生えているかは諸説あります",
         motif=draw_elephant),
    dict(key="heisei", bg=(240, 138, 36), name="チャイム鳴り止ます", tiny="キーンコーンカーンコー",
         motif=draw_chime),
    dict(key="showa", bg=(240, 223, 184), name="灰皿とコッペパン", tiny="当チャンネルは全席禁煙です",
         motif=draw_showa),
]

for ch in CHANNELS:
    # アイコン 800x800（円形に切り抜かれる前提で中央に寄せる）
    icon = Image.new("RGB", (800, 800), ch["bg"])
    d = ImageDraw.Draw(icon)
    d._image = icon
    ch["motif"](d, 400, 360, 240, ch["bg"])
    icon.save(os.path.join(OUT, f"icon_{ch['key']}.png"))

    # バナー 2048x1152（セーフエリア中央 1235x338 = x 406..1641 / y 407..745）
    banner = Image.new("RGB", (2048, 1152), ch["bg"])
    d = ImageDraw.Draw(banner)
    d._image = banner
    title = ch["name"] + ".チャンネル"
    size = 96 if len(title) <= 12 else 72
    jitter_text(banner, title, 960, 520, size, BLACK)
    d.line([(1024 - 430, 585), (1024 + 430, 585)], fill=BLACK, width=6)
    tiny_font = ImageFont.truetype(ROUND, 34)
    tw = d.textlength(ch["tiny"], font=tiny_font)
    d.text((1024 - tw / 2, 612), ch["tiny"], font=tiny_font, fill=BLACK)
    ch["motif"](d, 640, 665, 55, ch["bg"])
    banner.save(os.path.join(OUT, f"banner_{ch['key']}.png"))

print("done:", sorted(os.listdir(OUT)))
