#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シーン素材 → 縦動画（ffmpeg）。

設計:
- シーンごとに独立した mp4 を書き出してから concat する。
  1本のフィルタグラフで全部やると、1シーン失敗しただけで全体が落ちる上に
  どのシーンが原因か分からない。分けておくと切り分けが効く（docs/09 4-6）。
- 各シーンの尺はナレーション音声の長さに従う（docs/09 4-7）。
- 字幕はASSで焼く。フレーズごとに音声と同期して表示し、数字は色を変える。
  drawtext ではなく ASS なのは、行内の色変え・ポップイン・複数スタイルの
  同時表示（見出し＋フレーズ）が1ファイルで済むため（docs/09 4-3）。
- BGMは素材がある場合のみ、サイドチェーンでダッキングして敷く（docs/09 4-4）。
"""
import os
import re
import subprocess

from .channels import CHARACTERS
from .common import FPS, HEIGHT, WIDTH, ffmpeg, font_path, require, wrap_japanese

# 字幕の見た目。ショートは小さい画面で見られるので、太く・大きく・縁を厚く
FONT_FALLBACK = "Hiragino Kaku Gothic StdN W8"
SIZE_HOOK = 100        # シーン1の見出し（フック）。画面中央寄りに大きく
SIZE_HEAD = 76         # シーン2以降の見出し。画面上部に置き続ける
SIZE_LINE = 62         # フレーズ字幕（セリフと同期。話者の色で塗る）
WRAP_HOOK = 9
WRAP_HEAD = 12
WRAP_LINE = 14
ACCENT = r"\1c&H00D7FF&"   # 数字の強調色（金）。ASSはBGR並び
WHITE_BGR = "FFFFFF"
POP = r"{\fscx132\fscy132\t(0,110,\fscx100\fscy100)}"  # フレーズのポップイン

# 立ち絵。左右の下端に置き、話しているキャラだけ明るくする（誰のセリフか一目で分かる）
CHAR_HEIGHT = 560      # 画面の約3割。字幕（下寄せ・MarginV=560）とは重ならない高さ
CHAR_MARGIN_X = 10
CHAR_MARGIN_Y = 16
# 非発話側の減光。輪郭は残しつつ「今は聞き役」と分かる暗さ（アルファは保つ）
CHAR_DIM = "format=rgba,colorchannelmixer=rr=0.5:gg=0.5:bb=0.5"

# 静止画背景の Ken Burns。
# 中央固定の微ズームは「動いていない」と見える（毎秒1.8%・1フレーム0.06%では目が拾えない上に、
# 中心が動かないと画面の端に手がかりが出ない）。シーンごとに寄り／引きとパンの向きを変え、
# 端が流れる量を付ける（docs/09 4-5）。
# (ズーム開始, ズーム終了, x開始, x終了, y開始, y終了)。位置は取り得る範囲に対する 0〜1 の割合
MOTIONS = [
    (1.05, 1.24, 0.35, 0.62, 0.50, 0.50),  # 寄りながら右へ
    (1.24, 1.05, 0.50, 0.50, 0.34, 0.62),  # 引きながら下へ
    (1.05, 1.24, 0.65, 0.38, 0.50, 0.50),  # 寄りながら左へ
    (1.24, 1.05, 0.50, 0.50, 0.66, 0.38),  # 引きながら上へ
]

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


def _ken_burns(index: int, dur: float) -> str:
    """シーン番号で動きを変える zoompan を組み立てる。

    zoompan の x/y は切り出し矩形の左上を入力座標で指すので、可動域 (iw - iw/zoom) に
    割合を掛ける。ズームが1.0まで戻ると可動域が0になってパンが止まるため、
    下限は1.05に取ってある。
    """
    z0, z1, x0, x1, y0, y1 = MOTIONS[index % len(MOTIONS)]
    frames = max(1, int(round(dur * FPS)))
    p = f"min(on/{frames},1)"
    return (
        f"zoompan=z='{z0}+{z1 - z0:.4f}*{p}'"
        f":x='(iw-iw/zoom)*({x0}+{x1 - x0:.4f}*{p})'"
        f":y='(ih-ih/zoom)*({y0}+{y1 - y0:.4f}*{p})'"
        f":d=1:s={WIDTH}x{HEIGHT}:fps={FPS}"
    )


def _ass_time(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str, per_line: int, highlight: bool = True,
              base_bgr: str = WHITE_BGR) -> str:
    """折り返してASSのテキストにする。数字は強調色に変え、話者の色へ戻す。"""
    text = text.replace("{", "（").replace("}", "）").strip("、。 ")
    wrapped = wrap_japanese(text, per_line).replace("\n", r"\N")
    if highlight:
        reset = r"\1c&H" + base_bgr + "&"
        wrapped = _NUM.sub(lambda m: "{%s}%s{%s}" % (ACCENT, m.group(0), reset), wrapped)
    return wrapped


def _speaker_windows(scene: dict) -> dict:
    """話者ごとの発話区間 [(start, end), ...]。立ち絵の明滅に使う。"""
    wins, t = {}, 0.0
    n = len(scene["phrases"])
    for i, p in enumerate(scene["phrases"]):
        # 最後のフレーズはシーン末尾の余白まで（字幕の表示と揃える）
        end = scene["dur"] if i == n - 1 else t + p["dur"]
        wins.setdefault(p["speaker"], []).append((t, end))
        t += p["dur"]
    return wins


def _scene_ass(scene: dict, index: int, path: str, font: str) -> str:
    """1シーン分の字幕（見出し＋セリフ同期）を書き出す。セリフは話者の色で塗る。"""
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
            f"Dialogue: 0,{_ass_time(t)},{_ass_time(end)},Line_{p['speaker']},,0,0,0,,"
            + POP + _ass_text(p["text"], WRAP_LINE,
                              base_bgr=CHARACTERS[p["speaker"]]["color_bgr"])
        )
        t += p["dur"]

    # 話者ごとのセリフスタイル。色の違いは立ち絵の明滅と対で「誰の声か」を伝える
    speaker_styles = [
        (f"Line_{key}", SIZE_LINE, 8, 2, 560, CHARACTERS[key]["color_bgr"])
        for key in sorted({p["speaker"] for p in scene["phrases"]})
    ]
    styles = "\n".join(
        f"Style: {name},{font},{size},&H00{color},&H00{color},&H00000000,&H78000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},2,{align},60,60,{margin_v},1"
        for name, size, outline, align, margin_v, color in (
            ("Hook", SIZE_HOOK, 11, 8, 620, WHITE_BGR),   # 上中央合わせで画面中央寄り
            ("Head", SIZE_HEAD, 9, 8, 210, WHITE_BGR),    # 画面上部（UIに隠れない位置）
            *speaker_styles,                              # 下寄せ（キャプション欄を避ける）
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


def _enable_expr(windows: list) -> str:
    """overlay の enable 式。複数区間は between の和で表す（非0で有効）。"""
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)


def render_scene(scene: dict, index: int, work_dir: str, cast: list | None = None) -> str:
    """1シーンを mp4 にする。背景が映像ならループで敷き、静止画ならKen Burnsで動かす。

    cast は [(話者キー, 立ち絵パス), ...]（cast の並び順に 左・右 へ置く）。
    立ち絵は常時2体を減光して置き、発話中のキャラだけ通常の明るさを重ねる。
    素材が無いキャラは黙って省く（劣化継続。素材の置き場は docs/09 4-8）。
    """
    out = os.path.join(work_dir, f"scene{index:02d}.mp4")
    ass = _scene_ass(scene, index, os.path.join(work_dir, f"sub{index:02d}.ass"), _font_family())
    audio = _scene_audio(scene, index, work_dir)
    subs = f"ass='{ass}':fontsdir='{os.path.dirname(font_path())}'"

    if scene["bg_kind"] == "video":
        inputs = ["-stream_loop", "-1", "-i", scene["bg"], "-i", audio]
        base_chain = _VIDEO_PREP
    else:
        inputs = ["-loop", "1", "-framerate", str(FPS), "-t", str(scene["dur"]),
                  "-i", scene["bg"], "-i", audio]
        # 任意アスペクト比の写真が来るので、まずcover-cropで縦画角に切り出す。
        # 2倍で作業するのはズーム時の劣化を避けるため
        base_chain = (
            f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH * 2}:{HEIGHT * 2},"
            + _ken_burns(index, scene["dur"]) + ","
            "eq=contrast=1.04:saturation=1.08:brightness=-0.04"
        )

    chars = [(key, img) for key, img in (cast or []) if img]
    windows = _speaker_windows(scene)
    graph = [f"[0:v]{base_chain}[v0]"]
    last = "v0"
    for n, (key, img) in enumerate(chars):
        inputs += ["-i", img]
        idx = 2 + n  # 0=背景, 1=音声 の後ろに立ち絵が並ぶ
        # 左右の下端。cast の並び順で 0=左, 1=右（3体以上は想定しない=配役2人の前提）
        x = f"{CHAR_MARGIN_X}" if n == 0 else f"main_w-overlay_w-{CHAR_MARGIN_X}"
        y = f"main_h-overlay_h-{CHAR_MARGIN_Y}"
        graph.append(f"[{idx}:v]scale=-2:{CHAR_HEIGHT}[c{n}]")
        graph.append(f"[c{n}]split[c{n}on][c{n}pre]")
        graph.append(f"[c{n}pre]{CHAR_DIM}[c{n}off]")
        # 減光した2体を常時敷いてから、発話区間だけ通常の明るさを重ねる
        graph.append(f"[{last}][c{n}off]overlay=x={x}:y={y}[d{n}]")
        last = f"d{n}"
        wins = windows.get(key)
        if wins:
            graph.append(f"[{last}][c{n}on]overlay=x={x}:y={y}:enable='{_enable_expr(wins)}'[b{n}]")
            last = f"b{n}"
    graph.append(f"[{last}]{subs}[vout]")

    ffmpeg([
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(scene["dur"]), out,
    ])
    return out


def concat(parts: list, out_path: str, work_dir: str,
           bgm: str | None = None, total_dur: float = 0.0) -> str:
    """シーンmp4を連結する。BGMがあればダッキングして重ねる。

    映像は再エンコードしない。シーンmp4は同一パラメータのh264なので copy で繋がる上に、
    BGMのフィルタグラフと libx264 を同時に走らせると音声が末尾2秒ほど早くEOFする
    （実行ごとに切れる位置がブレる＝ffmpeg側の競合。docs/09 4-4）。
    copy にすると再現しなくなり、世代劣化も無くなる。
    """
    listfile = os.path.join(work_dir, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in parts:
            # concat デミューサはシングルクォートをこの形式でしかエスケープできない
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))

    args = ["-f", "concat", "-safe", "0", "-i", listfile]
    audio = ["-c:a", "copy"]
    if bgm:
        # ナレーションが鳴っている間だけBGMを下げる（サイドチェーン）。
        # 最後は全体をフェードアウトして切り上がりの唐突さを消す
        fade = f",afade=t=out:st={max(0.0, total_dur - 0.9)}:d=0.9" if total_dur else ""
        # BGMは無限ループのままにせず尺で打ち切る。少し長めに取るのは、
        # total_dur が四捨五入値でここが短いと末尾のBGMが先に切れるため。
        # 出力長は amix の duration=first ＝ シーン音声側で決まる
        loop = ["-stream_loop", "-1"] + (["-t", f"{total_dur + 1:.3f}"] if total_dur else [])
        args += [
            *loop, "-i", bgm,
            "-filter_complex",
            "[1:a]volume=0.25[bgm];"
            "[bgm][0:a]sidechaincompress=threshold=0.02:ratio=12:attack=40:release=500[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:normalize=0{fade}[a]",
            "-map", "0:v", "-map", "[a]",
        ]
        audio = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]
    ffmpeg([*args, "-c:v", "copy", *audio, "-movflags", "+faststart", out_path])
    return out_path


def build(scenes: list, out_path: str, work_dir: str, bgm: str | None = None,
          cast: list | None = None) -> str:
    parts = [render_scene(s, i, work_dir, cast=cast) for i, s in enumerate(scenes)]
    total = sum(s["dur"] for s in scenes)
    return concat(parts, out_path, work_dir, bgm=bgm, total_dur=total)
