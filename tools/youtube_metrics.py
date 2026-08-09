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
| **登録者増（動画単位）** | **Analytics API** | 同上 |

YouTubeに「プロフィールアクセス」というmetricは無い。最も近いのが動画単位の
subscribersGained で、これを「この人が気になる」の指標として使う（docs/07 Phase 3）。

Analytics のスコープは既存トークンに含まれていないため、初回は再認可が要る。
無くても Data API の分だけ取れるようにしてある（判定のうち保存率・コメント率は成立する）。
"""
import argparse
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


def _analytics_rows(analytics, video_ids: list, since: str, until: str) -> dict:
    """動画IDごとの {完走率, 登録者増}。取れなければ空。

    YouTubeに「プロフィールアクセス」に相当するmetricは無い。
    最も近いのが動画単位の subscribersGained（この動画を見て登録した人）で、
    これが「情報が役に立った」から「この人が気になる」への移行を測る指標になる
    （docs/07 Phase 3）。
    """
    if not analytics or not video_ids:
        return {}
    try:
        res = analytics.reports().query(
            ids="channel==MINE", startDate=since, endDate=until,
            metrics="averageViewPercentage,subscribersGained,views",
            dimensions="video",
            filters="video==" + ",".join(video_ids[:200]),
            maxResults=200,
        ).execute()
    except Exception as e:                                  # noqa: BLE001
        # Analytics は反映が遅く、投稿直後は空や404になる。計測全体は止めない
        print(f"YouTube Analytics: 取得できませんでした（{e}）", file=sys.stderr)
        return {}
    headers = [h["name"] for h in res.get("columnHeaders", [])]
    out = {}
    for row in res.get("rows", []):
        rec = dict(zip(headers, row))
        video_id = rec.get("video")
        if not video_id:
            continue
        entry = {}
        avg_pct = rec.get("averageViewPercentage")
        if isinstance(avg_pct, (int, float)):
            entry["completion_rate"] = round(avg_pct / 100, 3)
        subs = rec.get("subscribersGained")
        if isinstance(subs, (int, float)):
            entry["follows_gained"] = int(subs)
        out[video_id] = entry
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

    # チャンネルごとに束ねる。分割後（#29）は**チャンネルごとに別のトークン**で、
    # 他チャンネルのトークンでは自分の動画として statistics も Analytics も引けない
    by_channel: dict[str, dict] = {}
    for filename, rec in ledger.items():
        video_id = (rec or {}).get("video_id")
        if not video_id:
            continue
        script = yt.script_for(os.path.join(yt.OUT_DIR, filename))
        # 台本はチャンネル別ディレクトリに移った。直下だけを見ると必ず空になり、
        # ジャンル別集計（2週間テストの判定そのもの）が全部「未設定」になる
        channel = yt.script_channel(script) or filename.split("-")[0]
        by_channel.setdefault(channel, {})[video_id] = (script or {}).get("genre", "") or channel

    now = datetime.now(JST)
    since_dt = now - timedelta(days=days)
    posts = []
    for channel, genre_of in sorted(by_channel.items()):
        try:
            service = yt.get_service(channel=channel)
        except PipelineError as e:
            print(f"YouTube[{channel}]: 認可が無いため飛ばします（{str(e).splitlines()[0]}）",
                  file=sys.stderr)
            continue
        # Analytics は別クライアントが要る。認可情報は同じチャンネルのトークンから読む
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(yt.token_path_for(channel))
        analytics = _analytics_service(creds)
        if analytics is None:
            print(f"YouTube Analytics[{channel}]: スコープが足りないため完走率は取れません。\n"
                  "  取りたい場合は `.venv/bin/python tools/publish_youtube.py --auth "
                  f"--as {channel}` で再認可してください（{ANALYTICS_SCOPE}）。", file=sys.stderr)

        ids = list(genre_of)
        found = []
        for i in range(0, len(ids), 50):                    # videos.list は50件ずつ
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
                found.append({
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

        rows = _analytics_rows(
            analytics, [p["_video_id"] for p in found],
            since_dt.date().isoformat(), now.date().isoformat(),
        )
        for p in found:
            p.update(rows.get(p["_video_id"], {}))
        posts.extend(found)
    return sorted(posts, key=lambda p: p["_dt"])


def main() -> None:
    p = argparse.ArgumentParser(description="YouTube Shortsの数字を取る")
    p.add_argument("--days", type=int, default=14)
    args = p.parse_args()
    try:
        for post in fetch(args.days):
            print(f"{post['post_date']} [{post['genre'] or '未設定'}] {post['title']} "
                  f"再生{post['views_total']} 完走率{post.get('completion_rate', '-')} "
                  f"登録増{post.get('follows_gained', '-')}")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
