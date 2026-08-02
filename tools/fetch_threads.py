#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""おーぷん2ちゃんねるからスレを収集し、台本のネタ候補にする（docs/04 2-2章）。

    # 板から候補を集める（レス数が乗っているスレを新しい順に）
    .venv/bin/python tools/fetch_threads.py --board livejupiter

    # ブラウザで見つけたスレを直接取り込む（open2ch以外のURLは拒否される）
    .venv/bin/python tools/fetch_threads.py --url "https://hayabusa.open2ch.net/test/read.cgi/livejupiter/1785665449/"

    # 候補を眺めて、採用・不採用を付ける（採用したものだけが台本生成の入力になる）
    .venv/bin/python tools/fetch_threads.py --list
    .venv/bin/python tools/fetch_threads.py --adopt livejupiter-1785665449
    .venv/bin/python tools/fetch_threads.py --reject livejupiter-1785665449

引用元をおーぷん2ちゃんねるに限定する理由: 投稿がパブリックドメイン（転載自由）と
規約に明記されている唯一の主要掲示板だから。5chは運営の許可制、ガールズちゃんねる等は
許諾なき転載が不可（docs/04 2-2章の線引き表）。ここ以外のドメインはコードで拒否する。
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError

THREADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "threads")

# 引用してよいドメイン。これ以外は理由の如何を問わず拒否する（docs/04 2-2章）
ALLOWED_DOMAIN = "open2ch.net"

# 板 → サーバのサブドメイン。おーぷんの主要板はほぼ hayabusa に載っている
BOARDS = {
    "livejupiter": "hayabusa",   # なんでも実況（おんJ）。雑談・実話系の主力
    "news4vip": "hayabusa",      # VIP。ネタ・大喜利系
}

# 相手サーバーに負荷をかけない取得間隔（秒）
FETCH_INTERVAL = 1.5

# 候補にするレス数の範囲。少なすぎると展開もオチも無く、
# 多すぎるとショートの尺（15〜40秒）に刈り込めない
MIN_RES = 10
MAX_RES = 400

# 保存するレス数の上限。選定と翻案に必要なのは序盤〜中盤の流れで、
# 長寿スレの後半は同じ話の繰り返しになりがち
KEEP_RES = 120

# 素直なUAで名乗る。datはHTTP/2のTLS指紋がCloudflareのチャレンジ対象になるが、
# urllibはHTTP/1.1なのでそのまま通る（curlで再現するときは --http1.1 が要る）
_UA = {"User-Agent": "personal-sns-operation/1.0 (content pipeline)"}


def _check_domain(url: str) -> None:
    """引用元の規約線引き（docs/04 2-2章）をコードで守る。ここは絶対に緩めない。"""
    host = urllib.parse.urlparse(url).hostname or ""
    if host != ALLOWED_DOMAIN and not host.endswith("." + ALLOWED_DOMAIN):
        raise PipelineError(
            f"引用できるのは転載自由の {ALLOWED_DOMAIN} だけです: {url}\n"
            "  5ch・ガールズちゃんねる等は規約上、許諾なき転載ができません（docs/04 2-2章）。"
        )


def _http(url: str, timeout: float = 20) -> bytes:
    _check_domain(url)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _decode(data: bytes) -> str:
    # 2ch互換のsubject.txt / datはCP932。壊れたバイトでスレ1本を捨てない
    return data.decode("cp932", errors="replace")


def _base_url(board: str) -> str:
    server = BOARDS.get(board)
    if not server:
        raise PipelineError(
            f"未登録の板です: {board}（登録済み: {', '.join(BOARDS)}）\n"
            "  板を増やすときは fetch_threads.py の BOARDS にサーバごと追記してください。"
        )
    return f"https://{server}.{ALLOWED_DOMAIN}/{board}"


def _clean_body(body: str) -> str:
    """datのレス本文を素のテキストにする。<br>は改行、タグは落とし、実体参照を戻す。"""
    text = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def list_threads(board: str) -> list:
    """subject.txt からスレ一覧を取る。[{thread, title, res_count}] を新しい順で返す。"""
    text = _decode(_http(f"{_base_url(board)}/subject.txt"))
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\d+)\.dat<>(.*)\s\((\d+)\)$", line.strip())
        if m:
            rows.append({
                "thread": m.group(1),
                "title": html.unescape(m.group(2)).strip(),
                "res_count": int(m.group(3)),
            })
    return rows


def fetch_thread(board: str, thread: str) -> dict:
    """dat を取ってレスを展開する。1レス目の後ろにスレタイが入っている（2ch互換形式）。"""
    dat = _decode(_http(f"{_base_url(board)}/dat/{thread}.dat"))
    title, res = "", []
    for i, line in enumerate(dat.splitlines(), 1):
        fields = line.split("<>")
        if len(fields) < 4:
            continue
        if i == 1 and len(fields) >= 5:
            title = html.unescape(fields[4]).strip()
        body = _clean_body(fields[3])
        if body:
            res.append({"no": i, "text": body})
        if len(res) >= KEEP_RES:
            break
    if not res:
        raise PipelineError(f"スレの本文が読めませんでした: {board}/{thread}")
    return {
        "id": f"{board}-{thread}",
        "board": board,
        "thread": thread,
        "url": f"https://{BOARDS[board]}.{ALLOWED_DOMAIN}/test/read.cgi/{board}/{thread}/",
        "title": title or res[0]["text"][:40],
        "res_count": len(res),
        "res": res,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # candidate → adopted / rejected。採用は必ず人の目視で決める（docs/05 3章）
        "status": "candidate",
    }


def _path(thread_id: str) -> str:
    return os.path.join(THREADS_DIR, f"{thread_id}.json")


def _save(data: dict) -> str:
    os.makedirs(THREADS_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(thread_id: str) -> dict:
    path = _path(thread_id)
    if not os.path.exists(path):
        raise PipelineError(f"候補がありません: {thread_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_threads() -> list:
    """保存済みのスレ全部。--list と後段（採用分の取り出し）が使う。"""
    rows = []
    for path in sorted(os.listdir(THREADS_DIR)) if os.path.isdir(THREADS_DIR) else []:
        if path.endswith(".json"):
            with open(os.path.join(THREADS_DIR, path), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def adopted_threads() -> list:
    """採用済みのスレだけ。台本生成（#89）はここからだけ読む。"""
    return [t for t in saved_threads() if t.get("status") == "adopted"]


def collect(board: str, limit: int) -> list:
    """板から候補を集めて保存する。選定は機械で絞りすぎず、人の目視に委ねる。"""
    known = {t["id"] for t in saved_threads()}
    picked, saved = [], []
    for row in list_threads(board):
        if not (MIN_RES <= row["res_count"] <= MAX_RES):
            continue
        # 「Part2」「★3」のような続き物は前提知識が要るので、単発で完結するスレを優先する
        if re.search(r"(part|Part|PART|★|\bpt\.)\s*\d+", row["title"]):
            continue
        if f"{board}-{row['thread']}" in known:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    for i, row in enumerate(picked):
        if i:
            time.sleep(FETCH_INTERVAL)  # 相手サーバーへの負荷を抑える
        try:
            saved.append(_save(fetch_thread(board, row["thread"])))
        except (urllib.error.URLError, OSError) as e:
            print(f"  取得失敗: {row['title']} ({e})", file=sys.stderr)
    return saved


def from_url(url: str) -> str:
    """read.cgi / dat のURLを1本取り込む。ドメイン検査は _http が必ず通す。"""
    _check_domain(url)
    m = (re.search(r"/test/read\.cgi/([a-z0-9]+)/(\d+)", url)
         or re.search(r"/([a-z0-9]+)/dat/(\d+)\.dat", url))
    if not m:
        raise PipelineError(f"スレのURLとして解釈できません: {url}")
    board, thread = m.group(1), m.group(2)
    if board not in BOARDS:
        # URL直指定は板の登録が無くてもサーバ名がURLに入っているので、そのまま使う
        server = urllib.parse.urlparse(url).hostname.split(".")[0]
        BOARDS[board] = server
    return _save(fetch_thread(board, thread))


def mark(thread_id: str, status: str) -> None:
    data = _load(thread_id)
    data["status"] = status
    _save(data)


def show_list() -> None:
    rows = saved_threads()
    if not rows:
        print("候補がありません。--board で収集してください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌"}
    for t in rows:
        print(f"{icons.get(t['status'], '?')} {t['id']}  ({t['res_count']}res)  {t['title']}")
    print(f"\n採用 {sum(t['status'] == 'adopted' for t in rows)} / "
          f"候補 {sum(t['status'] == 'candidate' for t in rows)} / "
          f"不採用 {sum(t['status'] == 'rejected' for t in rows)}")


def main() -> None:
    p = argparse.ArgumentParser(description="おーぷん2ちゃんねるからスレを収集する")
    p.add_argument("--board", help=f"収集する板（{' / '.join(BOARDS)}）")
    p.add_argument("--limit", type=int, default=10, help="収集する候補数（既定10）")
    p.add_argument("--url", help="スレのURLを1本だけ取り込む（open2ch限定）")
    p.add_argument("--list", action="store_true", help="保存済み候補の一覧")
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
        elif args.url:
            print(f"取り込み: {os.path.relpath(from_url(args.url))}")
        elif args.board:
            saved = collect(args.board, args.limit)
            print(f"{len(saved)}本を保存しました → data/threads/")
            print("次: --list で眺めて --adopt / --reject を付けてください（採用は人の目視）")
        else:
            p.error("--board / --url / --list / --adopt / --reject のいずれかを指定してください")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
