#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""話者アイコンが全話者ぶん揃っているかを検品する（content/assets/icons/）。

    .venv/bin/python tools/fetch_icons.py

## なぜ「取得」ではなく「検品」なのか（#189・2026-08-07 本人決定）

アイコンは**90年代セル画風のAI生成人物顔で全話者統一**になった（経緯: いらすとや #154 →
雑多なAI生成 #179 → 絵柄バラバラの反省で一括統一 #189。docs/09 4-8）。
生成はCanvaのAI画像生成を対話的に使うため、BGM・SEのようにスクリプトで
再取得できない。このスクリプトの役目は「欠けたまま気づかずレンダリングする」のを
防ぐことだけにする。欠けていたら docs/09 4-8 のプロンプトテンプレで再生成する。

自動でいらすとやから取り直す旧実装は消した。残しておくと環境再構築のときに
これが走って、統一したはずの絵柄が silently いらすとやに巻き戻るため。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.channels import CHARACTERS
from tools.pipeline.common import ASSETS_DIR, PipelineError

ICONS_DIR = os.path.join(ASSETS_DIR, "icons")


def main() -> None:
    missing_png = [k for k in CHARACTERS
                   if not os.path.exists(os.path.join(ICONS_DIR, f"{k}.png"))]
    # クレジットは credits.txt に出す素材規約情報なので、対の .txt が無いのも欠けと扱う
    missing_txt = [k for k in CHARACTERS
                   if not os.path.exists(os.path.join(ICONS_DIR, f"{k}.txt"))]
    if missing_png or missing_txt:
        lines = []
        if missing_png:
            lines.append(f"アイコンが欠けています: {', '.join(missing_png)}")
        if missing_txt:
            lines.append(f"クレジット(.txt)が欠けています: {', '.join(missing_txt)}")
        lines.append("docs/09 4-8 のプロンプトテンプレでCanvaから再生成してください"
                     "（90年代セル画風・800x800・背景はキャラ色）。")
        raise PipelineError("\n".join(lines))
    print(f"全{len(CHARACTERS)}話者のアイコンとクレジットが揃っています")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
