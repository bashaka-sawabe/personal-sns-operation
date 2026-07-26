#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YouTube Shorts の数字を取る（Data API ＋ Analytics API）。

`fetch_metrics.py` から呼ばれる。単体でも動く:

    .venv/bin/python tools/youtube_metrics.py --days 14

Data API と Analytics API で取れるものが違う:

| 指標 | どこから | 必要なスコープ |
|---|---|---|
| 再生・いいね・コメント | Data API `videos.list` | youtube（取得済み） |
| **完走率（平均視聴率）** | **Analytics API** | **yt-analytics.readonly（未取得）** |

Analytics のスコープは既存トークンに含まれていないため、初回は再認可が要る。
無くても Data API の分だけ取れるようにしてある（判定のうち保存率・コメント率は成立する）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import publish_youtube as yt
from tools.pipeline.common import PipelineError

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
JST = timezone(timedelta(hours=9))


def _analytics_service(creds):
    """Analytics API のクライアント。スコープが無ければ None を返す。"""
    if not creds.has_scopes([ANALYTICS_SCOPE]):
        return None
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def _retention(analytics, video_ids: list, since: str, until: str) -> dict:
    """動画IDごとの平均視聴率（0〜1）。取れなければ空。"""
    if not analytics or not video_ids:
        return {}
    try:
        res = analytics.reports().query(
            ids="channel==MINE", startDate=since, endDate=until,
            metrics="averageViewPercentage,views",
            dimensions="video",
            filters="video==" + ",".join(video_ids[:200]),
            maxResults=200,
        ).execute()
    except Exception as e:                                  # noqa: BLE001
        # Analytics は反映が遅く、投稿直後は空や404になる。計測全体は止めない
        print(f"YouTube Analytics: 取得できませんでした（{e}）", file=sys.stderr)
        return {}
    out = {}
    for row in res.get("rows", []):
        video_id, avg_pct = row[0], row[1]
        if isinstance(avg_pct, (int, float)):
            out[video_id] = avg_pct / 100
    return out


def fetch(days: int = 14) -> list:
    """直近 days 日に投稿した自分のShortsの数字を返す。

    ジャンルは投稿台帳（content/out/.published_youtube.json）経由で
    台本JSONから引く。手入力に頼らないのは、ジャンル別集計が
    2週間テストの判定そのものだから（docs/06 2章）。
    """
    ledger = yt.load_ledger()
    if not ledger:
        print("YouTube: 投稿台帳が空です（まだ投稿していない）")
        return []

    # video_id -> ジャンル。台帳のキーは "<script_id>.mp4"
    genre_of = {}
    for filename, rec in ledger.items():
        video_id = (rec or {}).get("video_id")
        if not video_id:
            continue
        script_id = os.path.splitext(filename)[0]
        path = os.path.join(yt.SCRIPTS_DIR, f"{script_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                genre_of[video_id] = json.load(f).get("genre", "")

    service = yt.get_service()
    # Analytics は別クライアントが要る。認可情報は同じトークンファイルから読む
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(yt.secret_path(yt.TOKEN_FILE))
    analytics = _analytics_service(creds)
    if analytics is None:
        print("YouTube Analytics: スコープが足りないため完走率は取れません。\n"
              "  取りたい場合は `.venv/bin/python tools/publish_youtube.py --auth` で再認可してください"
              f"（{ANALYTICS_SCOPE}）。", file=sys.stderr)

    now = datetime.now(JST)
    since_dt = now - timedelta(days=days)
    ids = list(genre_of)
    posts = []
    for i in range(0, len(ids), 50):                        # videos.list は50件ずつ
        chunk = ids[i:i + 50]
        res = service.videos().list(
            part="snippet,statistics", id=",".join(chunk), maxResults=50,
        ).execute()
        for item in res.get("items", []):
            published = datetime.strptime(
                item["snippet"]["publishedAt"], "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=timezone.utc).astimezone(JST)
            if published < since_dt:
                continue
            stats = item.get("statistics", {})
            posts.append({
                "_dt": published,
                "_video_id": item["id"],
                "platform": "youtube_shorts",
                "genre": genre_of.get(item["id"], ""),
                "post_date": published.date().isoformat(),
                "title": item["snippet"]["title"][:40],
                "url": f"https://www.youtube.com/shorts/{item['id']}",
                "views_total": int(stats["viewCount"]) if "viewCount" in stats else None,
                "likes": int(stats["likeCount"]) if "likeCount" in stats else None,
                "comments": int(stats["commentCount"]) if "commentCount" in stats else None,
            })

    retention = _retention(
        analytics, [p["_video_id"] for p in posts],
        since_dt.date().isoformat(), now.date().isoformat(),
    )
    for p in posts:
        if p["_video_id"] in retention:
            p["completion_rate"] = round(retention[p["_video_id"]], 3)
    return posts


def main() -> None:
    p = argparse.ArgumentParser(description="YouTube Shortsの数字を取る")
    p.add_argument("--days", type=int, default=14)
    args = p.parse_args()
    try:
        for post in fetch(args.days):
            print(f"{post['post_date']} [{post['genre'] or '未設定'}] {post['title']} "
                  f"再生{post['views_total']} 完走率{post.get('completion_rate', '-')}")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
