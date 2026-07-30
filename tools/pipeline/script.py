#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""チャンネル × テーマ → 掛け合い台本JSON（Claude API）。

v4はキャラ2人の会話で進める（docs/05 2章）。チャンネルごとの型・配役は
data/channels/<name>.json が持ち、ここではそれをプロンプトに織り込むだけにする。

APIキーが無い場合は --offline のテンプレ台本にフォールバックし、
後段（画像・音声・合成・投稿）の検証だけ先に通せるようにしてある。
"""
import json
import os

from .channels import CHARACTERS, cast_keys
from .common import PipelineError, read_secret

# 台本の型。フック（0〜3秒）→ 本編3点 → 締め、が最も完走率が安定する構成
SCENE_COUNT = 5

# シーン4は一次情報の置き場（docs/05 2章）。ここが空だと類型D（AI量産）と
# 区別がつかず、2026年のプラットフォーム規制の直撃を受ける（docs/01 4章）。
FIRST_HAND_SCENE = 4

# 本人が埋めるまで残る目印。この文字列が残っている台本は投稿できない
PLACEHOLDER = "【要実体験】"


def _schema(cfg: dict) -> dict:
    """台本のJSONスキーマ。話者はチャンネルの配役に限定する。"""
    speakers = cast_keys(cfg)
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "社内管理用のタイトル。40字以内"},
            "first_hand": {
                "type": "string",
                "description": (
                    "本人が埋めるべき一次情報の指示。"
                    "「独立1年目に実際に見落とした支払いと、その顛末」のように、"
                    "何を入れるかだけを書く。求めるのは体験・手順・判断・失敗談であり、"
                    "金額を求めてはいけない（実額は公開しない方針）。"
                    "具体的な金額・数値・体験は書かない。"
                    "一次情報が不要なチャンネルでは空文字。"
                ),
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "caption": {
                            "type": "string",
                            "description": "画面に焼く見出し。20字以内。体言止め中心で短く",
                        },
                        "dialogue": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "speaker": {"type": "string", "enum": speakers},
                                    "text": {
                                        "type": "string",
                                        "description": "セリフ。35字以内の話し言葉",
                                    },
                                },
                                "required": ["speaker", "text"],
                                "additionalProperties": False,
                            },
                            "description": "1シーン1〜3行の掛け合い",
                        },
                        "image_prompt": {
                            "type": "string",
                            "description": "背景画像の生成プロンプト。英語。人物の顔は入れない",
                        },
                    },
                    "required": ["caption", "dialogue", "image_prompt"],
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


def _system(cfg: dict) -> str:
    """チャンネル設定からシステムプロンプトを組み立てる。"""
    cast_lines = "\n".join(
        f"- {m['speaker']}（{CHARACTERS[m['speaker']]['name']}）: {m['role']}。"
        f"{CHARACTERS[m['speaker']]['speech']}"
        for m in cfg["cast"]
    )
    base = f"""あなたは日本語のショート動画（YouTube Shorts / Reels / TikTok）の構成作家です。
キャラクター2人の掛け合い（会話）で進む台本を作ります。

チャンネル: {cfg['name']} ／ 想定視聴者: {cfg['persona']}
この動画の型: {cfg['format']}

配役（speakerにはこのキーをそのまま使う）:
{cast_lines}

制約:
- シーンはちょうど{SCENE_COUNT}個。
- シーン1はフック。3秒で指を止めさせる。最初のセリフは悲鳴・疑問・意外な断定のどれか。
- 各シーンの dialogue は1〜3行。1行は35字以内の話し言葉。
- 会話として自然に繋がること。説明文の分担読みにしない（相槌・ツッコミ・感情を挟む）。
- キャラの口調を守る。2人の声の違いだけで誰のセリフか分かる書き方にする。
- 42歳の普通の会社員が見ても分かる言葉で書く。専門用語を裸で使わない（docs/05 1章の下限）。
- 見出し(caption)は20字以内。読み切れない長さにしない。
- シーン5は締め。保存かコメントを促す流れにする（露骨な「保存してね」は避ける）。
- image_promptは英語。抽象的・象徴的な背景に留め、人物の顔・文字・ロゴは入れない。
- 誇張・断定しすぎ・医療や投資の助言に踏み込む表現は避ける。
- 制度・税率・金額に触れるときは「2026年時点」と分かる書き方にし、断定しない。"""

    if cfg.get("first_hand"):
        base += f"""

一次情報について（最重要）:
このチャンネルの価値は「実際に会社を回している中の人しか言えないこと」にあります。
シーン{FIRST_HAND_SCENE}を、その一次情報の置き場にしてください。

ただし、**あなたは中の人の実体験を知りません。絶対に作らないでください。**
- 具体的な金額・年数・社名・体験談を、それらしく書いてはいけません。
- 一次情報として求めるのは**実際にやった体験・手順・判断・失敗談**です。
  会社の具体的な金額（実額）は公開しない方針のため、
  金額を埋めさせる指示・セリフにしてはいけません（docs/04 8章）。
- シーン{FIRST_HAND_SCENE}の該当セリフと見出しには、必ず「{PLACEHOLDER}」という
  文字列をそのまま含め、その後ろに「何を入れるべきか」だけを書いてください。
  例: 「{PLACEHOLDER}独立1年目に実際に見落とした支払いと、その顛末」
- first_handには、本人が何を思い出して埋めればよいかを1文で書いてください。
  ここにも具体的な数値を書いてはいけません。

嘘の体験談は、その1本が滑るだけでなくチャンネルの信用ごと壊します。
分からないことは「分からないので本人が埋める」と示すのが正解です。"""
    else:
        base += """

一次情報について:
このチャンネルでは発信者個人の体験談は必須ではありません。first_handは空文字にしてください。
ただし、**発信者の実体験や具体的な金額を勝手に作ってはいけません。**"""

    if cfg.get("nakanohito"):
        base += """

「中の人」への言及（本人への橋。docs/04 7章）:
教える側のセリフに1本あたりちょうど1回、「このチャンネルの中の人（一人社長）」への
言及を入れてください。例: 「このチャンネルの中の人は、実際は…」。
2回以上入れると宣伝臭くなるので、必ず1回だけにします。"""
    return base


def validate(script: dict) -> None:
    """掛け合い形式（v4）の台本であることを確かめる。旧形式は明確に落とす。"""
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise PipelineError("台本に scenes がありません。")
    for i, scene in enumerate(scenes, 1):
        if "narration" in scene and "dialogue" not in scene:
            raise PipelineError(
                "旧形式（ナレーション形式）の台本です。v4は掛け合い形式のみ対応しています。\n"
                "  make_video.py --channel <ch> --theme ... で台本を作り直してください。"
            )
        lines = scene.get("dialogue")
        if not isinstance(lines, list) or not lines:
            raise PipelineError(f"シーン{i}に dialogue がありません（掛け合い形式が必要です）。")
        for line in lines:
            if line.get("speaker") not in CHARACTERS:
                raise PipelineError(
                    f"シーン{i}に未定義の話者がいます: {line.get('speaker')}"
                    f"（定義済み: {', '.join(CHARACTERS)}）"
                )
            if not (line.get("text") or "").strip():
                raise PipelineError(f"シーン{i}に空のセリフがあります。")


def unfilled(script: dict) -> list:
    """本人がまだ埋めていない箇所を返す。空なら投稿できる状態。"""
    spots = []
    if PLACEHOLDER in (script.get("first_hand") or ""):
        spots.append("first_hand")
    for i, scene in enumerate(script.get("scenes", []), 1):
        if PLACEHOLDER in (scene.get("caption") or ""):
            spots.append(f"シーン{i}のcaption")
        for j, line in enumerate(scene.get("dialogue", []), 1):
            if PLACEHOLDER in (line.get("text") or ""):
                spots.append(f"シーン{i}のセリフ{j}")
    return spots


def _fallback(cfg: dict, theme: str) -> dict:
    """APIキーが無いときのテンプレ。パイプラインの疎通確認用で、投稿には使わない。"""
    a, b = cast_keys(cfg)  # a=教える側/先輩, b=持ち込む側（cast の並び順）
    return {
        "title": f"[offline] {theme}",
        "first_hand": "",
        "scenes": [
            {
                "caption": f"{theme}",
                "dialogue": [
                    {"speaker": b, "text": f"{theme}って、どういうことなのだ？"},
                    {"speaker": a, "text": "いい質問ね。順番に見ていくわよ。"},
                ],
                "image_prompt": "abstract dark gradient background, minimal",
            },
            *[
                {
                    "caption": f"ポイント{i}",
                    "dialogue": [
                        {"speaker": a, "text": f"{i}つ目のポイントはこれよ。"},
                        {"speaker": b, "text": "なるほどなのだ。"},
                    ],
                    "image_prompt": "abstract dark gradient background, minimal",
                }
                for i in (1, 2, 3)
            ],
            {
                "caption": "覚えておくと得",
                "dialogue": [
                    {"speaker": a, "text": "気になったらコメントで教えてね。"},
                    {"speaker": b, "text": "ボクも覚えたのだ！"},
                ],
                "image_prompt": "abstract dark gradient background, minimal",
            },
        ],
        "caption": f"[offline] {cfg['name']} / {theme}",
        "hashtags": ["#offline", "#テスト", "#パイプライン", "#検証", "#ショート動画"],
    }


def generate(cfg: dict, theme: str, offline: bool = False) -> dict:
    """台本JSONを返す。offline=True かAPIキー未設定ならテンプレを返す。"""
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if offline or not api_key:
        if not offline:
            print("  ANTHROPIC_API_KEY 未設定のためテンプレ台本を使います（--offline 相当）")
        return _fallback(cfg, theme)

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
        system=_system(cfg),
        output_config={"format": {"type": "json_schema", "schema": _schema(cfg)}},
        messages=[{
            "role": "user",
            "content": f"チャンネル: {cfg['name']}\nテーマ: {theme}\n\n"
                       "このテーマで掛け合いショート動画の台本を作ってください。",
        }],
    )
    if response.stop_reason == "refusal":
        raise PipelineError(f"生成を拒否されました（テーマを見直してください）: {theme}")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise PipelineError("台本が空で返りました。テーマを変えて再実行してください。")
    data = json.loads(text)
    validate(data)
    return data


def save(script: dict, script_id: str, scripts_dir: str) -> str:
    script = {"id": script_id, **script}
    path = os.path.join(scripts_dir, f"{script_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    return path


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
