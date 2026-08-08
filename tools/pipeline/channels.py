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
    "trivia": "2026-08-08に廃止（本人決定・#192）。YouTube「ゾウの鼻毛」ごと畳み、"
              "meme / heisei / showa の3本立てに集約",
}

# 台本・音声・字幕・立ち絵で共通に使うキャラクター定義。
# voicevox_speaker はVOICEVOXのスタイルID。色はASSのBGR並び。
#
# 構成はロンロンの天秤（51.2万・このジャンルの最大手）に合わせている（#133）。
# 同チャンネルは**レス主ごとに声を変える**ことで、1本の中に複数の人物を登場させている。
# 2人固定だと寸劇にしたときに登場人物を演じ分けられない（docs/02 2章）。
#
# **キャラの声は差し替えない**（声＝キャラの同一性。docs/09 4-2）。
# クレジット表記はVOICEVOXの利用条件なので、使った話者ぶんが credits.txt に出る。
CHARACTERS = {
    # ---- 主役2人（全チャンネル共通の軸） ----
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
        "speech": (
            "一人称は「わたくし」。上品な「〜わよ」「〜かしら」。"
            "数字・固有名詞・当時の文脈を織り込み、断定して言い切る。"
            "相手の脱線には乗らず、話を戻すか、冷たく一言で返す"
        ),
    },

    # ---- 脇役（レス民・通行人・別の登場人物を演じ分けるための声） ----
    # 立ち絵は無くてよい（字幕の色で誰の発言かを示す。docs/09 4-8）
    "tsumugi": {
        "name": "春日部つむぎ",
        "voicevox_speaker": 8,
        "credit": "VOICEVOX:春日部つむぎ",
        "color_bgr": "5CE6FF",  # 明るい黄
        "speech": "ギャル寄りの若い女子。「〜じゃん」「まじで」。軽くて遠慮がない",
    },
    "ritsu": {
        "name": "波音リツ",
        "voicevox_speaker": 9,
        "credit": "VOICEVOX:波音リツ",
        "color_bgr": "6B6BFF",  # 赤みのある紫
        "speech": "低めの声で不機嫌ぎみ。短く突き放す。「知らん」「どうでもいい」",
    },
    "hau": {
        "name": "雨晴はう",
        "voicevox_speaker": 10,
        "credit": "VOICEVOX:雨晴はう",
        "color_bgr": "A0FFD0",  # 淡い緑
        "speech": "素直で優しい。心配する側に回る。「大丈夫ですか？」",
    },
    "takehiro": {
        "name": "玄野武宏",
        "voicevox_speaker": 11,
        "credit": "VOICEVOX:玄野武宏",
        "color_bgr": "4C9CFF",  # オレンジ寄り
        "speech": "普通の男性。ぶっきらぼうで率直。「いや無理だろ」「知ってた」",
    },
    "kotaro": {
        "name": "白上虎太郎",
        "voicevox_speaker": 12,
        "credit": "VOICEVOX:白上虎太郎",
        "color_bgr": "70D0FF",  # 山吹
        "speech": "元気な少年。テンションが高く、勢いで喋る。「うおおお」「やば！」",
    },
    # 青山龍星（13/81）は廃止（2026-08-06 本人決定・#160）。
    # 音声規約に「企業・個人事業主は収益の有無にかかわらず事前申請」の特別条項があり、
    # 名義を変えた瞬間に申請漏れになる。熱血役は玄野武宏のツンギレで代替する
    "takehiro_nekketsu": {
        "name": "玄野武宏",           # 同一キャラの別スタイル（クレジットは1つ）
        "voicevox_speaker": 40,      # ツンギレ
        "credit": "VOICEVOX:玄野武宏",
        "color_bgr": "2020FF",       # 真紅
        "speech": "とにかく熱い。叫ぶ。「いいか」「やってみせろ」。昭和の熱血漢",
    },
    "himari": {
        "name": "冥鳴ひまり",
        "voicevox_speaker": 14,
        "credit": "VOICEVOX:冥鳴ひまり",
        "color_bgr": "C8A0FF",  # 藤色
        "speech": "落ち着いた女性。冷静に事実を置く。ナレーション向き",
    },
    "sora": {
        "name": "九州そら",
        "voicevox_speaker": 16,
        "credit": "VOICEVOX:九州そら",
        "color_bgr": "FFC060",  # 空色
        "speech": "しっかり者の年上女性。呆れながら面倒を見る。「もう、しょうがないわね」",
    },
    "mochiko": {
        "name": "もち子さん",
        "voicevox_speaker": 20,
        "credit": "VOICEVOX:もち子さん",
        "color_bgr": "B0C0FF",  # 薄桃
        "speech": "のんびりした女性。ずれた返しをする。場の空気を変える",
    },
    "shishio": {
        "name": "剣崎雌雄",
        "voicevox_speaker": 21,
        "credit": "VOICEVOX:剣崎雌雄",
        "color_bgr": "80FFFF",  # クリーム
        "speech": "理知的な男性。淡々と正論を置く。ツッコミの最終兵器",
    },
    "whitecul": {
        "name": "WhiteCUL",
        "voicevox_speaker": 23,
        "credit": "VOICEVOX:WhiteCUL",
        "color_bgr": "FFFFFF",  # 白
        "speech": "抑揚が控えめ。ナレーション・状況説明に向く",
    },
    "nurserobo": {
        "name": "ナースロボ＿タイプＴ",
        "voicevox_speaker": 47,
        "credit": "VOICEVOX:ナースロボ＿タイプＴ",
        "color_bgr": "D0FFB0",  # ミント
        "speech": "機械的で丁寧。無感情に事実を告げるので、内容とのギャップで笑いになる",
    },
}

# 主役2人。チャンネル設定の cast が空でもここに落ちる
MAIN_CAST = ("zundamon", "metan")


def extra_speakers() -> list:
    """脇役として使える話者キー。寸劇で登場人物を演じ分けるのに使う（#133）。"""
    return [k for k in CHARACTERS if k not in MAIN_CAST]


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
    # 2人固定を要求していたが、それが「ずんだもん・めたんの一問一答」の正体だった（#140）。
    # ロンロンは1本に何人でも出す。人数の上限はこちらで決めない
    if not cfg.get("cast"):
        raise PipelineError(f"{channel} の cast が空です（最低1人は要ります）。")
    return cfg


def available() -> list:
    """定義済みチャンネル名の一覧。"""
    if not os.path.isdir(CHANNELS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0] for f in os.listdir(CHANNELS_DIR) if f.endswith(".json")
    )


def cast_keys(cfg: dict) -> list:
    """配役のキャラキーを cast の並び順で返す。"""
    return [m["speaker"] for m in cfg["cast"]]
