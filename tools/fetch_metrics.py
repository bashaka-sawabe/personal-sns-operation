#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube / Threads / Instagram の数字を取得し、data/YYYY-Www.csv を作成・更新する。

    python3 tools/fetch_metrics.py                # 直近14日の投稿を取得して該当週のCSVを更新
    python3 tools/fetch_metrics.py --no-youtube   # YouTubeを飛ばす（再認可を促されたくないとき）
    python3 tools/fetch_metrics.py --days 30      # 取得範囲を変える
    python3 tools/fetch_metrics.py --dry-run      # CSVを書かずに取得結果だけ表示
    python3 tools/fetch_metrics.py --refresh-token  # 長期トークンを延長して保存し直す（月1回）

トークン（いずれも「長期トークン」。取得手順は docs/08_自動化.md）:
  Threads   : THREADS_TOKEN  または ~/repo/.cowork-secrets/threads_token.txt
  Instagram : IG_TOKEN       または ~/repo/.cowork-secrets/ig_token.txt
              （任意）IG_USER_ID または ~/repo/.cowork-secrets/ig_user_id.txt ※未設定なら me を使う

仕様メモ:
- 手入力列（issue_no / follows_gained / hypothesis / result_note）は既存値を温存する
- genre（money / relationship / trivia）はYouTubeなら投稿台帳から自動で入る。
  Threads/Instagramは投稿ツール実装後に同じ仕組みを入れる（それまでは手入力）
- views_48h は「投稿から48時間以内に実行したとき」だけ記録し、以後は上書きしない（初速の記録）
- 投稿は post_date のISO週で振り分けるため、複数週のCSVが同時に更新されることがある
- TikTokは Content Posting / Display API が審査必須で個人運用では実質使えないため対象外。
  手入力で行を足す運用にする（docs/08_自動化.md 3章）
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)          # 直接実行でも tools.youtube_metrics を読めるように

DATA_DIR = os.path.join(ROOT, "data")
# 環境ごとに場所が違うので候補を順に探す（~/dev/ から ~/repo/ に移動した実績あり）
SECRETS_DIRS = [
    os.path.expanduser("~/repo/.cowork-secrets"),
    os.path.expanduser("~/dev/.cowork-secrets"),
    os.path.expanduser("~/.cowork-secrets"),
]
JST = timezone(timedelta(hours=9))

# data/template.csv と同じ並び
COLUMNS = [
    "week", "post_date", "platform", "genre", "issue_no", "title", "url",
    "views_48h", "views_total", "completion_rate", "avg_watch_sec",
    "saves", "shares", "comments", "likes", "follows_gained",
    "hypothesis", "result_note",
]
# 人が書く列。APIの値で上書きしない。
# genre はYouTubeなら投稿台帳から自動で埋まるので、ここには入れない
# （ジャンル別集計が判定の本体で、手入力に頼ると埋まらないまま判定日が来る）
MANUAL_COLUMNS = {
    "issue_no", "follows_gained", "hypothesis", "result_note",
}

THREADS_BASE = "https://graph.threads.net/v1.0"
# https://developers.facebook.com/docs/threads/insights
THREADS_METRICS = ["views", "likes", "replies", "reposts", "quotes", "shares"]

# Instagram Login（Business Login for Instagram）を前提にする。
# Facebook Login経由の場合は graph.facebook.com + ページ連携が必要で手順が重い（docs/08）。
IG_BASE = "https://graph.instagram.com/v23.0"
IG_METRICS_COMMON = ["views", "likes", "comments", "saved", "shares", "total_interactions", "reach"]
IG_METRICS_REELS = IG_METRICS_COMMON + ["ig_reels_avg_watch_time"]


class ApiError(RuntimeError):
    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail[:300]}")
        self.code = code
        self.detail = detail


def secret_path(filename: str) -> str:
    """既存ファイルがあればそのパス、無ければ最優先候補（＝新規保存先）を返す。"""
    for d in SECRETS_DIRS:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return os.path.join(SECRETS_DIRS[0], filename)


def read_secret(env_name: str, filename: str) -> str:
    """環境変数を優先し、無ければ ~/repo/.cowork-secrets/ のファイルを読む。無ければ空文字。"""
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    p = secret_path(filename)
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def api_get(url: str, params: dict) -> dict:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode()) from None


def parse_insights(res: dict) -> dict:
    """insightsのレスポンスを {metric名: 数値} に均す（total_value形式とvalues形式の両方に対応）。"""
    out = {}
    for item in res.get("data", []):
        name = item.get("name")
        if "total_value" in item:
            value = item["total_value"].get("value")
        else:
            values = item.get("values") or [{}]
            value = values[-1].get("value")
        if isinstance(value, (int, float)):
            out[name] = value
    return out


def fetch_insights(base: str, obj_id: str, metrics: list, token: str) -> dict:
    """まとめて取得し、失敗したら1metricずつ試して取れた分だけ返す。

    Meta系APIは投稿種別やAPIバージョンで対応metricが変わり、1つでも非対応が混ざると
    リクエスト全体が400になる。仕様変更で計測が丸ごと止まるのを避けるためのフォールバック。
    """
    try:
        return parse_insights(api_get(f"{base}/{obj_id}/insights", {
            "metric": ",".join(metrics), "access_token": token,
        }))
    except ApiError:
        out = {}
        for m in metrics:
            try:
                out.update(parse_insights(api_get(f"{base}/{obj_id}/insights", {
                    "metric": m, "access_token": token,
                })))
            except ApiError:
                continue
        return out


def to_jst(timestamp: str) -> datetime:
    """ISO8601（例: 2026-07-28T12:34:56+0000）をJSTのdatetimeにする。"""
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(JST)


def title_of(text: str) -> str:
    one_line = " ".join((text or "").split())
    return one_line[:40]


def fetch_threads(token: str, since_ts: int) -> list:
    res = api_get(f"{THREADS_BASE}/me/threads", {
        "fields": "id,media_type,media_product_type,permalink,text,timestamp",
        "since": since_ts, "limit": 100, "access_token": token,
    })
    posts = []
    for m in res.get("data", []):
        ins = fetch_insights(THREADS_BASE, m["id"], THREADS_METRICS, token)
        dt = to_jst(m["timestamp"])
        posts.append({
            "_dt": dt,
            "platform": "threads",
            "post_date": dt.date().isoformat(),
            "title": title_of(m.get("text")),
            "url": m.get("permalink") or m["id"],
            "views_total": ins.get("views"),
            "likes": ins.get("likes"),
            "comments": ins.get("replies"),
            # Threadsに「保存」指標は無い。拡散は再投稿＋引用＋シェアの合計で見る
            "shares": sum(ins.get(k, 0) for k in ("reposts", "quotes", "shares")) or None,
        })
    return posts


def fetch_instagram(token: str, user_id: str, since_ts: int) -> list:
    res = api_get(f"{IG_BASE}/{user_id}/media", {
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp",
        "since": since_ts, "limit": 100, "access_token": token,
    })
    posts = []
    for m in res.get("data", []):
        is_reels = m.get("media_product_type") == "REELS"
        metrics = IG_METRICS_REELS if is_reels else IG_METRICS_COMMON
        ins = fetch_insights(IG_BASE, m["id"], metrics, token)
        dt = to_jst(m["timestamp"])
        avg_ms = ins.get("ig_reels_avg_watch_time")
        posts.append({
            "_dt": dt,
            "platform": "instagram_reels" if is_reels else "instagram",
            "post_date": dt.date().isoformat(),
            "title": title_of(m.get("caption")),
            "url": m.get("permalink") or m["id"],
            "views_total": ins.get("views"),
            # ミリ秒で返るため秒に直す。完走率は動画尺がAPIで取れないので手入力（docs/06）
            "avg_watch_sec": round(avg_ms / 1000, 1) if isinstance(avg_ms, (int, float)) else None,
            "saves": ins.get("saved"),
            "shares": ins.get("shares"),
            "comments": ins.get("comments"),
            "likes": ins.get("likes"),
        })
    return posts


def week_of(dt: datetime) -> str:
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def load_rows(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(path: str, rows: list) -> None:
    rows.sort(key=lambda r: (r.get("post_date", ""), r.get("platform", "")))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


def merge_post(rows: list, post: dict, now: datetime) -> tuple:
    """既存行があれば数値を更新、無ければ追加する。(行, 新規かどうか) を返す。"""
    row = next((r for r in rows if r.get("url") == post["url"]), None)
    is_new = row is None
    if is_new:
        row = {c: "" for c in COLUMNS}
        rows.append(row)

    row["week"] = week_of(post["_dt"])
    for column in COLUMNS:
        if column in MANUAL_COLUMNS or column not in post:
            continue
        value = post[column]
        if value is None:
            continue
        if column == "views_48h":
            continue
        row[column] = str(value)

    # 初速は「投稿48時間以内に実行できたとき」に一度だけ記録する
    already = (row.get("views_48h") or "").strip()
    within_48h = now - post["_dt"] <= timedelta(hours=48)
    if within_48h and already in ("", "0") and post.get("views_total") is not None:
        row["views_48h"] = str(post["views_total"])
    return row, is_new


def refresh_tokens() -> None:
    """長期トークン（60日で失効）を延長する。ファイルから読んだ場合は書き戻す。"""
    targets = [
        ("Threads", "THREADS_TOKEN", "threads_token.txt",
         "https://graph.threads.net/refresh_access_token", "th_refresh_token"),
        ("Instagram", "IG_TOKEN", "ig_token.txt",
         "https://graph.instagram.com/refresh_access_token", "ig_refresh_token"),
    ]
    for label, env_name, filename, url, grant_type in targets:
        token = read_secret(env_name, filename)
        if not token:
            print(f"- {label}: トークン未設定のためスキップ")
            continue
        try:
            res = api_get(url, {"grant_type": grant_type, "access_token": token})
        except ApiError as e:
            print(f"- {label}: 延長に失敗（{e}）。再発行が必要かもしれません（docs/08_自動化.md）")
            continue
        new_token = res.get("access_token", "")
        days = int(res.get("expires_in", 0)) // 86400
        path = secret_path(filename)
        if new_token and os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_token + "\n")
            print(f"- {label}: 延長して {path} に保存しました（あと約{days}日）")
        elif new_token:
            print(f"- {label}: 延長しました（あと約{days}日）。環境変数 {env_name} を新しい値に更新してください:\n  {new_token}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Threads/Instagramのインサイトを週次CSVに記録する")
    parser.add_argument("--days", type=int, default=14, help="何日前までの投稿を取得するか（既定14）")
    parser.add_argument("--dry-run", action="store_true", help="CSVを書かずに結果だけ表示する")
    parser.add_argument("--refresh-token", action="store_true", help="長期トークンを延長して保存し直す")
    parser.add_argument("--no-youtube", action="store_true", help="YouTubeの取得を飛ばす")
    args = parser.parse_args()

    if args.refresh_token:
        refresh_tokens()
        return

    now = datetime.now(JST)
    since_ts = int((now - timedelta(days=args.days)).timestamp())

    posts = []
    if not args.no_youtube:
        try:
            from tools import youtube_metrics
            fetched = youtube_metrics.fetch(args.days)
            posts += fetched
            print(f"YouTube: {len(fetched)}件取得")
        except Exception as e:                              # noqa: BLE001
            # 1PFの失敗で他PFの計測まで止めない（docs/08 7章）
            print(f"YouTube: 取得に失敗しました（{e}）", file=sys.stderr)

    threads_token = read_secret("THREADS_TOKEN", "threads_token.txt")
    ig_token = read_secret("IG_TOKEN", "ig_token.txt")
    if not threads_token and not ig_token and not posts:
        sys.exit(
            "トークンが見つかりません。docs/08_自動化.md の手順で長期トークンを取得し、\n"
            f"  {secret_path('threads_token.txt')}\n"
            f"  {secret_path('ig_token.txt')}\n"
            "に置くか、環境変数 THREADS_TOKEN / IG_TOKEN を設定してください。"
        )

    if threads_token:
        try:
            fetched = fetch_threads(threads_token, since_ts)
            posts += fetched
            print(f"Threads: {len(fetched)}件取得")
        except ApiError as e:
            print(f"Threads: 取得に失敗しました（{e}）", file=sys.stderr)
    else:
        print("Threads: トークン未設定のためスキップ")

    if ig_token:
        ig_user_id = read_secret("IG_USER_ID", "ig_user_id.txt") or "me"
        try:
            fetched = fetch_instagram(ig_token, ig_user_id, since_ts)
            posts += fetched
            print(f"Instagram: {len(fetched)}件取得")
        except ApiError as e:
            print(f"Instagram: 取得に失敗しました（{e}）", file=sys.stderr)
    else:
        print("Instagram: トークン未設定のためスキップ")

    if not posts:
        print("対象期間に投稿がありませんでした。")
        return

    by_week = {}
    for post in posts:
        by_week.setdefault(week_of(post["_dt"]), []).append(post)

    os.makedirs(DATA_DIR, exist_ok=True)
    needs_input = []
    for week, week_posts in sorted(by_week.items()):
        path = os.path.join(DATA_DIR, f"{week}.csv")
        rows = load_rows(path)
        added = 0
        for post in sorted(week_posts, key=lambda p: p["_dt"]):
            row, is_new = merge_post(rows, post, now)
            added += 1 if is_new else 0
            if not (row.get("genre") or "").strip():
                needs_input.append(f"{row['post_date']} {row['platform']} {row['title']}")
        if args.dry_run:
            print(f"[dry-run] {path}: 新規{added}件 / 更新{len(week_posts) - added}件")
            continue
        save_rows(path, rows)
        print(f"{os.path.relpath(path, ROOT)}: 新規{added}件 / 更新{len(week_posts) - added}件")

    if needs_input:
        print("\nジャンル未設定の行（judgeに入らないので埋めること）:")
        for item in needs_input:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
