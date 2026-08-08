#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シーン素材（背景・ナレーション音声）の生成。

背景（docs/09 4-1）:
  1. Pexels ストック映像（既定）— 台本の image_prompt から検索語を作って取得する。
     無料・商用可・帰属表示不要。取得済みは content/assets/stock/ にキャッシュする。
  2. グラデーション（フォールバック）— キーが無い・オフライン・検索ヒット無しでも
     止めないための保険。この見た目で投稿はしない（フィード水準を下回る）。

音声（docs/09 4-2）:
  1. VOICEVOX（既定）— エンジンを自動起動し、フレーズ単位で合成する。
     キャラ名のクレジット表記が利用条件なので credits.txt に書き出す。
  2. macOS `say`（フォールバック）— エンジンが無い環境の疎通確認用。

生成AIを使わない理由・本人の声にしない理由は docs/09 4-1 / 4-2 を参照。
"""
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .channels import CHARACTERS
from .script import INTERRUPT_MARK
from .common import (
    ASSETS_DIR, HEIGHT, WIDTH, PipelineError, ffmpeg, probe_duration,
    read_secret, require, run, split_phrases,
)

# フォールバック時のグラデ配色。白の極太字幕とのコントラストを最優先に選んである
PALETTE = [
    ("0x1a1a2e", "0x16213e"),
    ("0x1b2430", "0x2d4059"),
    ("0x231b2e", "0x3b2c47"),
    ("0x14262c", "0x1f3d3a"),
    ("0x2b1d1d", "0x40282a"),
]

# ---- テンポ（#90 → #121でチャンネル別に） ----
# ここはチャンネル設定に style が無いときのフォールバック。
# 実際の値は data/channels/<ch>.json の style から引く。
# ジャンルごとに最適値が違うことが実測で分かっている（docs/02 1章）:
# meme 26.7秒・テロップ1.1秒/枚、trivia 55〜59秒・2.0秒/枚、heisei 2.5秒/ネタ
PHRASE_GAP = 0.05      # フレーズ末尾の無音。0にすると息継ぎが消えて機械的になる
SCENE_TAIL = 0.15      # シーン末尾の余白。読み終わり即カットの「詰まり」を避ける最小限

# 効果音。効果音ラボ（商用無料・クレジット表記不要・収益化明示OK）から取得したものを
# content/assets/se/<名前>.mp3 に置く。**Content ID登録は規約で禁止**されている点に注意。
# どの音をどこで鳴らすかは style.se でチャンネルごとに変える（docs/02 1章）
SE_DIR = os.path.join(ASSETS_DIR, "se")

STOCK_DIR = os.path.join(ASSETS_DIR, "stock")
PEXELS_SEARCH = "https://api.pexels.com/videos/search"
# Openverse はサインアップ不要（Pexelsに登録できない環境のための本命。#50）。
# CC0/パブリックドメインに絞れば帰属表示も不要になる
OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
# 素材ホストがボット除けで python-urllib を弾くことがあるため名乗りを揃える
_UA = {"User-Agent": "personal-sns-operation/1.0 (content pipeline)"}

# image_prompt から検索語を作るときに捨てる語。
# プロンプトは「様式の指定」が大半で、Pexels検索に効くのは被写体の名詞だけ
_PROMPT_NOISE = {
    "abstract", "minimal", "minimalist", "illustration", "stylized", "symbolic",
    "of", "a", "an", "the", "and", "with", "over", "in", "on", "no", "text",
    "people", "person", "faces", "logo", "tones", "tone", "palette", "composition",
    "still", "life", "soft", "muted", "warm", "cool", "pale", "light", "dark",
    "gradient", "background", "motifs", "shapes", "grid", "overlay", "made",
    "white", "black", "grey", "gray", "blue", "green", "pink", "red", "cream",
    "ivory", "sepia", "gold", "silver", "beige", "navy",
}

VOICEVOX_URL = "http://127.0.0.1:50021"
# 1.0だと間延びする。1.1でもまだ遅く見えたので、ショートで見慣れた語速まで上げた（#85）
VOICEVOX_SPEED = 1.22
# エンジンの置き場候補。GUI版（VOICEVOX.app）にも同じエンジンが同梱されている
VOICEVOX_ENGINES = [
    os.path.expanduser("~/.voicevox/macos-arm64/run"),
    os.path.expanduser("~/.voicevox/macos-x64/run"),
    "/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run",
]

# 話者アイコンの置き場。**置かなくてよい**（無ければキャラ色と名前から生成される。#140）。
# 手描きアイコンなど規約のある素材を使うときだけ、ここに <key>.png と <key>.txt を対で置く
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

# エンジンの起動は1プロセスに1回で足りるのでモジュール内に持つ
_voicevox = {"checked": False, "up": False}


def _http(url: str, method: str = "GET", body: bytes | None = None,
          headers: dict | None = None, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


# ---------------------------------------------------------------- VOICEVOX

def _voicevox_alive() -> bool:
    try:
        _http(f"{VOICEVOX_URL}/version", timeout=2)
        return True
    except OSError:
        return False


def ensure_voicevox() -> bool:
    """エンジンが応答する状態にする。無い環境では False（say にフォールバック）。"""
    if _voicevox["checked"]:
        return _voicevox["up"]
    _voicevox["checked"] = True

    if not _voicevox_alive():
        binary = next((p for p in VOICEVOX_ENGINES if os.path.exists(p)), None)
        if binary:
            # 起動したまま放置する（毎回上げ下げすると初回ロードの数十秒を毎本払う）
            subprocess.Popen(
                [binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(120):  # 初回起動はモデルのロードで時間がかかる
                if _voicevox_alive():
                    break
                time.sleep(0.5)

    _voicevox["up"] = _voicevox_alive()
    if not _voicevox["up"]:
        print("  VOICEVOXエンジンが見つからないため say で代用します（投稿品質ではありません）",
              file=sys.stderr)
    return _voicevox["up"]


def voicevox_used() -> bool:
    """クレジット表記（利用条件）が必要か。VOICEVOXの声を使ったときだけ True。"""
    return _voicevox["up"]


def _voicevox_wav(text: str, path: str, speaker: int, speed: float) -> None:
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    query = json.loads(_http(f"{VOICEVOX_URL}/audio_query?{q}", method="POST"))
    query["speedScale"] = speed
    wav = _http(
        f"{VOICEVOX_URL}/synthesis?speaker={speaker}",
        method="POST",
        body=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    with open(path, "wb") as f:
        f.write(wav)


def _say_wav(text: str, path: str, voice: str = "Kyoko", rate: int = 180) -> None:
    try:
        run([require("say"), "-v", voice, "-r", str(rate), "-o", path, text])
    except PipelineError:
        # 音声 Kyoko が入っていない環境では既定音声にフォールバック
        run([require("say"), "-r", str(rate), "-o", path, text])


def narration(text: str, path: str, speaker: int,
              speed: float = VOICEVOX_SPEED, gap: float = PHRASE_GAP) -> str:
    """セリフ1フレーズ分の音声を作り、44.1kHzモノラルwavで返す。

    speaker はVOICEVOXのスタイルID（キャラごとに固定。channels.CHARACTERS）。
    speed / gap はチャンネルの style から来る（ジャンルで最適値が違う。docs/02 1章）。
    末尾の無音は息継ぎの最小限。「音声の長さ＝字幕の表示時間」なので、
    この無音は字幕の余韻でもある（docs/09 4-3）。
    """
    raw = path + ".raw"
    if ensure_voicevox():
        try:
            _voicevox_wav(text, raw + ".wav", speaker, speed)
            os.rename(raw + ".wav", raw)
        except (OSError, ValueError):
            _say_wav(text, raw + ".aiff")
            os.rename(raw + ".aiff", raw)
    else:
        _say_wav(text, raw + ".aiff")
        os.rename(raw + ".aiff", raw)
    ffmpeg(["-i", raw, "-af", f"apad=pad_dur={gap}", "-ar", "44100", "-ac", "1", path])
    os.remove(raw)
    return path


# ---------------------------------------------------------------- 効果音

def se_track(kind: str) -> str | None:
    """効果音のパス。素材が無ければ None（効果音なしで劣化継続する）。

    素材は立ち絵・BGMと同じく手動で置く（規約確認を飛ばさないため。docs/09 4-8）。
    """
    if not kind:
        return None
    for ext in ("mp3", "wav", "m4a"):
        path = os.path.join(SE_DIR, f"{kind}.{ext}")
        if os.path.exists(path):
            return path
    _warn_missing_se(kind)
    return None


# 素材が無い警告は毎シーン同じなので1回だけ出す
_missing_se_warned = set()


def _warn_missing_se(kind: str) -> None:
    if kind in _missing_se_warned:
        return
    _missing_se_warned.add(kind)
    print(f"  ⚠️ 効果音がありません: content/assets/se/{kind}.mp3"
          "（効果音なしで続けます）", file=sys.stderr)


def se_credit(kind: str) -> str:
    """効果音のクレジット。同名 .txt（サイドカー）があればそれを使う。

    効果音ラボはクレジット表記が不要（禁止ではなく任意）なので、
    サイドカーが無ければ表記しない。表記が要る素材に差し替えたときのために
    仕組みだけ通してある（BGM・立ち絵と同じ約束）。
    """
    sidecar = os.path.join(SE_DIR, f"{kind}.txt")
    if os.path.exists(sidecar):
        return open(sidecar, encoding="utf-8").read().strip()
    return ""


# ---------------------------------------------------------------- 話者アイコン

def icon_image(key: str) -> str | None:
    """持ち込みアイコンのパス。無ければ None（render 側がキャラ色と名前で生成する）。

    立ち絵は「規約を確認した本人が置く」運用にした結果、置かれていないキャラは
    画面に出られず、喋っているのに姿が無いという事故になった（#140）。
    アイコンは素材が無くても生成できるので、ここは**任意の差し替え口**でしかない。
    """
    path = os.path.join(ICONS_DIR, f"{key}.png")
    return path if os.path.exists(path) else None


def icon_credit(key: str) -> str:
    """持ち込みアイコンのクレジット。画像と同名の .txt（サイドカー）に書いてある。

    BGM（bgm_credit）と同じ約束。素材を置くときは必ず対で置く（docs/08）。
    """
    sidecar = os.path.join(ICONS_DIR, f"{key}.txt")
    if os.path.exists(sidecar):
        return open(sidecar, encoding="utf-8").read().strip()
    return ""


# ---------------------------------------------------------------- 背景

def _stock_query(image_prompt: str) -> str:
    """image_prompt（英語の様式指定つき）から Pexels 検索語を抽出する。"""
    head = (image_prompt or "").split(",")[0].lower()
    words = [w for w in re.findall(r"[a-z]+", head) if w not in _PROMPT_NOISE]
    return " ".join(words[:4])


def _pick_video_file(video: dict) -> dict | None:
    """縦動画で1080x1920を賄える最小のファイルを選ぶ（帯域と画質のバランス）。"""
    files = [
        f for f in video.get("video_files", [])
        if (f.get("height") or 0) >= (f.get("width") or 0)  # 縦向きだけ
    ]
    enough = [f for f in files if (f.get("height") or 0) >= HEIGHT]
    if enough:
        return min(enough, key=lambda f: f["height"])
    return max(files, key=lambda f: f.get("height") or 0) if files else None


def stock_background(image_prompt: str, api_key: str) -> str | None:
    """Pexelsからストック映像を1本取る。取れない理由が何であれ None（Openverseへ）。

    検索の質はOpenverseより高いが、「題材は合っているがトーンが違う」「暗すぎて画に
    ならない」は検索語では防げない。Openverseと同じ目視選別を通す（#83）。
    """
    query = _stock_query(image_prompt)
    if not query:
        return None
    cached = os.path.join(STOCK_DIR, hashlib.sha1(query.encode()).hexdigest()[:16] + ".mp4")
    if os.path.exists(cached):
        return cached

    try:
        q = urllib.parse.urlencode({
            "query": query, "orientation": "portrait", "size": "medium", "per_page": 8,
        })
        res = json.loads(_http(f"{PEXELS_SEARCH}?{q}", headers={"Authorization": api_key}))
        # 動画そのものは見せられないので、APIが返すプレビュー静止画で判定する
        candidates = [
            {"url": v.get("image", ""), "video": v}
            for v in res.get("videos", []) if v.get("image") and _pick_video_file(v)
        ]
        if not candidates:
            return None
        chosen = _vision_pick(candidates, image_prompt)
        if chosen is None:
            return None  # 全候補が不適 → Openverse に任せる
        f = _pick_video_file(chosen["video"])
        os.makedirs(STOCK_DIR, exist_ok=True)
        data = _http(f["link"], timeout=120)
        with open(cached, "wb") as fp:
            fp.write(data)
        return cached
    except (OSError, ValueError) as e:
        print(f"  ストック映像の取得に失敗（{query}）: {e}", file=sys.stderr)
    return None


def openverse_background(image_prompt: str) -> str | None:
    """Openverse（キーレス）からCC0/PDの縦写真を1枚取る。取れなければ None。

    匿名利用はレート制限が厳しめなので、429は一度だけ待って引き直す。
    写真は静止画なので、動きは render 側の Ken Burns に任せる。
    """
    query = _stock_query(image_prompt)
    if not query:
        return None
    cached = os.path.join(STOCK_DIR, "ov_" + hashlib.sha1(query.encode()).hexdigest()[:16] + ".jpg")
    if os.path.exists(cached):
        return cached

    # CC0/PDに絞ると母数が小さく、具体的な検索語は0件になりやすい。
    # 語を後ろから削り、縦向き指定→指定なしの順で段階的に緩める
    # （横写真でも render 側が中央をcover-cropするので使える）。
    # まず写真ソースを絞った検索を全段試し、駄目なら制限なしでもう一周する
    words = query.split()
    variants = [" ".join(words[:n]) for n in range(len(words), 0, -1)]
    attempts = [
        (q_text, aspect, source)
        for source in ("flickr,rawpixel,stocksnap", "")
        for q_text in variants
        for aspect in ("tall", "")
    ]
    # 検索結果の並びは信用できない（商品パッケージ等が先頭に来る）ため、
    # 候補はClaudeの目で選別する。ただし選別の呼び出しは1シーン5回まで
    # （全滅が続く=検索語が悪いので、それ以上払っても良い画は出ない）
    judge_budget = 5
    for q_text, aspect, source in attempts:
        if judge_budget <= 0:
            break
        params = {"q": q_text, "license": "cc0,pdm", "category": "photograph", "per_page": 8}
        if aspect:
            params["aspect_ratio"] = aspect
        if source:
            params["source"] = source
        items = _openverse_results(urllib.parse.urlencode(params), q_text)
        if not items:
            continue
        judge_budget -= 1
        # 未検証の画は採らない。検索順で拾うと、題材が全く違う写真や
        # 他人が写り込んだ写真がそのまま動画に焼き込まれる（#74）
        chosen = _vision_pick(items, image_prompt)
        if chosen is None:
            continue  # 全候補が不適 → 検索を緩めて次へ
        try:
            data = _http(chosen.get("url", ""), timeout=60)
        except OSError:
            continue  # ホスト側で弾かれたら次の緩め方へ
        os.makedirs(STOCK_DIR, exist_ok=True)
        with open(cached, "wb") as fp:
            fp.write(data)
        return cached
    return None


def _openverse_results(params: str, query: str) -> list:
    """検索を1回投げて候補一覧を返す。429は一度だけ待って引き直す。"""
    for attempt in (1, 2):
        try:
            res = json.loads(_http(f"{OPENVERSE_SEARCH}?{params}", timeout=20))
            return [i for i in res.get("results", []) if i.get("url")]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                time.sleep(20)  # 匿名のレート制限。1回だけ待って引き直す
                continue
            print(f"  Openverse検索に失敗（{query}）: {e}", file=sys.stderr)
            return []
        except (OSError, ValueError) as e:
            print(f"  Openverse検索に失敗（{query}）: {e}", file=sys.stderr)
            return []
    return []


def _media_type(data: bytes) -> str | None:
    """画像のMIMEタイプをマジックバイトから判定する。対応外なら None。

    拡張子やContent-Typeは当てにならない。Openverseのサムネイルは拡張子が .jpg でも
    実体がWebPのことがあり、jpegと偽って送るとAPIが400で落ちる（#74）。
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


# 選別できなかった理由は毎シーン同じなので、1回だけ出す
_unjudged_warned = set()


def _warn_unjudged(reason: str) -> None:
    if reason in _unjudged_warned:
        return
    _unjudged_warned.add(reason)
    print(f"  ⚠️ {reason}。背景はグラデになります。", file=sys.stderr)


# 目視判定の返答スキーマ。番号だけを構造化出力で受け取る（パース事故を防ぐ）
_PICK_SCHEMA = {
    "type": "object",
    "properties": {"choice": {"type": "integer"}},
    "required": ["choice"],
    "additionalProperties": False,
}


def _vision_pick(items: list, image_prompt: str) -> dict | None:
    """候補のサムネイルをClaudeに見せて、背景に使える1枚を選ばせる。

    検索の並び順だけでは商品パッケージ・文字だらけの画像を弾けない
    （タイトル文字列でも判別できない）ため、画像そのものを見て選ぶ。

    **判定できなかった場合も None を返す**（#74）。以前は先頭候補で続行していたが、
    それだと題材の違う写真や他人が写った写真が無検査で動画に入る。
    背景が無いほうが、確認できていない画を焼き込むより安全。
    """
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if not api_key:
        _warn_unjudged("ANTHROPIC_API_KEY が無いため背景を選別できません")
        return None
    try:
        import anthropic
    except ImportError:
        _warn_unjudged("anthropic SDK が無いため背景を選別できません")
        return None

    thumbs = []
    for item in items[:6]:
        url = item.get("thumbnail") or item.get("url")
        try:
            data = _http(url, timeout=30)
        except OSError:
            continue
        media_type = _media_type(data)
        if not media_type:
            continue  # 番号がずれるので、送れないものはここで落とす
        thumbs.append((item, data, media_type))
    if not thumbs:
        return None

    content = []
    for i, (_, data, media_type) in enumerate(thumbs, 1):
        content.append({"type": "text", "text": f"候補{i}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.b64encode(data).decode()},
        })
    content.append({"type": "text", "text": (
        "縦型ショート動画の背景素材を選んでいます。\n"
        f"欲しい画のイメージ（英語）: {image_prompt}\n\n"
        "**人が写り込んでいるものは、顔が写っていなくても全て不適**。"
        "CC0は著作権の許諾でしかなく肖像権は別に残るため、他人が写った写真は使えません。\n"
        "その他の不適: 商品パッケージ・ラベル・ロゴ・文字が主体・スクリーンショット・"
        "図表や地図の文字だらけのもの・画質が低いもの。\n"
        "**暗すぎて何が写っているか分からないもの、ほぼ単色で画として成立しないものも不適。**\n"
        "上のイメージと**題材が違うものも不適**（机が欲しいのに乗り物、など）。"
        "雰囲気が近ければ細部の一致は問いません。\n"
        "**トーンが合わないものも不適**。日常の悩みや雑談の動画に、有刺鉄線・鎖・銃器・"
        "医療現場のような緊張感の強い画は合いません。\n"
        "適切: 人のいない実写の風景・情景・静物で、上に字幕を載せても邪魔にならないもの。\n"
        "最も適切な候補の番号を choice に入れてください。"
        "**迷ったら 0 を選んでください**（不適な画を使うより、背景なしで作り直すほうが安全）。"
    )})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=2048,
            output_config={
                "effort": "low",  # 番号を1つ選ぶだけの判定。深い思考は要らない
                "format": {"type": "json_schema", "schema": _PICK_SCHEMA},
            },
            messages=[{"role": "user", "content": content}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        n = int(json.loads(text)["choice"])
    except (OSError, ValueError, KeyError, anthropic.APIError) as e:
        _warn_unjudged(f"背景の選別に失敗しました: {e}")
        return None  # 判定の失敗でパイプラインは止めないが、未検証の画も使わない
    if 1 <= n <= len(thumbs):
        return thumbs[n - 1][0]
    return None  # 0（全候補が不適）と範囲外は、どちらも「使えるものが無い」


def gradient_background(index: int, path: str) -> str:
    """フォールバックのグラデ背景。連番で色が変わり、単調さを避ける。"""
    c0, c1 = PALETTE[index % len(PALETTE)]
    try:
        ffmpeg([
            "-f", "lavfi",
            "-i", f"gradients=s={WIDTH}x{HEIGHT}:c0={c0}:c1={c1}:n=2:d=1",
            "-vf", "noise=alls=6:allf=t+u",
            "-frames:v", "1", path,
        ])
    except PipelineError:
        # gradients フィルタが無いビルド向けのフォールバック
        ffmpeg(["-f", "lavfi", "-i", f"color=c={c0}:s={WIDTH}x{HEIGHT}", "-frames:v", "1", path])
    return path


def _fetch_background(prompt: str, api_key: str) -> dict | None:
    """検索語1本ぶんの取得。Pexels（映像）→ Openverse（写真）の順に試す。"""
    if api_key:
        path = stock_background(prompt, api_key)
        if path:
            return {"path": path, "kind": "video", "provider": "pexels"}
    path = openverse_background(prompt)
    if path:
        return {"path": path, "kind": "image", "provider": "openverse"}
    return None


def background(index: int, image_prompt: str, asset_dir: str,
               api_key: str, offline: bool, queries: list | None = None) -> dict:
    """背景素材を返す。{"path", "kind": "video" | "image", "provider"}

    queries（チャンネルの style.bg_queries）があれば**そちらを使い、台本の
    image_prompt は見ない**（#141）。背景をシーンの内容から引くと、画面が台本を
    復唱するだけの挿絵になり、視覚的な報酬がゼロになる。ロンロンは2chと無関係の
    高刺激映像を流している（docs/02 2章）。

    **1本落ちてもグラデに落とさず、次の検索語で引き直す。** showa-001 は
    「昭和の工場」で何も取れず、真っ黒のグラデのまま完成してしまった。
    """
    if not offline:
        # 先頭を index でずらすのは、同じ動画の全シーンが同じ画にならないようにするため
        pool = queries or [image_prompt]
        for n in range(len(pool)):
            got = _fetch_background(pool[(index + n) % len(pool)], api_key)
            if got:
                return got
    return {"path": gradient_background(index, os.path.join(asset_dir, f"bg{index:02d}.png")),
            "kind": "image", "provider": "gradient"}


# ---------------------------------------------------------------- 組み立て

def build_scene_assets(script: dict, asset_dir: str, offline: bool = False,
                       style: dict | None = None) -> list:
    """台本の全シーン分の素材を作る（掛け合い形式）。

    返り値: [{bg, bg_kind, caption, phrases: [{text, audio, dur, speaker}], dur}]
    フレーズごとに音声を分けるのは、字幕を音声に同期させるため（docs/09 4-3）。
    話者はフレーズ単位で持ち、声・字幕色・立ち絵の強調をそこから引く。
    style はチャンネルの演出設定（data/channels/<ch>.json）。
    ジャンルごとに最適な尺・テンポが違うため、話速と間はここから引く（docs/02 1章）。
    """
    api_key = read_secret("PEXELS_API_KEY", "pexels_key.txt")
    style = style or {}
    speed = style.get("speed", VOICEVOX_SPEED)
    gap = style.get("phrase_gap", PHRASE_GAP)
    tail = style.get("scene_tail", SCENE_TAIL)
    se_map = style.get("se", {})

    scenes, providers, used_speakers = [], [], []
    n_scenes = len(script["scenes"])
    # 「◯◯選」リスト形式は20シーン超になる。1シーン1枚だと素材の取得と目視判定が
    # 数十回走って現実的でないので、数枚を使い回す（bg_pool 枚でローテーション）
    pool_size = style.get("bg_pool", 0) or n_scenes
    # 背景の検索語をチャンネル側に持たせているなら、台本の内容からは引かない（#141）
    bg_queries = style.get("bg_queries") or None
    bg_cache: dict[int, dict] = {}
    for i, scene in enumerate(script["scenes"]):
        slot = i % pool_size
        if slot not in bg_cache:
            bg_cache[slot] = background(slot, scene.get("image_prompt", ""),
                                        asset_dir, api_key, offline, queries=bg_queries)
        bg = bg_cache[slot]
        providers.append(bg["provider"])
        phrases = []
        for line in scene["dialogue"]:
            key = line["speaker"]
            if key not in used_speakers:
                used_speakers.append(key)
            style_id = CHARACTERS[key]["voicevox_speaker"]
            for text in split_phrases(line["text"]):
                j = len(phrases)
                # 割り込み記号は字幕にだけ残す。読み上げには渡さず、末尾の無音も落として
                # 「言い切る前に奪われた」を音で作る（#143）
                cut = text.rstrip().endswith(INTERRUPT_MARK)
                spoken = text.rstrip()[:-len(INTERRUPT_MARK)] if cut else text
                # 話者ごとの素のテンポ差を speed_scale で打ち消してから
                # チャンネルの話速を掛ける（遅い話者を出さない。#203）
                char_speed = speed * CHARACTERS[key].get("speed_scale", 1.0)
                audio = narration(spoken, os.path.join(asset_dir, f"na{i:02d}_{j:02d}.wav"),
                                  speaker=style_id, speed=char_speed, gap=0.0 if cut else gap)
                phrases.append({"text": text, "audio": audio,
                                "dur": probe_duration(audio), "speaker": key})
        # 読み終わりで即カットすると詰まって聞こえるのでシーン末尾に余白を足す
        dur = round(sum(p["dur"] for p in phrases) + tail, 2)
        # シーン頭の効果音。最終シーン（オチ）だけ音を変える。
        # どの音を使うかはチャンネルごと（meme=軽い転換音、heisei=和太鼓 など）
        se_kind = se_map.get("last" if i == n_scenes - 1 else "scene", "")
        scenes.append({
            "bg": bg["path"],
            "bg_kind": bg["kind"],
            "caption": scene["caption"],
            "phrases": phrases,
            "dur": dur,
            "se": se_track(se_kind),
            "se_kind": se_kind,
        })

    _write_credits(asset_dir, providers, used_speakers,
                   sorted({s["se_kind"] for s in scenes if s["se_kind"]}))
    voice = ("VOICEVOX: " + "・".join(CHARACTERS[k]["name"] for k in used_speakers)
             if voicevox_used() else "macOS say（フォールバック）")
    stock = sum(p != "gradient" for p in providers)
    print(f"  背景: ストック素材 {stock}/{len(scenes)}シーン ／ 音声: {voice}")
    if stock < len(scenes) and not offline:
        print(f"  ⚠️ {len(scenes) - stock}シーンがグラデ背景に落ちました"
              "（投稿品質ではありません）。素材が見つからないか、"
              "候補が全て不適でした。", file=sys.stderr)
    return scenes


def _write_credits(asset_dir: str, providers: list, used_speakers: list,
                   se_kinds: list | None = None) -> None:
    """投稿時の説明文に入れるクレジットを書き出す（VOICEVOXは表記が利用条件）。"""
    lines = []
    if voicevox_used():
        lines.extend(CHARACTERS[k]["credit"] for k in used_speakers)
    for key in used_speakers:
        # 持ち込みアイコンのクレジット（サイドカー）。生成アイコンなら何も出ない
        if icon_image(key) and icon_credit(key):
            lines.append(icon_credit(key))
    for kind in se_kinds or []:
        # 効果音のクレジット（サイドカー）。同文は1行にまとめる
        line = se_credit(kind)
        if line and line not in lines:
            lines.append(line)
    if "pexels" in providers:
        lines.append("映像素材: Pexels")
    if "openverse" in providers:
        # CC0/PDのみ取得しているため表記義務は無いが、出所は明示しておく
        lines.append("写真素材: Openverse（CC0 / Public Domain）")
    with open(os.path.join(asset_dir, "credits.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def append_credit(asset_dir: str, line: str) -> None:
    """後段（BGM選定など）で決まるクレジットを追記する。"""
    if not line:
        return
    with open(os.path.join(asset_dir, "credits.txt"), "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def bgm_credit(track: str) -> str:
    """BGMのクレジット。曲と同名の .txt（サイドカー）に書いてある。

    CC BY はクレジット表記が利用条件なので、サイドカーが無い曲は表記漏れの
    危険がある。曲を足すときは必ず対で置く（docs/09 4-4）。
    """
    sidecar = os.path.splitext(track)[0] + ".txt"
    if os.path.exists(sidecar):
        return open(sidecar, encoding="utf-8").read().strip()
    return ""


def bgm_track(seed: str) -> str | None:
    """BGM音源。content/assets/bgm/ に本人が置いた曲から決定的に選ぶ（同じ動画は同じ曲）。"""
    tracks = sorted(
        p for ext in ("mp3", "m4a", "wav", "aac")
        for p in glob.glob(os.path.join(ASSETS_DIR, "bgm", f"*.{ext}"))
    )
    if not tracks:
        return None
    return tracks[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(tracks)]
