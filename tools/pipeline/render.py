#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シーン素材 → 縦動画（ffmpeg）。

設計:
- シーンごとに独立した mp4 を書き出してから concat する。
  1本のフィルタグラフで全部やると、1シーン失敗しただけで全体が落ちる上に
  どのシーンが原因か分からない。分けておくと切り分けが効く。
- 各シーンの尺はナレーション音声の長さに従う。映像側を音に合わせるので、
  台本の長短をそのまま許容できる（尺の手調整が発生しない＝自動化が壊れない）。
- Ken Burns（ゆっくりズーム）を必ず入れる。静止画の連結は完走率が落ちる。
"""
import os

from .common import FPS, HEIGHT, WIDTH, ffmpeg, font_path, wrap_japanese

# 字幕の見た目。ショート動画は小さい画面で見られるので、太く・大きく・縁を厚く
FONT_SIZE = 82
LINE_SPACING = 22
BORDER_W = 9
CAPTION_Y = "h*0.60"      # 下寄せ。上部はUI、下部はキャプション欄に隠れやすい
CHARS_PER_LINE = 11
ZOOM_PER_FRAME = 0.0006   # 1フレームあたりの拡大率。これ以上速いと酔う
ZOOM_MAX = 1.12


def _drawtext(caption: str, textfile: str) -> str:
    with open(textfile, "w", encoding="utf-8") as f:
        f.write(wrap_japanese(caption, CHARS_PER_LINE))
    # ffmpeg のフィルタ引数はコロンとバックスラッシュが区切り文字になるためエスケープする
    escaped_font = font_path().replace("\\", "\\\\").replace(":", r"\:")
    escaped_file = textfile.replace("\\", "\\\\").replace(":", r"\:")
    return (
        f"drawtext=fontfile='{escaped_font}':textfile='{escaped_file}'"
        f":fontcolor=white:fontsize={FONT_SIZE}:line_spacing={LINE_SPACING}"
        f":borderw={BORDER_W}:bordercolor=black@0.85"
        f":x=(w-tw)/2:y={CAPTION_Y}"
    )


def render_scene(scene: dict, index: int, work_dir: str) -> str:
    """1シーンを mp4 にする。"""
    out = os.path.join(work_dir, f"scene{index:02d}.mp4")
    textfile = os.path.join(work_dir, f"cap{index:02d}.txt")
    # 先に2倍に拡大してからズームすることで、拡大時の劣化を避ける
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='min(1+{ZOOM_PER_FRAME}*on,{ZOOM_MAX})'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
        + _drawtext(scene["caption"], textfile)
    )
    ffmpeg([
        "-loop", "1", "-framerate", str(FPS), "-t", str(scene["dur"]), "-i", scene["bg"],
        "-i", scene["audio"],
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(scene["dur"]), "-shortest", out,
    ])
    return out


def concat(parts: list, out_path: str, work_dir: str) -> str:
    """シーンmp4を連結して最終ファイルにする。"""
    listfile = os.path.join(work_dir, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in parts:
            # concat デミューサはシングルクォートをこの形式でしかエスケープできない
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))
    ffmpeg([
        "-f", "concat", "-safe", "0", "-i", listfile,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", out_path,
    ])
    return out_path


def build(scenes: list, out_path: str, work_dir: str) -> str:
    parts = [render_scene(s, i, work_dir) for i, s in enumerate(scenes)]
    return concat(parts, out_path, work_dir)
