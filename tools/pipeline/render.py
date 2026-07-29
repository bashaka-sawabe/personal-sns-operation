#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シーン素材 → 縦動画（ffmpeg）。

設計:
- シーンごとに独立した mp4 を書き出してから concat する。
  1本のフィルタグラフで全部やると、1シーン失敗しただけで全体が落ちる上に
  どのシーンが原因か分からない。分けておくと切り分けが効く（docs/09 4-5）。
- 各シーンの尺はナレーション音声の長さに従う（docs/09 4-6）。
- 字幕はASSで焼く。フレーズごとに音声と同期して表示し、数字は色を変える。
  drawtext ではなく ASS なのは、行内の色変え・ポップイン・複数スタイルの
  同時表示（見出し＋フレーズ）が1ファイルで済むため（docs/09 4-3）。
- BGMは素材がある場合のみ、サイドチェーンでダッキングして敷く（docs/09 4-4）。
"""
import os
import re
import subprocess

from .common import FPS, HEIGHT, WIDTH, ffmpeg, font_path, require, wrap_japanese

# 字幕の見た目。ショートは小さい画面で見られるので、太く・大きく・縁を厚く
FONT_FALLBACK = "Hiragino Kaku Gothic StdN W8"
SIZE_HOOK = 100        # シーン1の見出し（フック）。画面中央寄りに大きく
SIZE_HEAD = 76         # シーン2以降の見出し。画面上部に置き続ける
SIZE_LINE = 62         # フレーズ字幕（ナレーションと同期）
WRAP_HOOK = 9
WRAP_HEAD = 12
WRAP_LINE = 14
ACCENT = r"\1c&H00D7FF&"   # 数字の強調色（金）。ASSはBGR並び
WHITE = r"\1c&HFFFFFF&"
POP = r"{\fscx132\fscy132\t(0,110,\fscx100\fscy100)}"  # フレーズのポップイン

# フォールバック背景（静止画）用 Ken Burns
ZOOM_PER_FRAME = 0.0006
ZOOM_MAX = 1.12

# ストック映像の敷き込み。彩度を少し上げ、字幕のためにわずかに暗くする
_VIDEO_PREP = (
    f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
    f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
    "eq=contrast=1.05:saturation=1.12:brightness=-0.03"
)

# 数字＋単位をキーワードとして強調する。台本スキーマ上、勝負どころは常に数字
_NUM = re.compile(r"[0-9０-９][0-9０-９.,]*(?:[年月日割円万億兆時分秒人回つ個社]|[%％])?")

# fc-scan は毎シーン呼ぶほど安くないのでプロセス内で1回だけ引く
_font_cache = {"family": ""}


def _font_family() -> str:
    """ASSはフォントをファイルパスではなく名前で引くため、実名を調べる。"""
    if _font_cache["family"]:
        return _font_cache["family"]
    try:
        out = subprocess.run(
            [require("fc-scan"), "--format", "%{family}", font_path()],
            capture_output=True, text=True, timeout=10,
        ).stdout
        names = [n.strip() for n in out.split(",") if n.strip()]
        # 「W8」付きの英名を優先する。ウェイト名まで含めた方がlibassの解決が安定する
        picked = next((n for n in names if n.isascii() and "W8" in n), names[0] if names else "")
        _font_cache["family"] = picked or FONT_FALLBACK
    except (OSError, subprocess.SubprocessError):
        _font_cache["family"] = FONT_FALLBACK
    return _font_cache["family"]


def _ass_time(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str, per_line: int, highlight: bool = True) -> str:
    """折り返してASSのテキストにする。数字は強調色に変える。"""
    text = text.replace("{", "（").replace("}", "）").strip("、。 ")
    wrapped = wrap_japanese(text, per_line).replace("\n", r"\N")
    if highlight:
        wrapped = _NUM.sub(lambda m: "{%s}%s{%s}" % (ACCENT, m.group(0), WHITE), wrapped)
    return wrapped


def _scene_ass(scene: dict, index: int, path: str, font: str) -> str:
    """1シーン分の字幕（見出し＋フレーズ同期）を書き出す。"""
    hook = index == 0
    head_style = "Hook" if hook else "Head"
    head_wrap = WRAP_HOOK if hook else WRAP_HEAD
    dur = scene["dur"]

    events = [
        # 見出しはシーンの間ずっと出す。スクショ1枚でも意味が通るようにする
        f"Dialogue: 0,{_ass_time(0)},{_ass_time(dur)},{head_style},,0,0,0,,"
        + "{\\fad(150,0)}" + _ass_text(scene["caption"], head_wrap),
    ]
    t = 0.0
    for i, p in enumerate(scene["phrases"]):
        # 最後のフレーズはシーン末尾の余白まで出し続ける（先に消えると欠けて見える）
        end = dur if i == len(scene["phrases"]) - 1 else t + p["dur"]
        events.append(
            f"Dialogue: 0,{_ass_time(t)},{_ass_time(end)},Line,,0,0,0,,"
            + POP + _ass_text(p["text"], WRAP_LINE)
        )
        t += p["dur"]

    styles = "\n".join(
        f"Style: {name},{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},2,{align},60,60,{margin_v},1"
        for name, size, outline, align, margin_v in (
            ("Hook", SIZE_HOOK, 11, 8, 620),   # 上中央合わせで画面中央寄り
            ("Head", SIZE_HEAD, 9, 8, 210),    # 画面上部（UIに隠れない位置）
            ("Line", SIZE_LINE, 8, 2, 560),    # 下寄せ（キャプション欄を避ける）
        )
    )
    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _scene_audio(scene: dict, index: int, work_dir: str) -> str:
    """フレーズ音声を連結し、シーン尺まで無音を足す。"""
    listfile = os.path.join(work_dir, f"au{index:02d}.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in scene["phrases"]:
            f.write("file '%s'\n" % os.path.abspath(p["audio"]).replace("'", r"'\''"))
    out = os.path.join(work_dir, f"audio{index:02d}.wav")
    ffmpeg([
        "-f", "concat", "-safe", "0", "-i", listfile,
        "-af", f"apad=whole_dur={scene['dur']}", out,
    ])
    return out


def render_scene(scene: dict, index: int, work_dir: str) -> str:
    """1シーンを mp4 にする。背景が映像ならループで敷き、静止画ならKen Burnsで動かす。"""
    out = os.path.join(work_dir, f"scene{index:02d}.mp4")
    ass = _scene_ass(scene, index, os.path.join(work_dir, f"sub{index:02d}.ass"), _font_family())
    audio = _scene_audio(scene, index, work_dir)
    subs = f"ass='{ass}':fontsdir='{os.path.dirname(font_path())}'"

    if scene["bg_kind"] == "video":
        inputs = ["-stream_loop", "-1", "-i", scene["bg"], "-i", audio]
        vf = f"{_VIDEO_PREP},{subs}"
    else:
        inputs = ["-loop", "1", "-framerate", str(FPS), "-t", str(scene["dur"]),
                  "-i", scene["bg"], "-i", audio]
        # 先に2倍に拡大してからズームすることで、拡大時の劣化を避ける
        vf = (
            f"scale={WIDTH * 2}:{HEIGHT * 2},"
            f"zoompan=z='min(1+{ZOOM_PER_FRAME}*on,{ZOOM_MAX})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={WIDTH}x{HEIGHT}:fps={FPS},{subs}"
        )

    ffmpeg([
        *inputs,
        "-map", "0:v", "-map", "1:a",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(scene["dur"]), out,
    ])
    return out


def concat(parts: list, out_path: str, work_dir: str,
           bgm: str | None = None, total_dur: float = 0.0) -> str:
    """シーンmp4を連結する。BGMがあればダッキングして重ねる。"""
    listfile = os.path.join(work_dir, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in parts:
            # concat デミューサはシングルクォートをこの形式でしかエスケープできない
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))

    args = ["-f", "concat", "-safe", "0", "-i", listfile]
    if bgm:
        # ナレーションが鳴っている間だけBGMを下げる（サイドチェーン）。
        # 最後は全体をフェードアウトして切り上がりの唐突さを消す
        fade = f",afade=t=out:st={max(0.0, total_dur - 0.9)}:d=0.9" if total_dur else ""
        args += [
            "-stream_loop", "-1", "-i", bgm,
            "-filter_complex",
            "[1:a]volume=0.25[bgm];"
            "[bgm][0:a]sidechaincompress=threshold=0.02:ratio=12:attack=40:release=500[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:normalize=0{fade}[a]",
            "-map", "0:v", "-map", "[a]",
        ]
    ffmpeg([
        *args,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", out_path,
    ])
    return out_path


def build(scenes: list, out_path: str, work_dir: str, bgm: str | None = None) -> str:
    parts = [render_scene(s, i, work_dir) for i, s in enumerate(scenes)]
    total = sum(s["dur"] for s in scenes)
    return concat(parts, out_path, work_dir, bgm=bgm, total_dur=total)
