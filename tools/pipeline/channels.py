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

    # ---- jiji「東京だいたい銀行」専属キャスト（v9.1・#305/#310） ----
    # 実在ドラマの全力もじりパロディ（docs/00 v9.1）。本名・番組名は出さない。
    # `channel` を持つキャラはそのチャンネル専属で extra_speakers() から外れる。
    # 既存盤面と声を共有しないのは、声がキャラの同一性だから（docs/09 4-2）——
    # 同じ声が別チャンネルで別人を演じると、どちらのキャラも壊れる。
    # speed_scale は #203 と同じ方法で実測した校正値（2026-08-17。テスト文の
    # 合成時間を既存話者の補正後中央値10.06秒に揃える係数）。
    # 全声とも規約はエンジンの /speaker_info で確認済み: 商用可・クレジットのみ。
    # ただし青山龍星のみ「**企業が携わる形**の利用は事前確認が要る」条項がある。
    # 運用は個人名義なので現状は可だが、Phase 2 で会社名と接続する前に
    # ずんだもん立ち絵と同様の許諾再確認が必須（docs/00 素材規約）。
    "banzawa": {
        "channel": "jiji",
        "speed_scale": 0.94,
        "name": "盤沢",
        "voicevox_speaker": 13,  # 青山龍星ノーマル
        "credit": "VOICEVOX:青山龍星",
        "color_bgr": "D9904A",  # 銀行ブルー
        "speech": (
            "東京だいたい銀行の融資課次長。普段は低く静かな敬語で、理不尽の核心に"
            "触れた瞬間だけ語気が二段階跳ね上がる。決め台詞「◯◯したなら——"
            "◯◯返してもらう」は1本に1回だけ、ネタに合わせて変形して使う。"
            "倍・利息・複利など銀行の語彙で報復を宣言する"
        ),
    },
    "owada": {
        "channel": "jiji",
        "speed_scale": 0.8,
        "name": "尾和田",
        "voicevox_speaker": 51,  # †聖騎士 紅桜†
        "credit": "VOICEVOX:†聖騎士 紅桜†",
        "color_bgr": "C85B9A",  # 常務の紫
        "speech": (
            "元・敵の常務。芝居がかった慇懃な口調で、まず全力で擁護してから"
            "同じ論法で梯子を外す詭弁の天才。「施し」「恩」の語彙が好物で、"
            "追い詰められたときの土下座は誰よりも美しい。"
            "名前が「終わった」に聞こえることに本人だけ気づいていない"
        ),
    },
    "todori": {
        "channel": "jiji",
        "speed_scale": 1.3,
        "name": "頭取",
        "voicevox_speaker": 42,  # ちび式じい
        "credit": "VOICEVOX:ちび式じい",
        "color_bgr": "30A0C8",  # 金茶
        "speech": (
            "名前が最後まで出ない頭取。ゆっくり短く穏やかに、固有名詞を言わずに話す。"
            "湯呑みを置いてから喋り、説教ではなく観察で議論を収める。"
            "「……◯◯だけで、偉い」"
        ),
    },
    "gondo": {
        "channel": "jiji",
        "speed_scale": 0.83,
        "name": "権藤",
        "voicevox_speaker": 73,  # 満別花丸ボーイ
        "credit": "VOICEVOX:満別花丸",
        "color_bgr": "6FBF7F",  # 胃薬の緑
        "speech": (
            "出向経験のある胃痛持ちの同期。気弱で腰が低いが、経験者として急に"
            "生々しい実話を置く。「胃が……」「うちの行でもあった」。"
            "視聴者と同じ目線で驚き、傷つき、それでも出社する"
        ),
    },
    "tomari": {
        "channel": "jiji",
        "speed_scale": 1.09,
        "name": "泊",
        "voicevox_speaker": 100,  # 黒沢冴白
        "credit": "VOICEVOX:黒沢冴白",
        "color_bgr": "E8C855",  # 明るい水色
        "speech": (
            "情報通の同期で進行役。軽やかで社交的、秘密を共有するときだけ声を落とす。"
            "「ここだけの話だけど」で毎回ネタを持ち込み、置いたら一歩引いて眺める"
        ),
    },
    "shirosaki": {
        "channel": "jiji",
        "speed_scale": 0.91,
        "name": "白崎",
        "voicevox_speaker": 67,  # 栗田まろん
        "credit": "VOICEVOX:栗田まろん",
        "color_bgr": "C8A0E8",  # ピンクゴールド
        "speech": (
            "検査部門のオネエ口調の検査官。語尾を引き伸ばして甘く入り、"
            "数字と規則で一気に詰める。決めは一音ずつ区切る「見・せ・な・さ・い」。"
            "感情ではなく帳簿で怒る"
        ),
    },
    "kobikado": {
        "channel": "jiji",
        "speed_scale": 1.06,
        "name": "小備門",
        "voicevox_speaker": 101,  # 離途シリアス
        "credit": "VOICEVOX:離途",
        "color_bgr": "5555E8",  # 法廷の赤
        "speech": (
            "顧問の毒舌弁護士。超早口・慇懃な罵倒・金への執着を隠さない。"
            "「はい出た◯◯」「読みました？」と無知を煽ってから、"
            "正論を機関銃のように浴びせる。綺麗事と感動の空気が大嫌い"
        ),
    },
    "nogi": {
        "channel": "jiji",
        "speed_scale": 0.93,
        "name": "野木",
        "voicevox_speaker": 52,  # 雀松朱司
        "credit": "VOICEVOX:雀松朱司",
        "color_bgr": "A8A8A8",  # 灰
        "speech": (
            "素性不明の無口な男。報告書のように簡潔で、感情を見せず最小の語数で"
            "本質だけ言う。長台詞は禁止（1シーン1文まで）。"
            "「……」から始まる一言で議論を終わらせる"
        ),
    },
}

# 主役2人。チャンネル設定の cast が空でもここに落ちる
MAIN_CAST = ("zundamon", "metan")


def extra_speakers() -> list:
    """脇役として使える話者キー。寸劇で登場人物を演じ分けるのに使う（#133）。

    `channel` を持つ専属キャラは除く。jiji の銀行員が meme のスレに湧いたり、
    その逆が起きたりすると、声とキャラの対応が崩れる（docs/09 4-2）。
    """
    return [k for k, v in CHARACTERS.items()
            if k not in MAIN_CAST and not v.get("channel")]


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
