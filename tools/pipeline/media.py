#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シーン素材（背景画像・ナレーション音声）の生成。

背景は2系統ある:
  1. ローカル生成（既定）— ffmpeg のグラデーション。無料・即時・失敗しない。
     情報系ショートは字幕が主役なので、背景は「邪魔をしない」ことが要件であり、
     これで十分成立する。テスト期の回転数を最大化するのが目的。
  2. 画像生成API — 当たったフォーマットが決まってから差し替える。
     テスト段階で1本あたり数十円を払う理由がないため、意図的に後回しにしている。

音声も同様に、まず macOS の `say`（無料・オフライン）で回し、
ジャンルが確定してから本人の声または高品質TTSに差し替える。
"""
import os

from .common import HEIGHT, WIDTH, PipelineError, ffmpeg, probe_duration, require, run

# 落ち着いたダーク基調。白の極太字幕とのコントラストを最優先に選んである
PALETTE = [
    ("0x1a1a2e", "0x16213e"),
    ("0x1b2430", "0x2d4059"),
    ("0x231b2e", "0x3b2c47"),
    ("0x14262c", "0x1f3d3a"),
    ("0x2b1d1d", "0x40282a"),
]


def background(index: int, path: str) -> str:
    """シーン番号に応じた背景画像を作る。連番で色が変わり、単調さを避ける。"""
    c0, c1 = PALETTE[index % len(PALETTE)]
    # グラデーション → わずかにノイズを載せてベタ塗り感を消す
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


def narration(text: str, path: str, voice: str = "Kyoko", rate: int = 180) -> str:
    """ナレーション音声を作る。

    macOS の `say` を使う。無料・オフライン・十分聞ける品質で、テスト期には最適。
    TikTokのAI生成ラベル対象になり得るため、ジャンル確定後は本人の声に差し替える方針
    （docs/07_ロードマップ.md の S3）。
    """
    aiff = path + ".aiff"
    try:
        run([require("say"), "-v", voice, "-r", str(rate), "-o", aiff, text])
    except PipelineError:
        # 音声 Kyoko が入っていない環境では既定音声にフォールバック
        run([require("say"), "-r", str(rate), "-o", aiff, text])
    # ffmpeg で扱いやすいよう 44.1kHz モノラル wav に揃える
    ffmpeg(["-i", aiff, "-ar", "44100", "-ac", "1", path])
    os.remove(aiff)
    return path


def build_scene_assets(script: dict, asset_dir: str) -> list:
    """台本の全シーン分の素材を作り、[{bg, audio, caption, dur}] を返す。"""
    scenes = []
    for i, scene in enumerate(script["scenes"]):
        bg = background(i, os.path.join(asset_dir, f"bg{i:02d}.png"))
        audio = narration(scene["narration"], os.path.join(asset_dir, f"na{i:02d}.wav"))
        # 尺は音声に合わせる。読み終わりで即カットすると詰まって聞こえるので余白を足す
        dur = round(probe_duration(audio) + 0.45, 2)
        scenes.append({
            "bg": bg,
            "audio": audio,
            "caption": scene["caption"],
            "dur": dur,
        })
    return scenes
