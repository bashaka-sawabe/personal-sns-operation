#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""話者アイコンを原本（assets/icons/）から復元し、全話者ぶん揃っているか検品する。

    .venv/bin/python tools/fetch_icons.py

## アイコンの正体（#204・2026-08-08 本人決定）

アイコンは**本人支給のAI生成人物顔10枚**（ChatGPT生成）で、キャラ盤面の10人と
1対1で対応する（経緯: いらすとや #154 → 雑多なAI生成 #179 → Canva一括統一 #189 →
本人支給の10枚に差し替え #204。docs/09 4-8）。

画像はネットから再取得できない（支給元のChatGPT共有リンクは失効しうる）ため、
**原本を git 管理下の assets/icons/ に置き、ここから復元する**。
レンダが読むのは content/assets/icons/（git外）で、欠けていれば原本からコピーする。
新しいアイコンに差し替えるときは assets/icons/ の原本を置き換えること。
"""
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.channels import CHARACTERS
from tools.pipeline.common import ASSETS_DIR, ROOT, PipelineError

ICONS_DIR = os.path.join(ASSETS_DIR, "icons")
# 原本。git管理下なので clone すれば必ずある
SOURCE_DIR = os.path.join(ROOT, "assets", "icons")


def main() -> None:
    os.makedirs(ICONS_DIR, exist_ok=True)
    restored = []
    for key in CHARACTERS:
        png = os.path.join(ICONS_DIR, f"{key}.png")
        source = os.path.join(SOURCE_DIR, f"{key}.png")
        if not os.path.exists(png) and os.path.exists(source):
            shutil.copyfile(source, png)
            restored.append(key)
        # 表情差分（<key>_<表情>.png・#311）も原本から復元する。
        # 差分はあるキャラにしか無いので、欠けていてもエラーにしない
        # （render は基本アイコンに落ちる）
        for src in glob.glob(os.path.join(SOURCE_DIR, f"{key}_*.png")):
            dst = os.path.join(ICONS_DIR, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copyfile(src, dst)
                restored.append(os.path.splitext(os.path.basename(src))[0])
        # アイコンにクレジットの .txt は置かない（#222）。ChatGPT生成画像は
        # OpenAI利用規約上ユーザーの所有物で、表記義務が無い。BGM・効果音のように
        # 規約が表記を求める素材だけがサイドカーを持つ
        stale = os.path.join(ICONS_DIR, f"{key}.txt")
        if os.path.exists(stale):
            os.remove(stale)
    if restored:
        print(f"原本から復元: {', '.join(restored)}")

    missing = [k for k in CHARACTERS
               if not os.path.exists(os.path.join(ICONS_DIR, f"{k}.png"))]
    if missing:
        raise PipelineError(
            f"アイコンが欠けています: {', '.join(missing)}\n"
            "  原本（assets/icons/<キャラ>.png）にも無い話者です。"
            "本人に画像を用意してもらい、assets/icons/ に置いてください。"
        )
    print(f"全{len(CHARACTERS)}話者のアイコンが揃っています")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
