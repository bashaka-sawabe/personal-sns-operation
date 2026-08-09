#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""パイプライン共通のパス・シークレット・外部コマンド実行。

方針:
- 外部依存は ffmpeg（必須）と anthropic SDK（台本生成時のみ）だけ。
  fetch_metrics.py と同じく、無いものはスキップして動き続ける。
- APIキーが1つも無くても --offline で一気通貫が動くこと。課金前に構造を検証できる。
"""
import os
import re
import shutil
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTENT_DIR = os.path.join(ROOT, "content")
SCRIPTS_DIR = os.path.join(CONTENT_DIR, "scripts")
ASSETS_DIR = os.path.join(CONTENT_DIR, "assets")
OUT_DIR = os.path.join(CONTENT_DIR, "out")
# シークレット置き場。環境ごとに場所が違うので候補を順に探す
# （実際 ~/dev/ から ~/repo/ に移動していた。1箇所決め打ちだと静かに壊れる）
SECRETS_DIRS = [
    os.path.expanduser("~/repo/.cowork-secrets"),
    os.path.expanduser("~/dev/.cowork-secrets"),
    os.path.expanduser("~/.cowork-secrets"),
]

# 縦動画の規格。IG Reels / TikTok / YouTube Shorts 共通
WIDTH, HEIGHT, FPS = 1080, 1920, 30

# 字幕フォント。極太ウェイトでないとショート動画では潰れて読めない
FONT_CANDIDATES = [
    "/Library/Fonts/ヒラギノ角ゴ StdN W8.otf",
    "/Library/Fonts/ヒラギノ角ゴ Std W8.otf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


class PipelineError(RuntimeError):
    pass


def secret_path(filename: str) -> str:
    """既存ファイルがあればそのパス、無ければ最優先候補のパスを返す（保存先の決定にも使う）。"""
    for d in SECRETS_DIRS:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return os.path.join(SECRETS_DIRS[0], filename)


def read_secret(env_name: str, filename: str) -> str:
    """環境変数を優先し、無ければシークレット置き場を順に探す。"""
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    p = secret_path(filename)
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def font_path() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise PipelineError(
        "日本語フォントが見つかりません。以下のいずれかを配置してください:\n  "
        + "\n  ".join(FONT_CANDIDATES)
    )


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise PipelineError(f"{binary} が見つかりません。`brew install {binary}` で導入してください。")
    return path


def run(cmd: list, quiet: bool = True) -> None:
    """外部コマンドを実行し、失敗したら stderr 付きで落とす。"""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        raise PipelineError(f"{cmd[0]} が失敗しました:\n" + "\n".join(tail))
    if not quiet and proc.stdout:
        print(proc.stdout.strip(), file=sys.stderr)


def ffmpeg(args: list) -> None:
    run([require("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args])


def probe_duration(path: str) -> float:
    """メディアの尺（秒）。音声に映像の長さを合わせるために使う。"""
    proc = subprocess.run(
        [require("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise PipelineError(f"尺を取得できませんでした: {path}") from None


def ensure_dirs(*paths: str) -> None:
    for p in paths:
        os.makedirs(p, exist_ok=True)


def normalize_powerword(text: str) -> str:
    """パワーワードの照合用の正規化（#142）。

    「スレに実在するか」「セリフに入っているか」「どのフレーズで特大にするか」を
    同じ物差しで判定する必要がある。全半角・空白の違いで別物と見なすと、
    採用時は通ったのに画面では特大にならない、という食い違いが起きる。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def split_phrases(text: str, min_len: int = 6) -> list:
    """ナレーションを字幕表示の単位（フレーズ）に割る。

    句読点で切り、短すぎる断片は前に併合する。フレーズごとに音声を合成して
    「音声の長さ＝字幕の表示時間」にするのが狙い（タイミング計算を持たないための設計。
    docs/09 4-3）。
    """
    parts = [p for p in re.split(r"(?<=[、。！？!?])", " ".join((text or "").split())) if p.strip()]
    phrases = []
    for p in parts:
        if phrases and (len(p.strip("、。！？!? ")) < min_len or len(phrases[-1].strip("、。！？!? ")) < min_len):
            phrases[-1] += p
        else:
            phrases.append(p)
    return [p.strip() for p in phrases if p.strip()]


# 声の演出マーカー（#213）。行頭に `[低]` のように置くと、その行の読み上げが変わる。
# 効果音（音源）は使わない方針（#203）なので、**音の演出は声そのものを変えて作る**。
# 値は VOICEVOX の audio_query に掛ける係数・加算値:
#   speed=speedScale倍率 / pitch=pitchScale加算 / intonation=intonationScale倍率
#   volume=volumeScale倍率 / pre=prePhonemeLength加算（その行の前の間）
VOICE_MARKS = {
    "間": {"pre": 0.5},                                        # 一拍おいてから言う
    "小": {"volume": 0.6, "pitch": -0.02, "speed": 0.93},      # 声を落とす・本音
    "叫": {"volume": 1.35, "intonation": 1.5, "pitch": 0.04, "speed": 1.08},
    "低": {"pitch": -0.06, "intonation": 0.65, "speed": 0.9},  # 真顔・気持ち悪さ
    "早": {"speed": 1.25, "intonation": 1.15},                 # まくしたてる
    "伸": {"speed": 0.6, "intonation": 1.3},                   # 「ンニィィィィィ」
}
# VOICEVOXが受け付ける範囲。外すとエンジンが400を返す
_PITCH_RANGE = (-0.15, 0.15)


def split_voice_marks(text: str) -> tuple:
    """行頭の演出マーカーを剥がして (マーカー名のリスト, 本文) を返す。

    マーカーは**読み上げにも字幕にも出さない**。字数の勘定からも外れるよう、
    台本の検査（script.form_issues）と素材生成（media）の両方がこれを通す。
    """
    marks, rest = [], (text or "").lstrip()
    while True:
        m = re.match(r"\[([^\[\]]{1,2})\]", rest)
        if not m or m.group(1) not in VOICE_MARKS:
            break
        marks.append(m.group(1))
        rest = rest[m.end():].lstrip()
    return marks, rest


def voice_effects(marks: list) -> dict:
    """マーカー列を audio_query への効果にまとめる。同時指定は掛け合わせる。"""
    eff = {"speed": 1.0, "pitch": 0.0, "intonation": 1.0, "volume": 1.0, "pre": 0.0}
    for name in marks:
        for k, v in VOICE_MARKS.get(name, {}).items():
            if k in ("pitch", "pre"):
                eff[k] += v
            else:
                eff[k] *= v
    eff["pitch"] = max(_PITCH_RANGE[0], min(_PITCH_RANGE[1], eff["pitch"]))
    return eff


# 折り返し位置の判定に使う文字クラス（wrap_japanese）
_HIRAGANA = re.compile(r"[ぁ-ん]")
_KATAKANA = re.compile(r"[ァ-ヶー]")
_WORDCHAR = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ%％]")
_KANJI = re.compile(r"[一-鿿々]")
# 直後で改行してよい助詞・接続の1〜2文字（「〜は/が/を…」の直後は文節境界になりやすい）
_PARTICLES = ("は", "が", "を", "に", "へ", "で", "と", "も", "や", "の",
              "から", "まで", "より", "って", "けど", "ので", "たら", "なら")


def _break_score(text: str, i: int) -> int:
    """text[:i] | text[i:] で改行したときの自然さ（大きいほど良い）。

    文字数だけで切ると「また行けない飲み／会」のような分節無視の改行になり
    読みにくい（本人指摘 2026-08-08・#203）。形態素解析を入れずに、
    文節境界に多い並び（句読点の後・助詞の後・かな→漢字の変わり目）を優先し、
    単語の内部（カタカナ語・英数字・拗促音の前）を強く避ける。
    """
    prev, nxt = text[i - 1], text[i]
    # 行頭に来てはいけない文字（句読点・閉じ・小書き・長音）の前では切らない
    if nxt in "、。！？!?」』）)】ーぁぃぅぇぉっゃゅょァィゥェォッャュョん…":
        return -100
    # 開き括弧の直後・単語（カタカナ語/英数字）の内部も切らない
    if prev in "「『（(":
        return -100
    if prev in "ぁぃぅぇぉっゃゅょァィゥェォッャュョー":
        return -60                    # 促音・拗音・長音の直後は活用の内部（「悔しかっ/た」）
    if _HIRAGANA.match(prev) and nxt in "たてだで":
        return -70                    # 活用語尾＋助動詞（「一体化し/た」）。動詞が真っ二つになる
    if _KATAKANA.match(prev) and _KATAKANA.match(nxt):
        return -60
    if _WORDCHAR.match(prev) and _WORDCHAR.match(nxt):
        return -60
    if prev in "、。！？!?":
        return 50                     # 句読点の直後が最良
    if prev in "」』）)】":
        return 45                     # 閉じ括弧の直後も切れ目（「【〜】一重」を「】一/重」で割らない）
    if nxt in "「『（(【":
        return 40                     # 開き括弧の前も切れ目
    if any(text[max(0, i - len(p)):i] == p for p in _PARTICLES) and not _HIRAGANA.match(nxt):
        return 30                     # 助詞の後ろ＋次がかな以外＝文節の頭
    if _HIRAGANA.match(prev) and not _HIRAGANA.match(nxt):
        return 20                     # かな→漢字/カタカナの変わり目
    if _HIRAGANA.match(nxt):
        # 文節の頭は内容語（漢字・カタカナ）で始まる。ひらがなの前で切ると
        # 送り仮名・活用語尾・助詞のどれかを割る（「悔/しかった」「みた/いに」）。
        # 個別パターンを潰しても別の形で再発したので一般則にした（#210→#219→#224）
        return -25
    if _KANJI.match(prev) and _KANJI.match(nxt):
        return -30                    # 漢字の連続は熟語の内部（「一/重」）になりやすい
    return 0


def wrap_japanese(text: str, per_line: int) -> str:
    """日本語を文節を考慮した位置で折り返す。

    必要な行数を先に決めて（貪欲に詰めると最終行に1文字だけ残る）、
    各行は目標幅の近傍で最も自然な切れ目を選ぶ。per_line は
    「1行がこれを超えたら画面外」という上限で、詰める目標値ではない。
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    lines = []
    rest = text
    while rest:
        rows = max(1, -(-len(rest) // per_line))
        if rows == 1:
            lines.append(rest)
            break
        width = -(-len(rest) // rows)   # 均等割りしたときの目標幅
        # 目標幅の±3文字（上限は超えない）から一番切れ目らしい位置を選ぶ。
        # ±2だと「昭和4年、解雇名簿…」の読点が窓の外に落ち、熟語の内部で切られた
        lo = max(1, width - 3)
        hi = min(len(rest) - 1, width + 3, per_line)
        best = max(range(lo, hi + 1),
                   key=lambda i: (_break_score(rest, i), -abs(i - width)))
        lines.append(rest[:best])
        rest = rest[best:]
    return "\n".join(lines)
