#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F1ニュースの検知とレースデータの取得（docs/04 2-2章・docs/05 3章）。

    # 当日の候補ニュースを集める（公式RSS＋日本語RSS）
    .venv/bin/python tools/fetch_f1.py --news

    # 最新レースのリザルトを見る（数字の裏付け）
    .venv/bin/python tools/fetch_f1.py --results
    .venv/bin/python tools/fetch_f1.py --standings

    # 一覧・採用・不採用
    .venv/bin/python tools/fetch_f1.py --list
    .venv/bin/python tools/fetch_f1.py --adopt f1-abc123
    .venv/bin/python tools/fetch_f1.py --reject f1-abc123

**記事の表現はコピーしない。** 事実の伝達は著作権の保護対象外（著作権法10条2項）だが、
記事の文章そのものは著作物。ここで集めるのは「何が起きたか」を知るための手がかりで、
台本は事実を自分の言葉で語る（docs/04 2-2章）。
**FOMの映像・画像・スクショは1フレームも使わない。**

数字は Jolpica-F1（Ergast後継・Apache 2.0・認証不要）から引く。
RSSの見出しだけでは順位や差が曖昧なままなので、数字は必ずAPIで裏を取る。
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError

NEWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "f1news")

# 公式を軸に、日本語圏の文脈（角田・ホンダ）を拾うために日本語RSSを併用する
FEEDS = [
    ("formula1", "https://www.formula1.com/en/latest/all.xml"),
    ("motorsport-jp", "https://jp.motorsport.com/rss/f1/news/"),
]

# Ergast後継。認証不要・Apache 2.0（docs/04 2-2章）
JOLPICA = "https://api.jolpi.ca/ergast/f1"

_UA = {"User-Agent": "personal-sns-operation/1.0 (content pipeline)"}


def _http(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _text(item: ET.Element, tag: str) -> str:
    return html.unescape((item.findtext(tag) or "").strip())


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _path(news_id: str) -> str:
    return os.path.join(NEWS_DIR, f"{news_id}.json")


def _save(data: dict) -> str:
    os.makedirs(NEWS_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(news_id: str) -> dict:
    path = _path(news_id)
    if not os.path.exists(path):
        raise PipelineError(f"候補がありません: {news_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_news() -> list:
    rows = []
    for name in sorted(os.listdir(NEWS_DIR)) if os.path.isdir(NEWS_DIR) else []:
        if name.endswith(".json"):
            with open(os.path.join(NEWS_DIR, name), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def load_adopted(news_id: str) -> dict:
    """採用済みニュースを1本返す。採用は人の目視で決める（docs/05 3章）。"""
    data = _load(news_id)
    if data.get("status") != "adopted":
        raise PipelineError(
            f"{news_id} は採用されていません（現在: {data.get('status')}）。\n"
            "  tools/fetch_f1.py --list で確認し、--adopt を付けてください。"
        )
    return data


def collect_news(limit: int) -> list:
    """各フィードの新着を候補にする。要約は「何の話か」を掴むためだけに保持する。"""
    known = {n["id"] for n in saved_news()}
    saved = []
    for source, url in FEEDS:
        try:
            root = ET.fromstring(_http(url))
        except (OSError, ET.ParseError) as e:
            print(f"  RSS取得に失敗（{source}）: {e}", file=sys.stderr)
            continue
        count = 0
        for item in root.iter("item"):
            title = _text(item, "title")
            if not title or count >= limit:
                continue
            news_id = "f1-" + hashlib.sha1(f"{source}:{title}".encode()).hexdigest()[:10]
            if news_id in known:
                continue
            saved.append(_save({
                "id": news_id,
                "source": source,
                "title": title,
                # 要約は記事の表現そのものなので、台本には流さず選定の材料に留める
                "summary": _strip_tags(_text(item, "description"))[:400],
                "url": _text(item, "link"),
                "published": _text(item, "pubDate"),
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "candidate",
            }))
            count += 1
    return saved


def mark(news_id: str, status: str) -> None:
    data = _load(news_id)
    data["status"] = status
    _save(data)


def _jolpica(path: str) -> dict:
    return json.loads(_http(f"{JOLPICA}/{path}.json"))["MRData"]


def last_results() -> dict:
    """最新レースの結果。台本の数字はここから引く（見出しの記憶で書かない）。"""
    races = _jolpica("current/last/results")["RaceTable"]["Races"]
    if not races:
        raise PipelineError("リザルトが取得できませんでした（シーズン開幕前の可能性があります）。")
    race = races[0]
    return {
        "race": race["raceName"],
        "round": race["round"],
        "season": race["season"],
        "circuit": race["Circuit"]["circuitName"],
        "date": race["date"],
        "results": [
            {
                "position": r["position"],
                "driver": f"{r['Driver']['givenName']} {r['Driver']['familyName']}",
                "code": r["Driver"].get("code", ""),
                "constructor": r["Constructor"]["name"],
                "points": r["points"],
                "status": r["status"],
                "time": (r.get("Time") or {}).get("time", ""),
            }
            for r in race["Results"]
        ],
    }


def standings() -> dict:
    """ドライバー・コンストラクターの選手権順位。「今どういう状況か」の背景に使う。"""
    d = _jolpica("current/driverStandings")["StandingsTable"]["StandingsLists"]
    c = _jolpica("current/constructorStandings")["StandingsTable"]["StandingsLists"]
    return {
        "round": d[0]["round"] if d else "",
        "season": d[0]["season"] if d else "",
        "drivers": [
            {"position": s["position"],
             "driver": f"{s['Driver']['givenName']} {s['Driver']['familyName']}",
             "points": s["points"], "wins": s["wins"],
             "constructor": s["Constructors"][0]["name"] if s["Constructors"] else ""}
            for s in (d[0]["DriverStandings"] if d else [])
        ],
        "constructors": [
            {"position": s["position"], "name": s["Constructor"]["name"],
             "points": s["points"], "wins": s["wins"]}
            for s in (c[0]["ConstructorStandings"] if c else [])
        ],
    }


def race_context() -> dict:
    """台本に渡す数字一式。取れない項目があってもニュース自体は作れるので止めない。"""
    context = {}
    for key, fn in (("last_race", last_results), ("standings", standings)):
        try:
            context[key] = fn()
        except (OSError, ValueError, KeyError, PipelineError) as e:
            print(f"  F1データの取得に失敗（{key}）: {e}", file=sys.stderr)
    return context


def show_list() -> None:
    rows = saved_news()
    if not rows:
        print("候補がありません。--news で集めてください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌"}
    for n in rows:
        print(f"{icons.get(n['status'], '?')} {n['id']}  [{n['source']}] {n['title'][:58]}")
    print(f"\n採用 {sum(n['status'] == 'adopted' for n in rows)} / "
          f"候補 {sum(n['status'] == 'candidate' for n in rows)} / "
          f"不採用 {sum(n['status'] == 'rejected' for n in rows)}")


def main() -> None:
    p = argparse.ArgumentParser(description="F1ニュースの検知とレースデータの取得")
    p.add_argument("--news", action="store_true", help="RSSから候補ニュースを集める")
    p.add_argument("--limit", type=int, default=10, help="1フィードあたりの取得数（既定10）")
    p.add_argument("--results", action="store_true", help="最新レースのリザルトを表示")
    p.add_argument("--standings", action="store_true", help="選手権順位を表示")
    p.add_argument("--list", action="store_true", help="候補一覧")
    p.add_argument("--adopt", metavar="ID", help="候補を採用にする")
    p.add_argument("--reject", metavar="ID", help="候補を不採用にする")
    args = p.parse_args()

    try:
        if args.list:
            show_list()
        elif args.adopt:
            mark(args.adopt, "adopted")
            print(f"採用: {args.adopt}")
        elif args.reject:
            mark(args.reject, "rejected")
            print(f"不採用: {args.reject}")
        elif args.results:
            data = last_results()
            print(f"{data['season']} R{data['round']} {data['race']}（{data['date']}）")
            for r in data["results"][:10]:
                print(f"  {r['position']:>2}. {r['driver']:<22} {r['constructor']:<18} "
                      f"{r['points']}pt {r['time'] or r['status']}")
        elif args.standings:
            data = standings()
            print(f"{data['season']} 第{data['round']}戦終了時点")
            print("ドライバー:")
            for s in data["drivers"][:8]:
                print(f"  {s['position']:>2}. {s['driver']:<22} {s['points']}pt（{s['wins']}勝）")
            print("コンストラクター:")
            for s in data["constructors"][:6]:
                print(f"  {s['position']:>2}. {s['name']:<22} {s['points']}pt")
        elif args.news:
            saved = collect_news(args.limit)
            print(f"{len(saved)}本を保存しました → data/f1news/")
            print("次: --list で眺めて --adopt / --reject を付けてください（採用は人の目視）")
        else:
            p.error("--news / --results / --standings / --list / --adopt / --reject "
                    "のいずれかを指定してください")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
