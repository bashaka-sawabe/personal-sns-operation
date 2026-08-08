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
# speed_scale は話者ごとの素の話速のばらつきを打ち消す補正（#203）。
# VOICEVOXのスタイルは素のテンポが最大1.4倍違い、そのまま使うと
# 「めっちゃゆっくり喋るやつ」が出る（本人指摘 2026-08-08。最遅は九州そら）。
# 同一テスト文の合成時間を実測し、全話者の中央値に揃う係数を入れた
# （±3%以内は省略＝1.0）。チャンネルの style.speed はこの上に掛かる。
#
# 構成はロンロンの天秤（51.2万・このジャンルの最大手）に合わせている（#133）。
# 同チャンネルは**レス主ごとに声を変える**ことで、1本の中に複数の人物を登場させている。
# 2人固定だと寸劇にしたときに登場人物を演じ分けられない（docs/02 2章）。
#
# **キャラの声は差し替えない**（声＝キャラの同一性。docs/09 4-2）。
# クレジット表記はVOICEVOXの利用条件なので、使った話者ぶんが credits.txt に出る。
CHARACTERS = {
    # キャラ盤面は本人指定の10人（2026-08-08・#204）。アイコンは本人支給のAI生成10枚
    # （content/assets/icons/）と1対1で対応し、声の性別と絵の性別を一致させている。
    # 旧盤面の春日部つむぎ・雨晴はう・もち子さん・WhiteCUL・ナースロボは
    # 対応する絵が無いため退役（アイコン無しの話者は画面に出せない。docs/09 4-8）。
    #
    # ---- 主役2人（全チャンネル共通の軸） ----
    "zundamon": {
        "speed_scale": 1.11,
        "name": "ずんだもん",
        "voicevox_speaker": 3,
        "credit": "VOICEVOX:ずんだもん",
        "color_bgr": "4CE29C",  # ずんだ餅の黄緑
        # 語尾だけを指定すると「形容詞＋のだ」の相槌ばかりになり、94%が同じ形になった（#127）。
        # 語尾ではなく**返し方**を書く
        "speech": (
            "アホな青年。真剣なのに根本がズレている。一人称は「ボク」、"
            "語尾は「〜のだ」系だが**同じ形を続けない**。"
            "疑問（〜なのだ？）・驚愕（〜なのだ!?）・食い気味（待つのだ、それ〜）・"
            "言い切り（無理なのだ）・体言止め（は？ それ詐欺なのだ）を混ぜる。"
            "たとえ話・自分語り・飛躍した結論・話の腰を折る質問で返す。"
            "本人だけは自分を賢いと思っている"
        ),
    },
    "metan": {
        "speed_scale": 0.97,
        "name": "四国めたん",
        "voicevox_speaker": 2,
        "credit": "VOICEVOX:四国めたん",
        "color_bgr": "D09CFF",  # 桜ピンク
        "speech": (
            "金髪ツインテールのツンデレ。一人称は「わたくし」、上品な「〜わよ」「〜かしら」。"
            "数字・固有名詞を織り込んで断定して言い切るが、褒めるときだけ"
            "「べ、別に〜じゃないわよ」と照れて素直になれない。"
            "相手の脱線には乗らず、冷たく一言で返す"
        ),
    },

    # ---- 脇役（レス民・通行人・別の登場人物を演じ分けるための声） ----
    "sora": {
        "speed_scale": 1.43,
        "name": "九州そら",
        "voicevox_speaker": 16,
        "credit": "VOICEVOX:九州そら",
        "color_bgr": "C060A0",  # 深い紫
        "speech": (
            "お姉さん系のしっとりS。落ち着いた低温で、余裕を崩さない。"
            "「あら、ダメな子ね」「もっと困った顔が見たいわ」。"
            "追い詰めるときほど優しい声になる"
        ),
    },
    "himari": {
        "name": "冥鳴ひまり",
        "voicevox_speaker": 14,
        "credit": "VOICEVOX:冥鳴ひまり",
        "color_bgr": "C8A0FF",  # 藤色
        "speech": (
            "価値観が普通の一般的な女性。この盤面で唯一の常識人で、視聴者の代弁者。"
            "「いや、普通に考えておかしいですよね」と冷静に事実を置く。"
            "周りの異常さに引きながらツッコむ"
        ),
    },
    "shishio": {
        "speed_scale": 0.96,
        "name": "剣崎雌雄",
        "voicevox_speaker": 21,
        "credit": "VOICEVOX:剣崎雌雄",
        "color_bgr": "80FFFF",  # クリーム
        "speech": (
            "ダンディーな変態紳士。理知的で言葉遣いは完璧に上品だが、"
            "こだわりの方向がどこかおかしい。「実にいい」「たまりませんな」。"
            "正論と変態性を同じ真顔で言う"
        ),
    },
    "takehiro_nekketsu": {
        "speed_scale": 0.86,
        "name": "玄野武宏",           # 同一キャラの別スタイル（クレジットは1つ）
        "voicevox_speaker": 40,      # ツンギレ
        "credit": "VOICEVOX:玄野武宏",
        "color_bgr": "2020FF",       # 真紅
        "speech": "熱血脳筋の角刈りサラリーマン。とにかく熱い。叫ぶ。「いいか」「やってみせろ」。理屈より根性",
    },
    "takehiro": {
        "speed_scale": 0.89,
        "name": "玄野武宏",           # ノーマル（ツンギレと同一キャラの別スタイル）
        "voicevox_speaker": 11,
        "credit": "VOICEVOX:玄野武宏",
        "color_bgr": "4C9CFF",  # オレンジ寄り
        "speech": (
            "アメリカのIT企業に勤めるギーク。淡々と即物的で、たまに英語が混ざる。"
            "「それ、仕様です」「スケールしないですね」「Doneです」。"
            "感情の起伏が薄いぶん、正確な指摘が刺さる"
        ),
    },
    "kotaro": {
        "name": "白上虎太郎",
        "voicevox_speaker": 12,
        "credit": "VOICEVOX:白上虎太郎",
        "color_bgr": "70D0FF",  # 山吹
        "speech": (
            "年中発情している、モテないけど行動力だけはある20代男性。"
            "テンションが高く、女性が出てくると即「結婚しよ」と口走って引かれる。"
            "フラれても3秒で立ち直る。悪気はない"
        ),
    },
    "ritsu": {
        "name": "波音リツ",
        "voicevox_speaker": 9,
        "credit": "VOICEVOX:波音リツ",
        "color_bgr": "6B6BFF",  # 赤みのある紫
        "speech": (
            "キレやすいおばさん。低い声で常に不機嫌、些細なことで沸点を超える。"
            "「はぁ!?」「ちょっとアンタ！」。キレていないときは短く突き放す。"
            "「知らん」「どうでもいい」"
        ),
    },
    # 麒ヶ島宗麟はVirVox Projectのキャラ。規約は商用可・クレジット表記のみで、
    # 青山龍星のような事前申請条項は無い（2026-08-08 規約確認済み・#204）
    "sorin": {
        "speed_scale": 0.88,
        "name": "麒ヶ島宗麟",
        "voicevox_speaker": 53,
        "credit": "VOICEVOX:麒ヶ島宗麟",
        "color_bgr": "5070B0",  # 渋い茶
        "speech": (
            "キレやすいおじさん。渋い低音で、正論を言っているうちに勝手にヒートアップする。"
            "「いい加減にしなさいよ！」「だからさっきからそう言ってるでしょう！」。"
            "キレた後に少しバツが悪そうにする"
        ),
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
