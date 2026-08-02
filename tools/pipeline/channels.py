#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""チャンネル設定とキャラクター定義（v4: ペルソナ別3チャンネル）。

チャンネルごとの違い（想定視聴者・台本の型・配役・一次情報の要否）は
data/channels/<name>.json に置き、コードはそれを読むだけにする。
テーマや配役の調整のたびにコードを触らないための分離（docs/05 1章）。

キャラクターの声・字幕色・クレジット表記はコード側の定数にする。
クレジットは素材規約の利用条件（docs/08）なので、設定ファイルの書き換えで
消えてしまわない場所に置く。
"""
import json
import os

from .common import ROOT, PipelineError

CHANNELS_DIR = os.path.join(ROOT, "data", "channels")

# 廃止済みチャンネル。設定ファイルが無いのは事故ではなく本人決定であることを
# エラーメッセージで区別する（docs/00 変更履歴）
RETIRED = {
    "girls": "v6（2026-08-02）で廃止。恋愛・職場スレは転載自由ソースに少なかった",
    "biz": "v6（2026-08-02）で廃止。一次情報の本人作業がボトルネックだった",
    "f1": "2026-08-02に廃止。データが商用不可（jolpica=CC BY-NC-SA）・"
          "F1公式がLLM利用を禁止・映像なしの成功例が無い（docs/04 4章）",
}

# 台本・音声・字幕・立ち絵で共通に使うキャラクター定義。
# voicevox_speaker はVOICEVOXのスタイルID（ノーマル）。色はASSのBGR並び
CHARACTERS = {
    "zundamon": {
        "name": "ずんだもん",
        "voicevox_speaker": 3,
        "credit": "VOICEVOX:ずんだもん",
        "color_bgr": "4CE29C",  # ずんだ餅の黄緑
        # 語尾だけを指定すると「形容詞＋のだ」の相槌ばかりになり、94%が同じ形になった（#127）。
        # 語尾ではなく**返し方**を書く
        "speech": (
            "一人称は「ボク」。語尾は「〜のだ」系だが、**同じ形を続けない**。"
            "疑問（〜なのだ？）・驚愕（〜なのだ!?）・食い気味（待つのだ、それ〜）・"
            "言い切り（無理なのだ）・体言止め（は？ それ詐欺なのだ）を混ぜる。"
            "相槌ではなく**自分の考えを言う**役。たとえ話・自分語り・飛躍した結論・"
            "話の腰を折る質問で返す。素直に感心して終わらない"
        ),
    },
    "metan": {
        "name": "四国めたん",
        "voicevox_speaker": 2,
        "credit": "VOICEVOX:四国めたん",
        "color_bgr": "D09CFF",  # 桜ピンク
        # 情報を持っている側。ずんだもんより長く喋り、数字と固有名詞を出す（#127）
        "speech": (
            "一人称は「わたくし」。上品な「〜わよ」「〜かしら」。"
            "**情報を持っている側**なので、ずんだもんより明確に長く喋る（25〜35字）。"
            "数字・固有名詞・当時の文脈を必ず織り込み、断定して言い切る。"
            "ずんだもんの脱線には乗らず、話を戻すか、冷たく一言で返す"
        ),
    },
}


def load(channel: str) -> dict:
    """チャンネル設定を読む。cast の speaker はキャラ定義に居ることを保証する。"""
    if channel in RETIRED:
        raise PipelineError(
            f"チャンネル {channel} は廃止済みです（{RETIRED[channel]}）。\n"
            f"  使えるチャンネル: {', '.join(available()) or '（未定義）'}"
        )
    path = os.path.join(CHANNELS_DIR, f"{channel}.json")
    if not os.path.exists(path):
        raise PipelineError(
            f"チャンネル設定がありません: {os.path.relpath(path, ROOT)}\n"
            f"  使えるチャンネル: {', '.join(available()) or '（未定義）'}"
        )
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for member in cfg.get("cast", []):
        if member.get("speaker") not in CHARACTERS:
            raise PipelineError(
                f"{channel} の配役に未定義のキャラがいます: {member.get('speaker')}"
                f"（定義済み: {', '.join(CHARACTERS)}）"
            )
    if len(cfg.get("cast", [])) != 2:
        raise PipelineError(f"{channel} の cast は2人にしてください（掛け合いの前提）。")
    return cfg


def available() -> list:
    """定義済みチャンネル名の一覧。"""
    if not os.path.isdir(CHANNELS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0] for f in os.listdir(CHANNELS_DIR) if f.endswith(".json")
    )


def cast_keys(cfg: dict) -> list:
    """配役のキャラキーを cast の並び順で返す（立ち絵の左右配置にも使う）。"""
    return [m["speaker"] for m in cfg["cast"]]
