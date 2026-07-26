#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ショート動画を1本作る（台本 → 素材 → 縦動画）。

    # 1本作る
    python3 tools/make_video.py --genre money --theme "経費で落とせるもの3選"

    # APIキー無しで疎通確認（テンプレ台本＋ローカル素材だけで最後まで通す）
    python3 tools/make_video.py --genre money --theme "テスト" --offline

    # 3ジャンル分をまとめて（2週間テストの本体）
    python3 tools/make_video.py --batch data/themes.tsv

    # 台本だけ先に作って、中身を見てから動画化する
    python3 tools/make_video.py --genre money --theme "..." --script-only
    python3 tools/make_video.py --from-script content/scripts/money-001.json

出力:
    content/scripts/<id>.json   台本（手直しして再実行できる）
    content/assets/<id>/        背景・音声・シーンmp4（中間物）
    content/out/<id>.mp4        完成した縦動画

前提: ffmpeg（必須）／台本生成に anthropic SDK と ANTHROPIC_API_KEY
      （どちらも無ければ --offline 相当で動く）
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline import media, render, script as script_mod
from tools.pipeline.common import (
    ASSETS_DIR, OUT_DIR, SCRIPTS_DIR, PipelineError, ensure_dirs,
)


def slugify(genre: str, theme: str) -> str:
    """ファイル名に使える id を作る。日本語は落ちるので連番で担保する。"""
    base = re.sub(r"[^a-z0-9]+", "-", f"{genre}".lower()).strip("-") or "video"
    n = 1
    while os.path.exists(os.path.join(SCRIPTS_DIR, f"{base}-{n:03d}.json")):
        n += 1
    return f"{base}-{n:03d}"


def build_from_script(data: dict, script_id: str) -> str:
    asset_dir = os.path.join(ASSETS_DIR, script_id)
    ensure_dirs(asset_dir, OUT_DIR)
    out_path = os.path.join(OUT_DIR, f"{script_id}.mp4")

    print(f"  素材を生成中（{len(data['scenes'])}シーン）...")
    scenes = media.build_scene_assets(data, asset_dir)
    total = sum(s["dur"] for s in scenes)
    print(f"  合成中（尺 {total:.1f}秒）...")
    render.build(scenes, out_path, asset_dir)
    return out_path


def make_one(genre: str, theme: str, offline: bool, script_only: bool) -> str:
    ensure_dirs(SCRIPTS_DIR)
    script_id = slugify(genre, theme)
    print(f"[{script_id}] {genre} / {theme}")

    print("  台本を生成中...")
    data = script_mod.generate(genre, theme, offline=offline)
    data["genre"] = genre
    data["theme"] = theme
    path = script_mod.save(data, script_id, SCRIPTS_DIR)
    print(f"  台本: {os.path.relpath(path)}")
    if script_only:
        return path
    return build_from_script(data, script_id)


def read_themes(path: str) -> list:
    """1行1件の `ジャンル<TAB>テーマ`。# 始まりと空行は無視する。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\t+|\s{2,}", line, maxsplit=1)
            if len(parts) != 2:
                print(f"  スキップ（形式不正）: {line}", file=sys.stderr)
                continue
            rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="ショート動画を生成する")
    p.add_argument("--genre", help="ジャンル（money / psychology / history など）")
    p.add_argument("--theme", help="テーマ（動画1本の中身）")
    p.add_argument("--batch", help="ジャンルとテーマの一覧ファイル（1行1本）")
    p.add_argument("--from-script", help="既存の台本JSONから動画だけ作り直す")
    p.add_argument("--offline", action="store_true", help="APIを一切使わず疎通確認する")
    p.add_argument("--script-only", action="store_true", help="台本だけ作って止める")
    args = p.parse_args()

    try:
        if args.from_script:
            data = script_mod.load(args.from_script)
            script_id = data.get("id") or os.path.splitext(os.path.basename(args.from_script))[0]
            print(f"[{script_id}] 台本から再生成")
            print(f"完成: {os.path.relpath(build_from_script(data, script_id))}")
            return

        if args.batch:
            rows = read_themes(args.batch)
            if not rows:
                sys.exit(f"{args.batch} に有効な行がありません。")
            print(f"{len(rows)}本を生成します\n")
            done, failed = [], []
            for genre, theme in rows:
                try:
                    done.append(make_one(genre, theme, args.offline, args.script_only))
                except PipelineError as e:
                    # 1本の失敗でバッチ全体を止めない。残りを作り切ってから報告する
                    print(f"  失敗: {e}\n", file=sys.stderr)
                    failed.append((genre, theme))
            print(f"\n完了: {len(done)}本 / 失敗: {len(failed)}本")
            for genre, theme in failed:
                print(f"  - {genre} / {theme}", file=sys.stderr)
            return

        if not args.genre or not args.theme:
            p.error("--genre と --theme（または --batch / --from-script）を指定してください")

        out = make_one(args.genre, args.theme, args.offline, args.script_only)
        print(f"完成: {os.path.relpath(out)}")

    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
