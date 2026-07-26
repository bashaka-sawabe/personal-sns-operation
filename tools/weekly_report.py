#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
週次CSVから週次レビュー用のレポート（Markdown）を作る。必要ならGitHub Issueにコメントする。

    python3 tools/weekly_report.py                  # 今週のレポートを標準出力に表示
    python3 tools/weekly_report.py --week 2026-W31  # 週を指定
    python3 tools/weekly_report.py --last-week      # 先週分
    python3 tools/weekly_report.py --issue 42       # レポートをIssue #42 にコメント

集計の考え方は docs/06_KPI・運用.md のKPIツリーに合わせている:
- 北極星は「保存率」「完走率」「コメント率」（コメント率はトーク型＝ピラーAの主指標）。
  フォロワー数は遅行指標として併記のみ
- ピラー別（A/B）の比較を必ず出す。Phase 1の最重要目標は
  「ピラーA/Bのフォーマット優劣がデータで語れる状態」

IssueコメントにはPATが要る。GH_PAT または ~/dev/.cowork-secrets/gh_token_personal.txt
から読む（fine-grained/classicどちらでも可）。
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SECRETS_DIR = os.path.expanduser("~/dev/.cowork-secrets")
JST = timezone(timedelta(hours=9))

OWNER = "bashaka-sawabe"
REPO = "personal-sns-operation"
API = "https://api.github.com"

# docs/06_KPI・運用.md 6章のラベル定義に対応（pillar:classic はv2で廃止）
PILLAR_LABELS = {
    "sakanaction": "A. ファントーク×ピアノ",
    "datalab": "B. AI×サカナクション研究",
}


def week_str(dt: datetime) -> str:
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def prev_week_str(week: str) -> str:
    """ISO週の文字列から前週の文字列を返す（年またぎもISO週の定義どおりに処理する）。"""
    year, num = week.split("-W")
    monday = datetime.fromisocalendar(int(year), int(num), 1)
    return week_str(monday - timedelta(days=7))


def load_week(week: str) -> list:
    path = os.path.join(DATA_DIR, f"{week}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("url") or "").strip()]


def num(row: dict, key: str) -> float:
    raw = (row.get(key) or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def has(row: dict, key: str) -> bool:
    return bool((row.get(key) or "").strip())


def rate_per_1k(rows: list, key: str) -> float:
    """1,000再生あたりの件数（保存率・コメント率）。再生が0なら0を返す。"""
    views = sum(num(r, "views_total") for r in rows)
    if views <= 0:
        return 0.0
    return sum(num(r, key) for r in rows) / views * 1000


def mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt(value: float, digits: int = 1) -> str:
    if value == 0:
        return "-"
    return f"{value:,.{digits}f}"


def pct(value: float) -> str:
    """0〜1の比率をパーセント表示にする。未記録（0）は「-」だけにして「-%」にしない。"""
    if value == 0:
        return "-"
    return f"{value * 100:,.1f}%"


def delta(current: float, previous: float) -> str:
    """前週比。前週データが無い（0）なら空欄にする。"""
    if previous <= 0:
        return ""
    diff = current - previous
    sign = "+" if diff >= 0 else ""
    return f"（前週比 {sign}{diff:,.0f}）"


def build_report(week: str, rows: list, prev_rows: list) -> str:
    views = sum(num(r, "views_total") for r in rows)
    prev_views = sum(num(r, "views_total") for r in prev_rows)
    follows = sum(num(r, "follows_gained") for r in rows)
    completions = [num(r, "completion_rate") for r in rows if has(r, "completion_rate")]

    lines = [
        f"## 週次レポート {week}",
        "",
        f"投稿 **{len(rows)}本**（前週 {len(prev_rows)}本） ／ 合計再生 **{views:,.0f}** {delta(views, prev_views)}",
        "",
        "### 北極星指標",
        "",
        "| 指標 | 今週 | 前週 | 目標（docs/06） |",
        "|---|---|---|---|",
        f"| 保存率（/1,000再生） | {fmt(rate_per_1k(rows, 'saves'), 2)} | "
        f"{fmt(rate_per_1k(prev_rows, 'saves'), 2)} | ピラー間で比較 |",
        f"| 完走率（記録済みの平均） | {pct(mean(completions))} | "
        f"{pct(mean([num(r, 'completion_rate') for r in prev_rows if has(r, 'completion_rate')]))} | 60%超の型を1つ確立 |",
        f"| コメント率（/1,000再生） | {fmt(rate_per_1k(rows, 'comments'), 2)} | "
        f"{fmt(rate_per_1k(prev_rows, 'comments'), 2)} | 共感コメントが安定して付く型を見つける |",
        f"| フォロワー増（合計） | {fmt(follows, 0)} | "
        f"{fmt(sum(num(r, 'follows_gained') for r in prev_rows), 0)} | Phase 1で3PF計1,000 |",
        "",
    ]

    lines += [
        "### ピラー別（Phase 1の判定材料）", "",
        "| ピラー | 本数 | 再生 | 保存率 | コメント率 | 完走率 |", "|---|---|---|---|---|---|",
    ]
    by_pillar = {}
    for row in rows:
        by_pillar.setdefault((row.get("pillar") or "未設定").strip() or "未設定", []).append(row)
    # A→B→Cの順に並べ、未設定など想定外のpillarは末尾にまとめる
    order = list(PILLAR_LABELS) + sorted(k for k in by_pillar if k not in PILLAR_LABELS)
    for pillar in [k for k in order if k in by_pillar]:
        pillar_rows = by_pillar[pillar]
        rates = [num(r, "completion_rate") for r in pillar_rows if has(r, "completion_rate")]
        lines.append(
            f"| {PILLAR_LABELS.get(pillar, pillar)} | {len(pillar_rows)} | "
            f"{sum(num(r, 'views_total') for r in pillar_rows):,.0f} | "
            f"{fmt(rate_per_1k(pillar_rows, 'saves'), 2)} | "
            f"{fmt(rate_per_1k(pillar_rows, 'comments'), 2)} | {pct(mean(rates))} |"
        )
    lines.append("")

    lines += ["### 投稿別", "", "| 投稿 | PF | 初速(48h) | 再生 | 保存 | シェア | コメント | 平均視聴 |", "|---|---|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: -num(r, "views_total")):
        title = row.get("title") or "(タイトル未設定)"
        url = (row.get("url") or "").strip()
        label = f"[{title}]({url})" if url.startswith("http") else title
        avg = num(row, "avg_watch_sec")
        lines.append(
            f"| {label} | {row.get('platform', '')} | {fmt(num(row, 'views_48h'), 0)} | "
            f"{fmt(num(row, 'views_total'), 0)} | {fmt(num(row, 'saves'), 0)} | "
            f"{fmt(num(row, 'shares'), 0)} | {fmt(num(row, 'comments'), 0)} | "
            f"{f'{avg:.1f}秒' if avg else '-'} |"
        )
    lines.append("")

    hypotheses = [(r.get("title") or "", r["hypothesis"].strip()) for r in rows if has(r, "hypothesis")]
    if hypotheses:
        lines += ["### 検証中の仮説", ""]
        lines += [f"- **{title}**: {text}" for title, text in hypotheses]
        lines.append("")

    todo = []
    if any(not has(r, "pillar") for r in rows):
        todo.append("`pillar` 未設定の行がある（ピラー別比較に入らない）")
    if any(not has(r, "completion_rate") for r in rows):
        todo.append("`completion_rate` 未入力の行がある（各PFのアナリティクスから手入力）")
    if any(not has(r, "follows_gained") for r in rows):
        todo.append("`follows_gained` 未入力の行がある（APIで取れないため手入力）")
    if not any(r.get("platform") == "tiktok" for r in rows) and rows:
        todo.append("TikTokの行が無い（APIで取得できないため手入力。docs/08_自動化.md 3章）")
    if todo:
        lines += ["### 手入力が必要な項目", ""] + [f"- [ ] {t}" for t in todo] + [""]

    lines += [
        "### 週次レビューの記入欄（docs/06 4章）",
        "",
        "- 仮説の検証結果（1行）: ",
        "- 来週の打ち手: ",
        "",
        f"<sub>`tools/weekly_report.py` が生成 ／ 元データ: `data/{week}.csv`</sub>",
    ]
    return "\n".join(lines)


def github_token() -> str:
    token = os.environ.get("GH_PAT", "").strip()
    if token:
        return token
    path = os.path.join(SECRETS_DIR, "gh_token_personal.txt")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    sys.exit("PATが見つかりません。GH_PAT を設定するか ~/dev/.cowork-secrets/gh_token_personal.txt を配置してください。")


def post_comment(issue_number: int, body: str) -> None:
    url = f"{API}/repos/{OWNER}/{REPO}/issues/{issue_number}/comments"
    req = urllib.request.Request(url, method="POST", data=json.dumps({"body": body}).encode())
    req.add_header("Authorization", f"Bearer {github_token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Issue #{issue_number} にコメントしました: {json.loads(r.read().decode()).get('html_url', '')}")
    except urllib.error.HTTPError as e:
        sys.exit(f"コメント投稿に失敗しました HTTP {e.code}: {e.read().decode()[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="週次CSVからレビュー用レポートを作る")
    parser.add_argument("--week", help="対象週（例 2026-W31）。既定は実行日の属する週")
    parser.add_argument("--last-week", action="store_true", help="先週を対象にする")
    parser.add_argument("--issue", type=int, help="このIssue番号にレポートをコメントする")
    parser.add_argument("--out", help="Markdownをファイルに書き出す")
    args = parser.parse_args()

    week = args.week or week_str(datetime.now(JST))
    if args.last_week:
        week = prev_week_str(week)

    rows = load_week(week)
    if not rows:
        sys.exit(
            f"data/{week}.csv が無いか、投稿行がありません。\n"
            "先に `python3 tools/fetch_metrics.py` を実行してください。"
        )

    report = build_report(week, rows, load_week(prev_week_str(week)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"{args.out} に書き出しました")
    else:
        print(report)

    if args.issue:
        post_comment(args.issue, report)


if __name__ == "__main__":
    main()
