#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""話者アイコンをいらすとやから生成する（content/assets/icons/）。

    .venv/bin/python tools/fetch_icons.py

アイコンは BGM・効果音と同じく git 管理外（content/assets/ は .gitignore）なので、
環境を作り直したときはこれで揃える。既にあるファイルは上書きしない。

## なぜ VOICEVOX キャラの顔ではないのか（#154・2026-08-05 本人決定）

参照チャンネルを実測した結果、どこも VOICEVOX キャラの絵を画面に出していない
（ロンロンの天秤は実写風の人物顔。docs/02 2章）。声はキャラの同一性だが、
見た目は「スレ民・寸劇の登場人物」なので、役柄を表す別人の顔を割り当てる。
いらすとやの「顔のアイコン」シリーズは同一画風で12人×6シリーズあり、
配役全員を1つの画風で揃えられる。

いらすとやの規約（https://www.irasutoya.com/p/terms.html）:
- 商用利用も無料。ただし**1つの制作物につき素材20点まで**（動画1本のアイコンは最大でも配役数なので収まる）
- クレジット表記は不要（credits.txt には慣行で「アイコン: いらすとや」を出す）
- 加工可（切り抜き・色変更OK）。**素材の再配布は禁止**

→ 再配布禁止なので、生成済みアイコンをリポジトリに置かずここで作り直す設計にしている。

背景色はキャラの字幕色と同じにする（色＝話者の対応を画面全体で崩さない。docs/09 4-8）。
"""
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.channels import CHARACTERS
from tools.pipeline.common import ASSETS_DIR, PipelineError

ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

# いらすとや「顔のアイコン」系の素材ページ。画像はページから正規表現で拾う
# （blogger.googleusercontent.com の直リンクは長く不安定なので、ページを起点にする）
PAGES = {
    "boy": "https://www.irasutoya.com/2013/10/blog-post_5077.html",         # 男の子の顔のアイコン
    "girl": "https://www.irasutoya.com/2013/10/blog-post_3974.html",        # 女の子の顔のアイコン
    "youngman": "https://www.irasutoya.com/2013/10/blog-post_9098.html",    # 男性の顔のアイコン
    "youngwoman": "https://www.irasutoya.com/2013/10/blog-post_6907.html",  # 女性の顔のアイコン
    "man": "https://www.irasutoya.com/2013/10/blog-post_872.html",          # おじさんの顔のアイコン
    "medical": "https://www.irasutoya.com/2015/10/blog-post_135.html",      # 白衣を着た女性のアイコン
}

# キャラキー → 素材ファイル名。役柄（channels.py の speech / cast の role）に合わせて選定。
# 選定の経緯と一覧画像は #154 を参照
PICKS = {
    "zundamon": "boy_01.png",                    # 元気な男の子。ツッコミ役
    "metan": "youngwoman_39.png",                # 花飾りの上品な女性
    "tsumugi": "youngwoman_47.png",              # サイドポニーの明るい女子
    "ritsu": "girl_18.png",                      # 姫カットの無表情
    "hau": "youngwoman_37.png",                  # 柔らかい微笑み
    "takehiro": "youngman_29.png",               # 普通の若い男
    "kotaro": "boy_11.png",                      # 帽子で大笑いの少年
    "ryusei": "man_50.png",                      # 渋いオールバック
    "ryusei_nekketsu": "man_54.png",             # 太眉の強面。昭和の熱血
    "himari": "youngwoman_43.png",               # 落ち着いたウェーブ
    "sora": "youngwoman_42.png",                 # お団子の年上女性
    "mochiko": "youngwoman_44.png",              # 大らかな笑顔
    "shishio": "youngman_34.png",                # 角メガネの真面目男
    "whitecul": "youngwoman_38.png",             # 赤メガネのクール女性
    "nurserobo": "icon_medical_woman01.png",     # 白衣の女性
}

SIZE = 300   # render.ICON_SIZE と同じ。丸抜きは render 側がやる
FACE = 272   # 顔の描画サイズ。縁の白リングに頭がかからない余白を残す


def _bgr_to_rgb(bgr: str) -> str:
    """キャラ定義の色（ASS向けのBGR並び）を ffmpeg の RGB 表記に直す。"""
    return bgr[4:6] + bgr[2:4] + bgr[0:2]


def _image_urls() -> dict:
    """素材ページを読んで {ファイル名: 画像URL(s400)} を集める。"""
    urls = {}
    for page in PAGES.values():
        try:
            html = urllib.request.urlopen(page).read().decode("utf-8", "ignore")
        except OSError as e:
            raise PipelineError(f"いらすとやのページを取得できません: {page}（{e}）")
        for full, name in re.findall(
            r'(https://blogger\.googleusercontent\.com/img/[^"]+?/s\d+/([a-z_0-9]+\.png))', html
        ):
            urls.setdefault(name, re.sub(r"/s\d+/", "/s400/", full))
    return urls


def main() -> None:
    unknown = [k for k in PICKS if k not in CHARACTERS]
    if unknown:
        raise PipelineError(f"キャラ定義に居ないキーがあります: {', '.join(unknown)}")

    os.makedirs(ICONS_DIR, exist_ok=True)
    todo = {k: v for k, v in PICKS.items()
            if not os.path.exists(os.path.join(ICONS_DIR, f"{k}.png"))}
    if not todo:
        print("全アイコンが揃っています（上書きしたいものは消してから再実行）")
        return

    urls = _image_urls()
    for key, src in todo.items():
        if src not in urls:
            raise PipelineError(f"素材が見つかりません: {src}（いらすとや側の構成が変わった可能性）")
        raw = os.path.join(ICONS_DIR, f".raw_{src}")
        urllib.request.urlretrieve(urls[src], raw)
        out = os.path.join(ICONS_DIR, f"{key}.png")
        rgb = _bgr_to_rgb(CHARACTERS[key]["color_bgr"])
        fc = (f"[1:v]scale={FACE}:{FACE}:force_original_aspect_ratio=decrease[i];"
              "[0:v][i]overlay=(W-w)/2:(H-h)/2:format=auto")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", f"color=c=0x{rgb}:s={SIZE}x{SIZE}",
             "-i", raw, "-filter_complex", fc, "-frames:v", "1", out],
            check=True,
        )
        os.remove(raw)
        with open(os.path.join(ICONS_DIR, f"{key}.txt"), "w", encoding="utf-8") as f:
            f.write("アイコン: いらすとや\n")
        print(f"{key} <- {src}")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
