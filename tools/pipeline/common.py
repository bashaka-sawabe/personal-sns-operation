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


def wrap_japanese(text: str, per_line: int) -> str:
    """日本語は単語境界が無いので文字数で折り返す。句読点は行頭に送らない。"""
    text = " ".join((text or "").split())
    lines, line = [], ""
    for ch in text:
        if len(line) >= per_line and ch not in "、。」』ー":
            lines.append(line)
            line = ""
        line += ch
    if line:
        lines.append(line)
    return "\n".join(lines)
