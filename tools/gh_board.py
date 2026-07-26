#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Issue と Project（Projects v2）の運用を1コマンドにまとめた薄いラッパ。

    # Issueを盤に載せる（優先度・状態つき）
    python3 tools/gh_board.py add 23 --priority P0 --iteration Current

    # 依存関係。23 が終わるまで 30 は着手できない
    python3 tools/gh_board.py block 30 --by 23,24

    # 次にやるIssueを1件出す。--auto は人手待ちを除外する
    python3 tools/gh_board.py next --auto

    # 盤の全体像（優先度・ブロック状況つき）
    python3 tools/gh_board.py list

認証は gh CLI に任せる（`gh auth status` が通っていればよい）。
Projects v2 の操作には `project` スコープが要る。

Blocked by は GitHub 公式の依存関係API（/issues/{n}/dependencies/blocked_by）を使う。
本文に「Blocked by #23」と書くだけだと、ただの文字列でブロック判定に使えない。
"""
import argparse
import json
import os
import subprocess
import sys

PROJECT_NUMBER = "4"
PROJECT_OWNER = "bashaka-sawabe"

# 人が決める・人が手を動かすものは自動実行の対象外にする
MANUAL_LABELS = {"manual", "decision"}
PRIORITIES = ["P0", "P1", "P2", "P3"]


class BoardError(Exception):
    pass


def gh(*args: str, parse: bool = False):
    """gh CLI を叩く。parse=True なら stdout を JSON として読む。"""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if proc.returncode != 0:
        raise BoardError(f"gh {' '.join(args)}\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if parse and out else out


_cache: dict = {}


def repo() -> str:
    if "repo" not in _cache:
        _cache["repo"] = gh("repo", "view", "--json", "nameWithOwner",
                            "--jq", ".nameWithOwner")
    return _cache["repo"]


def project() -> dict:
    """ProjectのIDと、単一選択フィールドの選択肢IDを引けるようにして返す。"""
    if "project" in _cache:
        return _cache["project"]
    data = gh("project", "field-list", PROJECT_NUMBER, "--owner", PROJECT_OWNER,
              "--limit", "50", "--format", "json", parse=True)
    fields = {}
    for f in data["fields"]:
        if f.get("options"):
            fields[f["name"]] = {
                "id": f["id"],
                "options": {o["name"]: o["id"] for o in f["options"]},
            }
    view = gh("project", "view", PROJECT_NUMBER, "--owner", PROJECT_OWNER,
              "--format", "json", parse=True)
    _cache["project"] = {"id": view["id"], "fields": fields}
    return _cache["project"]


def items() -> dict:
    """Projectに載っているIssueを {issue番号: item情報} で返す。"""
    if "items" in _cache:
        return _cache["items"]
    data = gh("project", "item-list", PROJECT_NUMBER, "--owner", PROJECT_OWNER,
              "--limit", "300", "--format", "json", parse=True)
    out = {}
    for it in data["items"]:
        num = (it.get("content") or {}).get("number")
        if num is not None:
            out[num] = it
    _cache["items"] = out
    return out


# ---------------------------------------------------------------- 依存関係

def blockers(number: int) -> list:
    """このIssueが待っているIssueの一覧（クローズ済みも含む）。"""
    data = gh("api", f"repos/{repo()}/issues/{number}/dependencies/blocked_by",
              parse=True)
    return [{"number": d["number"], "title": d["title"], "state": d["state"]}
            for d in (data or [])]


def issue_id(number: int) -> int:
    return int(gh("api", f"repos/{repo()}/issues/{number}", "--jq", ".id"))


def cmd_block(args) -> None:
    for by in args.by:
        gh("api", "-X", "POST",
           f"repos/{repo()}/issues/{args.number}/dependencies/blocked_by",
           "-F", f"issue_id={issue_id(by)}", "--silent")
        print(f"#{args.number} は #{by} を待つ")


def cmd_unblock(args) -> None:
    for by in args.by:
        gh("api", "-X", "DELETE",
           f"repos/{repo()}/issues/{args.number}/dependencies/blocked_by/{issue_id(by)}",
           "--silent")
        print(f"#{args.number} の待ち（#{by}）を解除")


# ------------------------------------------------------------------ 盤操作

def set_field(item_id: str, name: str, value: str) -> None:
    p = project()
    field = p["fields"].get(name)
    if not field:
        raise BoardError(f"フィールド「{name}」がProjectにありません")
    option = field["options"].get(value)
    if not option:
        choices = " / ".join(field["options"])
        raise BoardError(f"{name} に「{value}」はありません（{choices}）")
    gh("project", "item-edit", "--id", item_id, "--project-id", p["id"],
       "--field-id", field["id"], "--single-select-option-id", option)


def ensure_item(number: int) -> str:
    """IssueをProjectに載せて item id を返す。既に載っていればそれを使う。"""
    known = items().get(number)
    if known:
        return known["id"]
    url = f"https://github.com/{repo()}/issues/{number}"
    added = gh("project", "item-add", PROJECT_NUMBER, "--owner", PROJECT_OWNER,
               "--url", url, "--format", "json", parse=True)
    _cache.pop("items", None)
    return added["id"]


def cmd_add(args) -> None:
    for number in args.numbers:
        item_id = ensure_item(number)
        done = []
        for name, value in (("Priority", args.priority), ("Status", args.status),
                            ("Iteration", args.iteration)):
            if value:
                set_field(item_id, name, value)
                done.append(f"{name}={value}")
        print(f"#{number} を登録" + (f"（{' '.join(done)}）" if done else ""))


# -------------------------------------------------------------------- 一覧

def snapshot(auto: bool = False) -> list:
    """オープンなIssueを、優先度と待ち状況つきで返す。"""
    raw = gh("issue", "list", "--state", "open", "--limit", "200", "--json",
             "number,title,labels", parse=True)
    board = items()
    rows = []
    for iss in raw:
        labels = {lb["name"] for lb in iss["labels"]}
        item = board.get(iss["number"], {})
        waiting = [b for b in blockers(iss["number"]) if b["state"] == "open"]
        rows.append({
            "number": iss["number"],
            "title": iss["title"],
            "labels": sorted(labels),
            "priority": item.get("priority") or "-",
            "status": item.get("status") or "-",
            "blocked_by": [b["number"] for b in waiting],
            "manual": bool(labels & MANUAL_LABELS),
        })
    if auto:
        rows = [r for r in rows if not r["manual"]]
    order = {p: i for i, p in enumerate(PRIORITIES)}
    rows.sort(key=lambda r: (order.get(r["priority"], 9), r["number"]))
    return rows


def cmd_list(args) -> None:
    rows = snapshot(auto=args.auto)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("オープンなIssueはありません。")
        return
    print(f"{'#':>4}  {'優先':<4} {'状態':<12} {'待ち':<12} タイトル")
    for r in rows:
        wait = ",".join(f"#{n}" for n in r["blocked_by"]) or "-"
        mark = "🔒" if r["blocked_by"] else ("👤" if r["manual"] else "  ")
        print(f"{r['number']:>4}{mark} {r['priority']:<4} {r['status']:<12} "
              f"{wait:<12} {r['title']}")


def cmd_next(args) -> None:
    ready = [r for r in snapshot(auto=args.auto)
             if not r["blocked_by"] and r["status"] != "In Progress"]
    if not ready:
        # 何も出ないのが「終わった」なのか「全部詰まっている」なのかを区別する
        blocked = [r for r in snapshot(auto=args.auto) if r["blocked_by"]]
        if blocked:
            print("着手できるIssueはありません。全て他のIssue待ちです。", file=sys.stderr)
        else:
            print("着手できるIssueはありません。", file=sys.stderr)
        sys.exit(1)
    top = ready[0]
    if args.json:
        print(json.dumps(top, ensure_ascii=False, indent=2))
    else:
        print(f"#{top['number']} [{top['priority']}] {top['title']}")


def main() -> None:
    p = argparse.ArgumentParser(description="IssueとProjectの運用ラッパ")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="IssueをProjectに載せてフィールドを設定する")
    a.add_argument("numbers", nargs="+", type=int)
    a.add_argument("--priority", choices=PRIORITIES)
    a.add_argument("--status", choices=["Todo", "In Progress", "Done"])
    a.add_argument("--iteration", choices=["Current", "Next", "Backlog"])
    a.set_defaults(func=cmd_add)

    b = sub.add_parser("block", help="Blocked by を張る")
    b.add_argument("number", type=int)
    b.add_argument("--by", required=True,
                   type=lambda s: [int(x) for x in s.split(",")])
    b.set_defaults(func=cmd_block)

    u = sub.add_parser("unblock", help="Blocked by を外す")
    u.add_argument("number", type=int)
    u.add_argument("--by", required=True,
                   type=lambda s: [int(x) for x in s.split(",")])
    u.set_defaults(func=cmd_unblock)

    ls = sub.add_parser("list", help="オープンなIssueを優先度順に並べる")
    ls.add_argument("--auto", action="store_true", help="人手待ちを除く")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    nx = sub.add_parser("next", help="次に着手すべきIssueを1件出す")
    nx.add_argument("--auto", action="store_true", help="人手待ちを除く")
    nx.add_argument("--json", action="store_true")
    nx.set_defaults(func=cmd_next)

    args = p.parse_args()
    try:
        args.func(args)
    except BoardError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
