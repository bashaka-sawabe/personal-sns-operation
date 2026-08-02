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
                            "description": (
                                "シーン1のcaptionはスレタイとして全編画面上部に焼かれる。"
                                "動画全体を一言で言い切る20字以内。"
                                "シーン2以降のcaptionは画面には出ない構成メモ"
                            ),
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


def _thread_rules(cfg: dict) -> str:
    """引用スレから作るときの追加ルール（v5: オチ逆算。docs/05 2章）。"""
    base = f"""

ネタ元のスレについて（v5・最重要）:
ネタ元として実在のスレ（転載自由のおーぷん2ちゃんねる）が与えられます。
**0からの創作はしません。面白さはスレが持っています。あなたの仕事は翻案とテンポです。**
- まずスレ全体を読み、オチ（いちばん面白い展開・結末・ツッコミ）を一文で特定する。
- シーン{SCENE_COUNT}はそのオチで締める。オチのあとに解説やまとめを足さない（蛇足で台無しになる）。
- シーン1はスレタイの内容をフックにして、オチへの期待を作る。
- 本編（シーン2〜4）はスレの展開を時系列で刈り込み、レスの応酬を2人の掛け合いに翻案する。
  面白いレスの言い回しは活かしてよい（転載自由ソース）。ただしキャラの口調に直す。
- スレに無い展開・オチを創作しない。盛ってよいのは表現だけで、事実の水増しをしない。
- 実在の人物名・企業名・場所など特定につながる情報はぼかすか落とす。
- 誹謗中傷・差別的なレスは拾わない。"""

    if cfg["name"] != "meme":
        return base + """
- シーン1の caption はスレタイを20字以内に整えたもの（スレタイとして全編表示される）。"""

    # このジャンルで51.2万に達したチャンネルは掲示板の記号を全部捨てている（docs/02 2章）。
    # 従来型（www・【悲報】・レス番号を出す型）は直近2,000〜3,000回まで衰退した
    return base + f"""

**掲示板の記号を捨てる（このチャンネルで最も重要）:**
登録者51万のチャンネルはタイトルに `www` を0%、`【悲報】` を0%しか使っていません。
逆に、それらを使う従来型のチャンネルは再生数が2桁落ちています。
- **`w` `ｗ` `www` `【悲報】` `【朗報】` `ンゴ` `なんJ` を caption に書かない。**
  セリフの中でも「〜で草」のようななんJ語は使わない（キャラの口調で話す）。
- **レス番号・アンカー（>>1）・ID・名前欄に触れない。** 「1が」「イッチが」は可。
- 「スレ」「掲示板」という語は最小限に。**普通の話として語る**。

**caption（スレタイ）の型:**
`【4〜6字の名詞句】` + 内容を言い切る短文。**全体で16字以内**（【】を含む）。
画面では8字で折り返されるため、17字を超えると3行になって画面を占有します。**必ず2行以内**。
- 良い例（16字以内）: 「【ルールの盲点】無限に買える自販機」「【妙な説得力】上司の遅刻理由」
- 名詞句は状態を言い切るもの（盲点／説得力／大誤算／総ツッコミ／方向音痴／完全論破 など）。
- 後半は体言止めで短く。助詞を削れるだけ削る。
- **悪い例: 「【悲報】〜ｗｗｗ」「2chで話題の〜」**

**オチは「パワーワード1個」に集約する:**
このジャンルは**視聴者がコメント欄で復唱したくなる短い言葉**が1個あるかで決まります。
- シーン{SCENE_COUNT}に、スレの中で一番おかしい**短い固有の言い回し**を1つ置き、それで終わる。
- 説明を足さない。**言葉だけを残して終わる**のが正解。
- スレにそういう言葉が無ければ、一番おかしい一言をそのまま使う（創作はしない）。

**尺:**
この動画は**26秒前後**に収めます。セリフは全体で**12行以内・合計150字以内**。
1行は25字以内。テンポが命なので、説明的な行を入れない。"""


def _list_rules(cfg: dict, count: int) -> str:
    """「◯◯選」リスト形式のルール（heisei。docs/02 4章の実測）。

    掛け合い5シーンとは別の型。平成懐古で伸びているのは 2.5秒/ネタ の高速リストで、
    1本1ネタの情報提示型は185本出して1,000〜3,000回しか出ていない。
    """
    return f"""

**この動画は「◯◯選」のリスト形式です（掛け合い5シーンではありません）:**
裏取り済みのネタが複数与えられます。それを**次々に投げつける**構成にします。

- シーンは全部で{count + 2}個にします:
  - **シーン1 = 導入**。「◯◯年生まれ位が懐かしい△△{count}選」と宣言する（掛け合い2〜3行）
  - **シーン2〜{count + 1} = 各ネタ**。1シーン1ネタ。**セリフは1〜2行だけ**。
    めたんが名称を言い、ずんだもんが一言で反応する（または、めたんの1行だけ）
  - **シーン{count + 2} = 締め**（掛け合い2〜3行）
- **各ネタは極端に短く。1シーンのセリフ合計は16字以内**（2.5秒で読み切れる長さ）。
  実測ベンチマークは **2.5秒/ネタ** です。解説を入れると必ず超えます。
  良い例:「MD。33年で終了」「たまごっち1000万個」「プリクラは1995年」
  悪い例:「MDは1992年から33年続いたのよ」（長い）
  ずんだもんの反応は入れても**6字以内**（「早いのだ」「異常なのだ」）。省いてもよい。
- 各ネタの caption は**その品名・名称そのもの**（10字以内）。
- **与えられたネタ以外を足さない。** 年代・名称は与えられたものだけを使う。

**タイトル（title）と シーン1の caption:**
- 「◯◯年生まれ**位**が懐かしい△△{count}選」の型にする。
- **「位（くらい）」を必ず入れる。** 断定すると対象年代の人しか自分ごとにできませんが、
  「位」があると前後10年が自分の話として見られます（実測でこの型が最も伸びている）。
- シーン1の caption は16字以内（画面は8字で折り返すので2行まで）。
  例: 「平成生まれが懐かしい{count}選」

**締めの作り方:**
- 「今どうなったか」か「当時は当たり前だった」の落差で終わる。
- **視聴者が年齢を名乗りたくなる終わり方にする**（「何年生まれ？」と直接聞くのではなく、
  「あなたはどれを覚えている？」のように思い出を語らせる）。
- リストは全部を網羅しません。**入りきらないものがあるのが正解**
  （視聴者が「◯◯が無い」と補完コメントを打つ余地を残す）。"""


def _fact_rules(cfg: dict) -> str:
    """裏取り済みの事実から作るときの追加ルール（trivia / heisei。docs/05 2章）。

    同じ「事実＋裏取り」でも、triviaは知らない話の種明かし、heiseiは
    知っている話の掘り起こしで、フックの作り方が正反対になる（#113）。
    """
    common = """

ネタ元の事実について（最重要）:
裏取り済みの事実が1つ与えられます。**事実の水増し・別の話の混入をしません。**
与えられた事実と一次ソースの範囲を超えて断定せず、数字・年代を盛りません。
事実が英語なら自然な日本語に直します。医療・投資・法律の助言に踏み込みません。"""

    if cfg["name"] == "heisei":
        return common + f"""

このチャンネルの作り方:
- シーン1のフックは「あれ、覚えてる？」型。**視聴者が思い出せる入口**から入る
  （知らない話の種明かしではなく、知っている話の掘り起こし）。
- めたんが懐かしがり、平成を知らないずんだもんが現代の目線でツッコむ構図にする。
  これで同世代以外にも「そんな時代があったのか」で成立させる。
- **年代・製品名・出来事の名称は、与えられた事実に書かれているものだけを使う。**
  「確か◯年頃」のような曖昧な記憶を書かない（世代ネタの誤りは一発で刺される）。
- シーン{SCENE_COUNT}は「今どうなったか」か「当時は当たり前だった」の落差で締める。
  コメントで思い出を語りたくなる終わり方にする（露骨に「コメントして」とは書かない）。
- 懐かしむ対象を馬鹿にしない。特定の企業・商品を貶さない。"""

    return common + f"""

このチャンネルの作り方:
- シーン1は「実は◯◯！？」型のフック。事実のいちばん意外な点を一言で言い切る。
- 本編は「よくある思い込み → 実は → 根拠」の順。シーン4で出典に軽く触れる
  （「◯◯の資料にある話なのだ」程度。URLや正式名称の羅列はしない）。
- シーン{SCENE_COUNT}は、もう一段の意外か現代との接続で締める（まとめ・説教にしない）。"""


def _fact_context(facts: list) -> str:
    """採用ネタをユーザーメッセージに展開する。リスト形式では複数件を渡す。"""
    lines = []
    for i, f in enumerate(facts, 1):
        head = f"事実{i}: " if len(facts) > 1 else "事実: "
        lines.append(head + f["fact"])
        src = f.get("backing_note") or ""
        lines.append(f"  一次ソース: {src}（{f['backing_url']}）" if src
                     else f"  一次ソース: {f['backing_url']}")
    return "\n".join(lines)


def _thread_context(thread: dict) -> str:
    """採用スレをユーザーメッセージに展開する。レスは翻案に足りるぶんだけ渡す。"""
    lines = [f"スレタイ: {thread['title']}", "", "レス:"]
    for r in thread["res"][:80]:
        text = r["text"].replace("\n", " ")[:120]
        lines.append(f"{r['no']}: {text}")
    return "\n".join(lines)


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
- caption は20字以内。シーン1の caption はスレタイとして全編画面上部に出しつづけるので、
  動画全体を一言で言い切る（そのシーンの説明ではない）。シーン2以降の caption は
  画面に出ない構成メモ。
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


# チャンネル設定のフラグ → 入力の種類と、それを用意する手順。
# 複数のフラグを立てたチャンネル（heisei など）は、**どれか1つ**あれば作れる:
# heisei は事実ベースを主にしつつ、良い懐かしスレが出た日は引用型でも作れる（#113）
_SOURCE_KINDS = (
    ("fact_source", "fact", "裏取り済みのネタ",
     "tools/fetch_facts.py で収集し、--back で一次ソースを付けて --adopt してから\n"
     "  make_video.py --channel <ch> --fact <ネタID>"),
    ("thread_source", "thread", "引用スレ",
     "tools/fetch_threads.py --board ... で収集し、--adopt で採用してから\n"
     "  make_video.py --channel <ch> --thread <スレID>"),
)


def _require_source(cfg: dict, **given) -> None:
    """ネタ元が渡されているかを確かめる。0からの創作を防ぐ最後の関門（docs/04 2-2章）。"""
    allowed = [(key, label, how) for flag, key, label, how in _SOURCE_KINDS if cfg.get(flag)]
    if not allowed or any(given.get(key) is not None for key, _, _ in allowed):
        return
    labels = " か ".join(label for _, label, _ in allowed)
    steps = "\n  ".join(how for _, _, how in allowed)
    raise PipelineError(
        f"{cfg['name']} は{labels}が必要です（v5: 0からの創作はしない。docs/04 2-2章）。\n"
        f"  {steps}"
    )


def generate(cfg: dict, theme: str, offline: bool = False,
             thread: dict | None = None, fact: dict | None = None,
             facts: list | None = None) -> dict:
    """台本JSONを返す。offline=True かAPIキー未設定ならテンプレを返す。

    thread は採用スレ（fetch_threads）、fact は裏取り済みネタ（fetch_facts）。
    どのチャンネルもソース無しでは生成しない:
    LLMの0からの創作は展開もオチも平均値になり、つまらない（docs/04 2-2章・v5）。
    """
    if not offline:
        _require_source(cfg, thread=thread, fact=fact or (facts[0] if facts else None))
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

    system = _system(cfg)
    if thread:
        system += _thread_rules(cfg)
        user = (f"チャンネル: {cfg['name']}\n\n{_thread_context(thread)}\n\n"
                "このスレを翻案して、オチから逆算した掛け合いショート動画の台本を作ってください。")
    elif facts and len(facts) > 1:
        # 「◯◯選」リスト形式。ネタが複数あるときだけこの型になる（docs/02 4章）
        system += _fact_rules(cfg) + _list_rules(cfg, len(facts))
        user = (f"チャンネル: {cfg['name']}\n\n{_fact_context(facts)}\n\n"
                f"この{len(facts)}件で「◯◯選」形式のショート動画の台本を作ってください。")
    elif fact or facts:
        one = fact or facts[0]
        system += _fact_rules(cfg)
        shape = ("「あれ覚えてる？」型の懐かし掛け合い" if cfg["name"] == "heisei"
                 else "「実は◯◯」型の掛け合い")
        user = (f"チャンネル: {cfg['name']}\n\n{_fact_context([one])}\n\n"
                f"この事実で{shape}ショート動画の台本を作ってください。")
    else:
        user = (f"チャンネル: {cfg['name']}\nテーマ: {theme}\n\n"
                "このテーマで掛け合いショート動画の台本を作ってください。")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=4000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _schema(cfg)}},
        messages=[{"role": "user", "content": user}],
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
