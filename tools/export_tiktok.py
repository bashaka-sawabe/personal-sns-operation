#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完成動画をTikTokの持ち出しフォルダへ書き出す（#277）。

    # 未書き出しの完成動画を全部（2回目は何も書き出さない）
    .venv/bin/python tools/export_tiktok.py --all

    # 1本だけ
    .venv/bin/python tools/export_tiktok.py content/out/meme-016.mp4

TikTokのContent Posting APIは未審査だと投稿が SELF_ONLY（本人しか見えない）に
固定されるため、**アップロード操作だけ本人・準備は全部ここ**で済ませる
（#275・docs/08 3章）。アカウントはチャンネル1:1なので、アップロード先
アカウントごとのフォルダ（content/out/tiktok/<channel>/）に仕分ける。

本人の操作: フォルダのmp4をPC版TikTok Studioに上げ、隣の同名.txtを
キャプション欄に貼り、AI生成ラベルをONにして予約投稿する（docs/08 3章の手順）。
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import publish_youtube as yt
from tools.pipeline.channels import CHANNELS_DIR
from tools.pipeline.common import OUT_DIR, PipelineError

TIKTOK_DIR = os.path.join(OUT_DIR, "tiktok")
LEDGER = os.path.join(OUT_DIR, ".published_tiktok.json")
JST = timezone(timedelta(hours=9))


def load_ledger() -> dict:
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(ledger: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def account_channels() -> set[str]:
    """TikTokアカウントがあるチャンネル（=現役チャンネル）。

    廃止チャンネル（trivia等）の動画を持ち出しフォルダに混ぜると、本人が誤って
    アップしかねない。アカウント構成はチャンネル1:1（#276）なので、
    チャンネル設定の存在をそのまま「アップロード先がある」判定に使う。
    """
    return {os.path.splitext(f)[0] for f in os.listdir(CHANNELS_DIR)
            if f.endswith(".json")} if os.path.isdir(CHANNELS_DIR) else set()


def video_channel(video_path: str, script: dict | None) -> str | None:
    """仕分け先のチャンネル。台本が正、無ければファイル名の接頭辞で補う。"""
    return yt.script_channel(script) or (
        os.path.basename(video_path).split("-")[0] if "-" in os.path.basename(video_path)
        else None)


def build_caption(video_path: str, script: dict | None) -> str:
    """キャプション＝caption＋ハッシュタグ＋クレジットの3段（build_metadataと同型）。

    VOICEVOXのクレジット表記は音源規約の利用条件そのもの（docs/08 2章）。
    無い動画を持ち出せてしまうと規約違反の投稿が物理的に可能になるので、
    ここでエラーにして止める。
    """
    stem = os.path.splitext(os.path.basename(video_path))[0]
    credits = yt.read_credits(stem)
    if not credits:
        raise PipelineError(
            f"{stem}: credits.txt がありません（content/assets/{stem}/credits.txt）。"
            "VOICEVOXのクレジット表記は利用条件なので、無いまま持ち出せません。"
            "make_video.py --from-script で作り直してください。"
        )
    if not script:
        return credits
    return "\n\n".join(filter(None, [
        script.get("caption", ""),
        " ".join(script.get("hashtags", [])),
        credits,
    ]))


def export_one(video_path: str, ledger: dict) -> str:
    """1本を仕分けフォルダへ書き出し、台帳に記録して書き出し先を返す。"""
    name = os.path.basename(video_path)
    if name in ledger:
        raise PipelineError(
            f"{name}: 書き出し済みです（{ledger[name].get('exported_at', '')}）。")
    if not os.path.exists(video_path):
        raise PipelineError(f"動画が見つかりません: {video_path}")

    script = yt.script_for(video_path)
    # 投稿と同じく持ち出しも取り消しが効かない（本人がそのまま上げる）ので、
    # 未記入プレースホルダと明滅は書き出す前に止める
    reason = yt.blocking_reason(script) or yt.flicker_reason(video_path)
    if reason:
        raise PipelineError(f"{name}: {reason}")

    channel = video_channel(video_path, script)
    if channel not in account_channels():
        raise PipelineError(
            f"{name}: チャンネル「{channel}」のTikTokアカウントがありません"
            "（現役チャンネルのみ書き出します）。"
        )

    caption = build_caption(video_path, script)
    dest_dir = os.path.join(TIKTOK_DIR, channel)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    shutil.copy2(video_path, dest)
    stem = os.path.splitext(name)[0]
    with open(os.path.join(dest_dir, f"{stem}.txt"), "w", encoding="utf-8") as f:
        f.write(caption + "\n")

    ledger[name] = {
        "channel": channel,
        "exported_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    save_ledger(ledger)
    return dest


def export_all() -> tuple[list[str], list[tuple[str, str]]]:
    """未書き出しの完成動画を全部書き出す。(書き出し先, 弾いた一覧) を返す。"""
    ledger = load_ledger()
    targets = sorted(
        os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR)
        if f.endswith(".mp4") and f not in ledger
    ) if os.path.isdir(OUT_DIR) else []

    exported, skipped = [], []
    for path in targets:
        # 1本の不備で残りを道連れにしない。弾いた理由は末尾でまとめて出す
        try:
            exported.append(export_one(path, ledger))
        except PipelineError as e:
            skipped.append((os.path.basename(path), str(e)))
    return exported, skipped


def main() -> None:
    p = argparse.ArgumentParser(description="完成動画をTikTok持ち出しフォルダへ書き出す")
    p.add_argument("video", nargs="?", help="書き出す動画ファイル")
    p.add_argument("--all", action="store_true",
                   help="content/out/ の未書き出しを全部書き出す")
    args = p.parse_args()

    if args.all:
        exported, skipped = export_all()
        for dest in exported:
            print(f"書き出し: {os.path.relpath(dest)}")
        for name, reason in skipped:
            print(f"見送り: {reason}")
        if not exported and not skipped:
            print("未書き出しの動画はありません。")
        print(f"TikTok持ち出し: {len(exported)}本 → {os.path.relpath(TIKTOK_DIR)}/")
        return

    if not args.video:
        p.error("動画ファイルか --all を指定してください")
    ledger = load_ledger()
    name = os.path.basename(args.video)
    if name in ledger:
        print(f"書き出し済み: {name}（{ledger[name].get('exported_at', '')}）")
        return
    dest = export_one(args.video, ledger)
    print(f"書き出し: {os.path.relpath(dest)}")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
