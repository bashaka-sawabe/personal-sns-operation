#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""効果音を効果音ラボから取得する（content/assets/se/）。

    .venv/bin/python tools/fetch_se.py

効果音は BGM・立ち絵と同じく git 管理外（content/assets/ は .gitignore）なので、
環境を作り直したときはこれで揃える。既にあるファイルは上書きしない。

効果音ラボの規約（https://soundeffect-lab.info/agreement/）:
- 個人・法人問わず**無料で商用利用可**。YouTubeの収益化も明示的に可
- **クレジット表記・報告・リンクは不要**（禁止ではなく任意）
- **Content ID への登録は禁止**。登録すると他の利用者の動画の収益に影響が出る
- 素材の再配布・AI学習データとしての利用は禁止

→ 再配布禁止なので、取得済みファイルをリポジトリに置かずここで取り直す設計にしている。

どの音をどのチャンネルで鳴らすかは data/channels/<ch>.json の style.se が決める。
ジャンルごとに最適な音が違う（docs/02 1章の実測）。
"""
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import ASSETS_DIR, PipelineError

SE_DIR = os.path.join(ASSETS_DIR, "se")
BASE = "https://soundeffect-lab.info/sound"

# (カテゴリ, 効果音ラボのファイル名, 保存名)。保存名は style.se から引かれる
SOUNDS = [
    # meme: 軽くて速い。テンポを削がない音を選ぶ
    ("anime", "sceneswitch1", "meme_switch"),        # 場面転換
    ("anime", "chan-chan1", "meme_ochi"),            # オチのズッコケ
    ("anime", "pico-pico-hammer1", "meme_tsukkomi"), # ツッコミ
    # showa: 種明かしのジャジャーン（旧triviaから引き継ぎ。showa.json の style.se が参照）
    ("anime", "jajean1", "showa_reveal"),
    # heisei: 和の転換音。懐古の空気に合わせる
    ("anime", "drum-japanese1", "heisei_don"),       # 和太鼓の転換
    ("anime", "roll-finish1", "heisei_shime"),       # 締め
]

# 素材ホストは Referer が無いと403を返す
_HEADERS = {"User-Agent": "personal-sns-operation/1.0 (content pipeline)"}

FETCH_INTERVAL = 1.5  # 相手サーバーへの負荷を抑える


def fetch(category: str, name: str, out: str) -> str | None:
    """1件取る。既にあれば None（上書きしない）。"""
    path = os.path.join(SE_DIR, f"{out}.mp3")
    if os.path.exists(path):
        return None
    req = urllib.request.Request(
        f"{BASE}/{category}/mp3/{name}.mp3",
        headers={**_HEADERS, "Referer": f"{BASE}/{category}/"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = res.read()
    except (urllib.error.URLError, OSError) as e:
        raise PipelineError(f"効果音の取得に失敗しました（{name}）: {e}") from None
    os.makedirs(SE_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main() -> None:
    got, skipped = 0, 0
    try:
        for i, (category, name, out) in enumerate(SOUNDS):
            if i:
                time.sleep(FETCH_INTERVAL)
            path = fetch(category, name, out)
            if path:
                print(f"  取得: {out}.mp3（{name}）")
                got += 1
            else:
                skipped += 1
    except PipelineError as e:
        sys.exit(f"エラー: {e}")
    print(f"{got}件を取得しました（既存 {skipped}件はそのまま）→ content/assets/se/")


if __name__ == "__main__":
    main()
