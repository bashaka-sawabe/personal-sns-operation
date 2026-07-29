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

STOCK_DIR = os.path.join(ASSETS_DIR, "stock")
PEXELS_SEARCH = "https://api.pexels.com/videos/search"

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
# 既定は青山龍星（ノーマル）。落ち着いた男声で「大人のメモ帳」のトーンに合わせている
VOICEVOX_SPEAKER = int(os.environ.get("VOICEVOX_SPEAKER", "13"))
# 1.0だと間延びする。ショートの標準的な語速に寄せる
VOICEVOX_SPEED = 1.1
# エンジンの置き場候補。GUI版（VOICEVOX.app）にも同じエンジンが同梱されている
VOICEVOX_ENGINES = [
    os.path.expanduser("~/.voicevox/macos-arm64/run"),
    os.path.expanduser("~/.voicevox/macos-x64/run"),
    "/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run",
]

# エンジンの起動は1プロセスに1回で足りるのでモジュール内に持つ
_voicevox = {"checked": False, "up": False, "speaker_name": ""}


def _http(url: str, method: str = "GET", body: bytes | None = None,
          headers: dict | None = None, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


# ---------------------------------------------------------------- VOICEVOX

def _voicevox_alive() -> bool:
    try:
        _http(f"{VOICEVOX_URL}/version", timeout=2)
        return True
    except OSError:
        return False


def _voicevox_speaker_name(speaker: int) -> str:
    """クレジット表記用のキャラ名。取れなくても合成は続ける。"""
    try:
        speakers = json.loads(_http(f"{VOICEVOX_URL}/speakers", timeout=5))
        for s in speakers:
            for style in s.get("styles", []):
                if style.get("id") == speaker:
                    return s.get("name", "")
    except (OSError, ValueError):
        pass
    return ""


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
    if _voicevox["up"]:
        _voicevox["speaker_name"] = _voicevox_speaker_name(VOICEVOX_SPEAKER)
    else:
        print("  VOICEVOXエンジンが見つからないため say で代用します（投稿品質ではありません）",
              file=sys.stderr)
    return _voicevox["up"]


def voicevox_credit() -> str:
    """クレジット表記（利用条件）。VOICEVOXを使っていなければ空。"""
    if _voicevox["up"] and _voicevox["speaker_name"]:
        return f"VOICEVOX:{_voicevox['speaker_name']}"
    return ""


def _voicevox_wav(text: str, path: str, speaker: int) -> None:
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    query = json.loads(_http(f"{VOICEVOX_URL}/audio_query?{q}", method="POST"))
    query["speedScale"] = VOICEVOX_SPEED
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


def narration(text: str, path: str) -> str:
    """ナレーション1フレーズ分の音声を作り、44.1kHzモノラルwavで返す。

    末尾に短い無音を足す。フレーズ間が詰まって聞こえるのを防ぐと同時に、
    「音声の長さ＝字幕の表示時間」の余韻にもなる（docs/09 4-3）。
    """
    raw = path + ".raw"
    if ensure_voicevox():
        try:
            _voicevox_wav(text, raw + ".wav", VOICEVOX_SPEAKER)
            os.rename(raw + ".wav", raw)
        except (OSError, ValueError):
            _say_wav(text, raw + ".aiff")
            os.rename(raw + ".aiff", raw)
    else:
        _say_wav(text, raw + ".aiff")
        os.rename(raw + ".aiff", raw)
    ffmpeg(["-i", raw, "-af", "apad=pad_dur=0.12", "-ar", "44100", "-ac", "1", path])
    os.remove(raw)
    return path


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
    """Pexelsからストック映像を1本取る。取れない理由が何であれ None（グラデへ）。"""
    query = _stock_query(image_prompt)
    if not query:
        return None
    cached = os.path.join(STOCK_DIR, hashlib.sha1(query.encode()).hexdigest()[:16] + ".mp4")
    if os.path.exists(cached):
        return cached

    try:
        q = urllib.parse.urlencode({
            "query": query, "orientation": "portrait", "size": "medium", "per_page": 3,
        })
        res = json.loads(_http(f"{PEXELS_SEARCH}?{q}", headers={"Authorization": api_key}))
        for video in res.get("videos", []):
            f = _pick_video_file(video)
            if not f:
                continue
            os.makedirs(STOCK_DIR, exist_ok=True)
            data = _http(f["link"], timeout=120)
            with open(cached, "wb") as fp:
                fp.write(data)
            return cached
    except (OSError, ValueError) as e:
        print(f"  ストック映像の取得に失敗（{query}）: {e}", file=sys.stderr)
    return None


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


def background(index: int, image_prompt: str, asset_dir: str,
               api_key: str, offline: bool) -> dict:
    """背景素材を返す。{"path": ..., "kind": "video" | "image"}"""
    if api_key and not offline:
        path = stock_background(image_prompt, api_key)
        if path:
            return {"path": path, "kind": "video"}
    return {"path": gradient_background(index, os.path.join(asset_dir, f"bg{index:02d}.png")),
            "kind": "image"}


# ---------------------------------------------------------------- 組み立て

def build_scene_assets(script: dict, asset_dir: str, offline: bool = False) -> list:
    """台本の全シーン分の素材を作る。

    返り値: [{bg, bg_kind, caption, phrases: [{text, audio, dur}], dur}]
    フレーズごとに音声を分けるのは、字幕を音声に同期させるため（docs/09 4-3）。
    """
    api_key = read_secret("PEXELS_API_KEY", "pexels_key.txt")
    if not api_key and not offline:
        print("  Pexels APIキーが無いためグラデ背景で代用します（投稿品質ではありません。#50）",
              file=sys.stderr)

    scenes, stock_hits = [], 0
    for i, scene in enumerate(script["scenes"]):
        bg = background(i, scene.get("image_prompt", ""), asset_dir, api_key, offline)
        stock_hits += bg["kind"] == "video"
        phrases = []
        for j, text in enumerate(split_phrases(scene["narration"])):
            audio = narration(text, os.path.join(asset_dir, f"na{i:02d}_{j:02d}.wav"))
            phrases.append({"text": text, "audio": audio, "dur": probe_duration(audio)})
        # 読み終わりで即カットすると詰まって聞こえるのでシーン末尾に余白を足す
        dur = round(sum(p["dur"] for p in phrases) + 0.35, 2)
        scenes.append({
            "bg": bg["path"],
            "bg_kind": bg["kind"],
            "caption": scene["caption"],
            "phrases": phrases,
            "dur": dur,
        })

    _write_credits(asset_dir, stock_hits)
    voice = voicevox_credit() or "macOS say（フォールバック）"
    print(f"  背景: ストック映像 {stock_hits}/{len(scenes)}シーン ／ 音声: {voice}")
    return scenes


def _write_credits(asset_dir: str, stock_hits: int) -> None:
    """投稿時の説明文に入れるクレジットを書き出す（VOICEVOXは表記が利用条件）。"""
    lines = []
    if voicevox_credit():
        lines.append(voicevox_credit())
    if stock_hits:
        lines.append("映像素材: Pexels")
    with open(os.path.join(asset_dir, "credits.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def bgm_track(seed: str) -> str | None:
    """BGM音源。content/assets/bgm/ に本人が置いた曲から決定的に選ぶ（同じ動画は同じ曲）。"""
    tracks = sorted(
        p for ext in ("mp3", "m4a", "wav", "aac")
        for p in glob.glob(os.path.join(ASSETS_DIR, "bgm", f"*.{ext}"))
    )
    if not tracks:
        return None
    return tracks[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(tracks)]
