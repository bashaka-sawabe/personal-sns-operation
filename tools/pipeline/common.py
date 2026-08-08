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


# 折り返し位置の判定に使う文字クラス（wrap_japanese）
_HIRAGANA = re.compile(r"[ぁ-ん]")
_KATAKANA = re.compile(r"[ァ-ヶー]")
_WORDCHAR = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ%％]")
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
    if nxt in "、。！？!?」』）)ーぁぃぅぇぉっゃゅょァィゥェォッャュョん…":
        return -100
    # 開き括弧の直後・単語（カタカナ語/英数字）の内部も切らない
    if prev in "「『（(":
        return -100
    if _KATAKANA.match(prev) and _KATAKANA.match(nxt):
        return -60
    if _WORDCHAR.match(prev) and _WORDCHAR.match(nxt):
        return -60
    if prev in "、。！？!?":
        return 50                     # 句読点の直後が最良
    if nxt in "「『（(":
        return 40                     # 開き括弧の前も切れ目
    if any(text[max(0, i - len(p)):i] == p for p in _PARTICLES) and not _HIRAGANA.match(nxt):
        return 30                     # 助詞の後ろ＋次がかな以外＝文節の頭
    if _HIRAGANA.match(prev) and not _HIRAGANA.match(nxt):
        return 20                     # かな→漢字/カタカナの変わり目
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
        # 目標幅の±2文字（上限は超えない）から一番切れ目らしい位置を選ぶ
        lo = max(1, width - 2)
        hi = min(len(rest) - 1, width + 2, per_line)
        best = max(range(lo, hi + 1),
                   key=lambda i: (_break_score(rest, i), -abs(i - width)))
        lines.append(rest[:best])
        rest = rest[best:]
    return "\n".join(lines)
