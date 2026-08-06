#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1日ぶんの動画を生成して限定公開まで投稿する（#168）。

    # 今日の在庫と投稿計画だけ見る
    .venv/bin/python tools/daily_run.py --dry-run

    # 生成→限定公開投稿まで自動で回す（公開への切り替えは本人がやる）
    .venv/bin/python tools/daily_run.py

    # 毎日回すなら crontab に1行（17時に生成、18時に本人がレビューして公開）:
    #   0 17 * * * cd /Users/bashaka/repo/personal/personal-sns-operation && .venv/bin/python tools/daily_run.py >> logs/daily_run.log 2>&1

設計（docs/08 の線引きに従う）:
- **ネタは人が採用（目視選別）したものだけを使う。** 在庫が足りない日は本数を落として
  その旨を報告する。自動で候補を採用しない（人間の視点がAI量産対策の本体）。
- 投稿は**限定公開まで**。公開切り替え（publish_youtube.py --release）は本人がやる。
- YouTubeのクォータは動画1本1,600ユニット・1プロジェクト1日10,000ユニット＝**6本**。
  チャンネル別の youtube_client_secret_<ch>.json を置いてプロジェクトを分けると
  その分だけ上限が増える。無い間は6本で止まり、残りは翌日に回る（黙って超えない）。
- heisei の日次は1ネタの掛け合い型。6選（fact6個消費）は在庫を食い潰すので
  手動の特別編としてのみ作る。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import OUT_DIR, PipelineError, secret_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
THREADS_DIR = os.path.join(REPO, "data", "threads")
FACTS_DIR = os.path.join(REPO, "data", "facts")

# 各チャンネル毎日3本が目標（本人指示 2026-08-06）
TARGET_PER_CHANNEL = 3
CHANNELS = ["meme", "trivia", "heisei", "showa"]
# 1プロジェクト1日のアップロード上限（10,000ユニット ÷ 1,600ユニット/本）
UPLOADS_PER_PROJECT = 6
SHARED_CLIENT_SECRET = "youtube_client_secret.json"


def _load_all(directory: str) -> list[dict]:
    items = []
    for p in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["_path"] = p
        items.append(d)
    return items


def stock_for(channel: str) -> list[dict]:
    """チャンネルの未消費ネタ（人が採用済みのもの）を返す。"""
    if channel == "meme":
        return [t for t in _load_all(THREADS_DIR) if t["status"] == "adopted"]
    prefix = {"trivia": "til-", "heisei": "heisei-", "showa": "showa-"}[channel]
    return [f for f in _load_all(FACTS_DIR)
            if f["status"] == "adopted" and os.path.basename(f["_path"]).startswith(prefix)]


def mark_used(item: dict) -> None:
    """消費したネタに印を付ける（同じネタで2本作らないため）。"""
    path = item.pop("_path")
    item["status"] = "used"
    item["used_at"] = date.today().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False, indent=2)


def project_of(channel: str) -> str:
    """チャンネルが属するGoogle Cloudプロジェクト（クォータの単位）を返す。

    チャンネル専用の client secret があれば独立プロジェクト、無ければ共有。
    """
    own = secret_path(f"youtube_client_secret_{channel}.json")
    return channel if os.path.exists(own) else SHARED_CLIENT_SECRET


def _run(args: list[str]) -> tuple[int, str]:
    res = subprocess.run(args, capture_output=True, text=True, cwd=REPO)
    return res.returncode, (res.stdout + res.stderr)


def generate(channel: str, item: dict) -> str | None:
    """1本生成して動画パスを返す。失敗は None（理由は呼び出し側で表示済みの出力から）。"""
    if channel == "meme":
        args = [PYTHON, "tools/make_video.py", "--channel", channel, "--thread", item["id"]]
    else:
        args = [PYTHON, "tools/make_video.py", "--channel", channel, "--fact", item["id"]]
    code, out = _run(args)
    m = re.search(r"完成: (\S+\.mp4)", out)
    if code != 0 or not m:
        print(f"  生成失敗（{channel} / {item['id']}）:")
        print("    " + out.strip().splitlines()[-1] if out.strip() else "    (出力なし)")
        return None
    return os.path.join(REPO, m.group(1))


def upload(video: str) -> str | None:
    """限定公開で投稿してURLを返す。"""
    code, out = _run([PYTHON, "tools/publish_youtube.py", video])
    m = re.search(r"完了: (https://\S+)", out)
    if code != 0 or not m:
        print(f"  投稿失敗（{os.path.basename(video)}）:")
        print("    " + (out.strip().splitlines()[-1] if out.strip() else "(出力なし)"))
        return None
    return m.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="1日ぶんの生成と限定公開投稿をまとめて回す")
    parser.add_argument("--dry-run", action="store_true", help="在庫と計画だけ表示する")
    parser.add_argument("--per-channel", type=int, default=TARGET_PER_CHANNEL,
                        help=f"1チャンネルの本数（既定{TARGET_PER_CHANNEL}）")
    args = parser.parse_args()

    plan: list[tuple[str, dict]] = []
    quota_left: dict[str, int] = {}
    short_stock: list[str] = []
    deferred = 0

    stocks = {ch: stock_for(ch) for ch in CHANNELS}
    for ch, stock in stocks.items():
        if len(stock) < args.per_channel:
            short_stock.append(f"{ch}: 在庫{len(stock)}本（目標{args.per_channel}本）")
        quota_left.setdefault(project_of(ch), UPLOADS_PER_PROJECT)

    # クォータが足りない日でも特定チャンネルが0本にならないよう、1本ずつ順に取る
    for round_i in range(args.per_channel):
        for ch in CHANNELS:
            if round_i >= len(stocks[ch]):
                continue
            project = project_of(ch)
            if quota_left[project] <= 0:
                deferred += 1
                continue
            quota_left[project] -= 1
            plan.append((ch, stocks[ch][round_i]))

    print(f"本日の計画: {len(plan)}本")
    for ch, item in plan:
        label = item.get("title") or item.get("fact", "")[:40]
        print(f"  {ch}: {item['id']}  {label}")
    if deferred:
        print(f"クォータ都合で翌日回し: {deferred}本"
              f"（プロジェクト分割かクォータ引き上げで解消できます。docs/09 2-5章）")
    if short_stock:
        print("⚠️ ネタ在庫が目標に足りません。fetch_threads / fetch_facts で採用を増やしてください:")
        for line in short_stock:
            print(f"  {line}")
    if args.dry_run or not plan:
        return

    posted: list[str] = []
    for ch, item in plan:
        print(f"[{ch}] {item['id']} を生成中...")
        video = generate(ch, item)
        if not video:
            continue
        mark_used(item)
        url = upload(video)
        if url:
            posted.append(f"{ch}: {url}")

    print(f"\n投稿完了（限定公開）: {len(posted)}本")
    for line in posted:
        print(f"  {line}")
    if posted:
        print("\nレビュー後の公開切り替え:")
        print("  .venv/bin/python tools/publish_youtube.py --release <video_id> ...")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
