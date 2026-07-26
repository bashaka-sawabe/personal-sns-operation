#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ジャンル × テーマ → 台本JSON（Claude API）。

2週間テストの生産ラインの入口。ジャンルを固定せず、3ジャンルを同じ型で回して
数字で勝ちジャンルを決める（docs/07_ロードマップ.md）。

APIキーが無い場合は --offline のテンプレ台本にフォールバックし、
後段（画像・音声・合成・投稿）の検証だけ先に通せるようにしてある。
"""
import json
import os

from .common import PipelineError, read_secret

# 台本の型。フック（0〜3秒）→ 本編3点 → 締め、が最も完走率が安定する構成
SCENE_COUNT = 5

# シーン4は一次情報の置き場（docs/05 1章）。ここが空だと類型D（AI量産）と
# 区別がつかず、2026年のプラットフォーム規制の直撃を受ける（docs/01 4章）。
FIRST_HAND_SCENE = 4

# 本人が埋めるまで残る目印。この文字列が残っている台本は投稿できない
PLACEHOLDER = "【要実体験】"

# 一次情報が原理的に乗らないジャンルはスロットを求めない
GENRES_WITHOUT_FIRST_HAND = {"trivia"}

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "社内管理用のタイトル。40字以内"},
        "first_hand": {
            "type": "string",
            "description": (
                "本人が埋めるべき一次情報の指示。"
                "「実際に払った月額の社会保険料」のように、"
                "何を入れるかだけを書く。具体的な金額・数値・体験は書かない。"
                "一次情報が不要なジャンルでは空文字。"
            ),
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "caption": {
                        "type": "string",
                        "description": "画面に焼く字幕。20字以内。体言止め中心で短く",
                    },
                    "narration": {
                        "type": "string",
                        "description": "読み上げる文。40字以内。話し言葉",
                    },
                    "image_prompt": {
                        "type": "string",
                        "description": "背景画像の生成プロンプト。英語。人物の顔は入れない",
                    },
                },
                "required": ["caption", "narration", "image_prompt"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string", "description": "投稿キャプション。120字以内"},
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "先頭に#を含むタグ。5個",
        },
    },
    "required": ["title", "first_hand", "scenes", "caption", "hashtags"],
    "additionalProperties": False,
}

SYSTEM = f"""あなたは日本語のショート動画（Instagram Reels / TikTok）の構成作家です。
ナレーション付きの情報系ショートの台本を作ります。

制約:
- シーンはちょうど{SCENE_COUNT}個。
- シーン1は「フック」。3秒で指を止めさせる。断定・数字・意外性のどれかを必ず使う。
- シーン2〜4は中身。1シーン1メッセージ。抽象論を書かず、具体・数値・手順で書く。
- シーン5は締め。保存かコメントを促す一言を入れる（露骨な「保存してね」は避ける）。
- 字幕(caption)は20字以内。読み切れない長さにしない。
- ナレーション(narration)は話し言葉。書き言葉にしない。
- image_promptは英語。抽象的・象徴的な背景に留め、人物の顔・文字・ロゴは入れない。
- 誇張・断定しすぎ・医療や投資の助言に踏み込む表現は避ける。
- 制度・税率・金額に触れるときは「2026年時点」と分かる書き方にし、断定しない。

一次情報について（最重要）:
発信者は一人社長です。この動画の価値は「実際に会社を回している人しか言えないこと」
にあります。シーン{FIRST_HAND_SCENE}を、その一次情報の置き場にしてください。

ただし、**あなたは発信者の実体験を知りません。絶対に作らないでください。**
- 具体的な金額・年数・社名・体験談を、それらしく書いてはいけません。
- シーン{FIRST_HAND_SCENE}のnarrationとcaptionには、必ず「{PLACEHOLDER}」という
  文字列をそのまま含め、その後ろに「何を入れるべきか」だけを書いてください。
  例: 「{PLACEHOLDER}実際に毎月引かれている社会保険料の額」
- first_handには、本人が何を調べて埋めればよいかを1文で書いてください。
  ここにも具体的な数値を書いてはいけません。

嘘の数字は、その1本が滑るだけでなく発信者の信用ごと壊します。
分からないことは「分からないので本人が埋める」と示すのが正解です。"""

SYSTEM_NO_FIRST_HAND = SYSTEM.split("一次情報について（最重要）:")[0] + """一次情報について:
このジャンルは一般に知られた事実を扱うため、発信者個人の体験は不要です。
first_handは空文字にしてください。
ただし、**発信者の実体験や具体的な金額を勝手に作ってはいけません。**"""


def needs_first_hand(genre: str) -> bool:
    return genre not in GENRES_WITHOUT_FIRST_HAND


def unfilled(script: dict) -> list:
    """本人がまだ埋めていない箇所を返す。空なら投稿できる状態。"""
    spots = []
    if PLACEHOLDER in (script.get("first_hand") or ""):
        spots.append("first_hand")
    for i, scene in enumerate(script.get("scenes", []), 1):
        for key in ("caption", "narration"):
            if PLACEHOLDER in (scene.get(key) or ""):
                spots.append(f"シーン{i}の{key}")
    return spots


def _fallback(genre: str, theme: str) -> dict:
    """APIキーが無いときのテンプレ。パイプラインの疎通確認用で、投稿には使わない。"""
    return {
        "title": f"[offline] {theme}",
        "first_hand": "",
        "scenes": [
            {
                "caption": f"{theme}",
                "narration": f"{theme}について、知らないと損することがあります。",
                "image_prompt": "abstract dark gradient background, minimal",
            },
            *[
                {
                    "caption": f"ポイント{i}",
                    "narration": f"{i}つ目のポイントです。ここに具体的な中身が入ります。",
                    "image_prompt": "abstract dark gradient background, minimal",
                }
                for i in (1, 2, 3)
            ],
            {
                "caption": "覚えておくと得",
                "narration": "気になったところがあれば、コメントで教えてください。",
                "image_prompt": "abstract dark gradient background, minimal",
            },
        ],
        "caption": f"[offline] {genre} / {theme}",
        "hashtags": ["#offline", "#テスト", "#パイプライン", "#検証", "#ショート動画"],
    }


def generate(genre: str, theme: str, offline: bool = False) -> dict:
    """台本JSONを返す。offline=True かAPIキー未設定ならテンプレを返す。"""
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if offline or not api_key:
        if not offline:
            print("  ANTHROPIC_API_KEY 未設定のためテンプレ台本を使います（--offline 相当）")
        return _fallback(genre, theme)

    try:
        import anthropic
    except ImportError:
        raise PipelineError(
            "anthropic SDK がありません。`pip3 install anthropic` を実行するか "
            "--offline を付けてください。"
        ) from None

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=4000,
        system=SYSTEM if needs_first_hand(genre) else SYSTEM_NO_FIRST_HAND,
        output_config={"format": {"type": "json_schema", "schema": SCRIPT_SCHEMA}},
        messages=[{
            "role": "user",
            "content": f"ジャンル: {genre}\nテーマ: {theme}\n\nこのテーマでショート動画の台本を作ってください。",
        }],
    )
    if response.stop_reason == "refusal":
        raise PipelineError(f"生成を拒否されました（テーマを見直してください）: {theme}")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise PipelineError("台本が空で返りました。テーマを変えて再実行してください。")
    return json.loads(text)


def save(script: dict, script_id: str, scripts_dir: str) -> str:
    script = {"id": script_id, **script}
    path = os.path.join(scripts_dir, f"{script_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    return path


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
