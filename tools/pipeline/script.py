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

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "社内管理用のタイトル。40字以内"},
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
    "required": ["title", "scenes", "caption", "hashtags"],
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
- 誇張・断定しすぎ・医療や投資の助言に踏み込む表現は避ける。"""


def _fallback(genre: str, theme: str) -> dict:
    """APIキーが無いときのテンプレ。パイプラインの疎通確認用で、投稿には使わない。"""
    return {
        "title": f"[offline] {theme}",
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
        system=SYSTEM,
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
