# jiji「東京だいたい銀行」8キャラの話者アイコン生成（#306）。
#
#     .venv/bin/python assets/icons/gen_jiji_icons.py            # 全キャラ
#     .venv/bin/python assets/icons/gen_jiji_icons.py banzawa    # 1キャラだけ再生成
#
# 生成は Gemini の画像モデル。**ベース（normal）を1枚作り、それを入力にして
# 表情差分を「同一人物の編集」として作る**——プロンプトだけで4枚別々に作ると
# 別人が出るため（キャラの同一性は入力画像で担保する）。
# 実在の人物・俳優の写真は入力に使わない（docs/00 v9.1 の権利上の線引き）。
# 構図・画風は既存10人の原本（assets/icons/*.png = 背景つきアニメ調バストアップ、
# レンダ側で円形に切り抜かれる）に合わせる。透過にしないのも既存仕様に合わせるため。
import base64
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "gemini-3-pro-image"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# tools/pipeline/common.py の SECRETS_DIRS と同じ探索順（置き場が動いた実績があるため）
SECRETS_DIRS = [
    os.path.expanduser("~/repo/.cowork-secrets"),
    os.path.expanduser("~/dev/.cowork-secrets"),
    os.path.expanduser("~/.cowork-secrets"),
]

# 全キャラ共通の画風。既存原本（ChatGPT生成のアニメ調バストアップ）に寄せる
STYLE = (
    "High-quality Japanese anime illustration, detailed lineart and shading, "
    "bust-up portrait framed from the chest up, character centered and large in frame "
    "(the image will be cropped to a circle), softly blurred modern Japanese bank office "
    "interior background, dramatic TV-drama lighting, square 1:1 image. "
    "Completely original character design, not based on any real person or actor."
)

# キャラごとの容姿。人格は data/channels/jiji.json / channels.py の speech と対で設計
CHARACTERS = {
    "banzawa": (
        "A 46-year-old Japanese banker, neatly parted short black hair, sharp piercing "
        "eyes with quiet intensity, navy blue suit, white shirt, dark red tie."
    ),
    "owada": (
        "A 58-year-old Japanese bank executive, slicked-back gray-streaked hair, "
        "deep smile lines, charcoal double-breasted suit, purple tie and pocket square, "
        "theatrical smug charm like a stage actor."
    ),
    "todori": (
        "A 68-year-old gentle Japanese bank president, full white hair, kind wrinkled "
        "face, calm half-closed eyes, classic dark brown three-piece suit, holding a "
        "Japanese yunomi tea cup with both hands, faint steam rising."
    ),
    "gondo": (
        "A 46-year-old timid Japanese salaryman with a soft round face, worried slanted "
        "eyebrows, slightly messy hair, rumpled gray suit with a loosened tie, one hand "
        "pressed against his stomach as if it aches."
    ),
    "tomari": (
        "A 44-year-old smart sociable Japanese banker, wavy dark brown hair, light gray "
        "suit with no tie and an open collar, leaning slightly forward with a playful "
        "conspiratorial look, one hand raised beside his mouth as if sharing a secret."
    ),
    "shirosaki": (
        "A 41-year-old flamboyant Japanese financial inspector, perfectly styled glossy "
        "hair, thin elegant glasses, brilliant white suit with a pink shirt, holding a "
        "clipboard, one hand on his cheek, sly knowing smile, feminine graceful gestures."
    ),
    "kobikado": (
        "A 47-year-old flamboyant Japanese lawyer, long glossy black hair swept back, "
        "sharp fox-like eyes, luxurious black three-piece suit with a silk cravat and a "
        "small gold lapel pin, condescending smirk of absolute confidence."
    ),
    "nogi": (
        "A Japanese intelligence agent of unknown age, short dark hair, completely "
        "expressionless stoic face, sharp focused eyes, black turtleneck under a dark "
        "trench coat, standing perfectly still."
    ),
}

# 表情差分。normal はベース1枚目そのもの。ファイル名は <key>_<表情>.png で、
# 表情切替演出（#311）がこの名前を読む
EXPRESSIONS = {
    "angry": "furious expression, eyebrows drawn hard, shouting with mouth open wide",
    "shock": "shocked and appalled expression, wide eyes, mouth open in disbelief",
    "smug": "smug triumphant expression, confident grin, chin slightly raised",
}


def _key() -> str:
    for d in SECRETS_DIRS:
        p = os.path.join(d, "gemini_key.txt")
        if os.path.exists(p):
            return open(p).read().strip()
    raise SystemExit("gemini_key.txt がありません（~/repo/.cowork-secrets/ に置いてください）")


def _generate(api_key: str, prompt: str, base_png: bytes | None = None) -> bytes:
    parts = [{"text": prompt}]
    if base_png:
        parts.append({"inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(base_png).decode(),
        }})
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"imageConfig": {"aspectRatio": "1:1"}},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 混雑時の 429/500 系は待って引き直す。400 は直らないので即落とす
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            raise SystemExit(f"生成に失敗しました（HTTP {e.code}）: {e.read()[:300]}")
    for part in data["candidates"][0]["content"]["parts"]:
        blob = part.get("inlineData") or part.get("inline_data")
        if blob:
            return base64.b64decode(blob["data"])
    raise SystemExit(f"画像が返りませんでした: {json.dumps(data)[:300]}")


def main() -> None:
    api_key = _key()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for key, look in CHARACTERS.items():
        if only and key != only:
            continue
        base_path = os.path.join(HERE, f"{key}.png")
        print(f"[{key}] normal ...")
        base = _generate(api_key, f"{look} Neutral in-character expression. {STYLE}")
        with open(base_path, "wb") as f:
            f.write(base)
        for expr, desc in EXPRESSIONS.items():
            print(f"[{key}] {expr} ...")
            edited = _generate(api_key, (
                "Redraw the exact same character from the reference image: identical "
                "face, hairstyle, clothing, colors, art style, framing and background, "
                f"but change only the facial expression to: {desc}."
            ), base_png=base)
            with open(os.path.join(HERE, f"{key}_{expr}.png"), "wb") as f:
                f.write(edited)
    print("done")


if __name__ == "__main__":
    main()
