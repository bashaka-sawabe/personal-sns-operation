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

    # 着手前の前提チェック（作業ツリー・未pushコミット・ブランチ位置）
    python3 tools/gh_board.py doctor

認証は gh CLI に任せる（`gh auth status` が通っていればよい）。
Projects v2 の操作には `project` スコープが要る。

Blocked by は GitHub 公式の依存関係API（/issues/{n}/dependencies/blocked_by）を使う。
本文に「Blocked by #23」と書くだけだと、ただの文字列でブロック判定に使えない。

盤の状態は1本のGraphQLクエリで取り、短時間ディスクにキャッシュする。
`gh project` のサブコマンドを個別に叩くと、1コマンドあたり3回・毎回300ノード分を
取りに行くことになり、連続実行でGraphQLのレート制限（5,000ポイント/時）を
使い切る（実際に一度使い切った）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

PROJECT_NUMBER = 4
PROJECT_OWNER = "bashaka-sawabe"

# 人が決める・人が手を動かすものは自動実行の対象外にする
MANUAL_LABELS = {"manual", "decision"}
PRIORITIES = ["P0", "P1", "P2", "P3"]

# 盤の状態のキャッシュ。短く持つ（他所で更新されても数十秒で追いつく）
CACHE_PATH = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), f"gh_board_{PROJECT_OWNER}_{PROJECT_NUMBER}.json"
)
CACHE_TTL = 60.0


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
    """owner/name。remote URLから読む。

    `gh repo view` はGraphQLを使うため、GraphQLのレート制限に当たると
    RESTだけで済む操作（Blocked byの付け外し等）まで巻き添えで動かなくなる。
    """
    if "repo" not in _cache:
        url = git("remote", "get-url", "origin")
        m = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", url)
        if not m:
            raise BoardError(f"originのURLからリポジトリを判定できません: {url}")
        _cache["repo"] = m.group(1)
    return _cache["repo"]


# items はページネーション必須。first:100 だけだと101件目以降（＝新しく載せたIssue）が
# 見えなくなり、「登録したのに Priority が - に戻る」ように観測される（#174 の正体）
BOARD_QUERY = """
query($owner:String!, $number:Int!, $after:String) {
  user(login:$owner) {
    projectV2(number:$number) {
      id
      fields(first:30) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id name options { id name }
          }
        }
      }
      items(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { number } }
          fieldValues(first:20) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _fetch_board() -> dict:
    """盤の状態をGraphQLで取る（フィールド定義＋載っているIssue全件）。"""
    fields, items = {}, {}
    project_id, cursor = None, None
    while True:
        args = ["api", "graphql", "-f", f"query={BOARD_QUERY}",
                "-F", f"owner={PROJECT_OWNER}", "-F", f"number={PROJECT_NUMBER}"]
        if cursor:
            args += ["-F", f"after={cursor}"]
        raw = gh(*args, parse=True)
        pv = (raw.get("data") or {}).get("user", {}).get("projectV2")
        if not pv:
            raise BoardError(f"Project #{PROJECT_NUMBER}（{PROJECT_OWNER}）が見つかりません")
        project_id = pv["id"]

        for f in pv["fields"]["nodes"]:
            if f and f.get("options"):
                fields[f["name"]] = {
                    "id": f["id"],
                    "options": {o["name"]: o["id"] for o in f["options"]},
                }

        for it in pv["items"]["nodes"]:
            num = (it.get("content") or {}).get("number")
            if num is None:
                continue
            values = {}
            for v in it["fieldValues"]["nodes"]:
                if v and v.get("field"):
                    values[v["field"]["name"]] = v.get("name")
            items[num] = {"id": it["id"], "values": values}

        page = pv["items"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    return {"id": project_id, "fields": fields, "items": items}


def board(force: bool = False) -> dict:
    """盤の状態。プロセスをまたいで短時間キャッシュする。"""
    if not force and "board" in _cache:
        return _cache["board"]
    if not force and os.path.exists(CACHE_PATH):
        try:
            if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                # JSONのキーは文字列になるのでIssue番号を数値に戻す
                data["items"] = {int(k): v for k, v in data["items"].items()}
                _cache["board"] = data
                return data
        except (OSError, ValueError, KeyError):
            pass  # 壊れていたら取り直すだけ

    data = _fetch_board()
    _cache["board"] = data
    _save_cache(data)
    return data


def _save_cache(data: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass  # キャッシュは書けなくても動作に影響しない


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
    b = board()
    field = b["fields"].get(name)
    if not field:
        raise BoardError(f"フィールド「{name}」がProjectにありません")
    option = field["options"].get(value)
    if not option:
        choices = " / ".join(field["options"])
        raise BoardError(f"{name} に「{value}」はありません（{choices}）")
    gh("project", "item-edit", "--id", item_id, "--project-id", b["id"],
       "--field-id", field["id"], "--single-select-option-id", option)

    # 書いた内容をキャッシュに反映する。捨てるだけにすると、次の読み出しが
    # Projects v2 の反映待ちで古い値を返すことがある。
    # 自分の書き込み結果を古いまま報告するツールは、無いより悪い
    for rec in b["items"].values():
        if rec["id"] == item_id:
            rec.setdefault("values", {})[name] = value
            break
    _save_cache(b)


def ensure_item(number: int) -> str:
    """IssueをProjectに載せて item id を返す。既に載っていればそれを使う。"""
    b = board()
    known = b["items"].get(number)
    if known:
        return known["id"]
    url = f"https://github.com/{repo()}/issues/{number}"
    added = gh("project", "item-add", str(PROJECT_NUMBER), "--owner", PROJECT_OWNER,
               "--url", url, "--format", "json", parse=True)
    # 取り直さずキャッシュに足す。1コマンドあたりの再取得を1回に抑える
    b["items"][number] = {"id": added["id"], "values": {}}
    _save_cache(b)
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
    """オープンなIssueを、優先度と待ち状況つきで返す。

    Issue一覧はRESTで取る（`gh issue list` はGraphQLを使うため、
    Projects側の操作とレート制限の枠を食い合う）。
    """
    raw = gh("api", f"repos/{repo()}/issues?state=open&per_page=100",
             "--paginate", parse=True)
    items = board()["items"]
    rows = []
    for iss in raw:
        if "pull_request" in iss:      # PRも同じエンドポイントで返ってくる
            continue
        labels = {lb["name"] for lb in iss.get("labels", [])}
        values = items.get(iss["number"], {}).get("values", {})
        waiting = [b for b in blockers(iss["number"]) if b["state"] == "open"]
        rows.append({
            "number": iss["number"],
            "title": iss["title"],
            "labels": sorted(labels),
            "priority": values.get("Priority") or "-",
            "status": values.get("Status") or "-",
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
    rows = snapshot(auto=args.auto)
    ready = [r for r in rows if not r["blocked_by"] and r["status"] != "In Progress"]
    if not ready:
        # 何も出ないのが「終わった」なのか「全部詰まっている」なのかを区別する
        blocked = [r for r in rows if r["blocked_by"]]
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


# ------------------------------------------------------------ 前提チェック

def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if proc.returncode != 0:
        raise BoardError(f"git {' '.join(args)}\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def cmd_doctor(args) -> None:
    """着手前に、履歴を壊す条件が揃っていないかを見る。

    実際に一度やらかしている: ローカルmainが未pushのままブランチを切ったため、
    PRのベースが古く、squashマージが未pushの6コミットを1つに潰した。
    手順書に書くだけでは守られないので、コマンドにして落とす。
    """
    problems = []

    dirty = git("status", "--porcelain")
    if dirty:
        n = len(dirty.splitlines())
        problems.append(
            f"作業ツリーに未コミットの変更が{n}件あります。\n"
            "  → コミットするか `git stash` してから始めてください"
            f"（`git status` で確認）:\n    " + "\n    ".join(dirty.splitlines()[:5])
        )

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        problems.append(
            f"いま `{branch}` にいます（`main` ではありません）。\n"
            "  → 前の作業が終わっていれば `git switch main` してください"
        )

    if not args.offline:
        try:
            git("fetch", "--quiet", "origin", "main")
        except BoardError:
            problems.append("origin/main を取得できませんでした（ネットワーク・認証を確認）")

    ahead = git("rev-list", "--count", "origin/main..main")
    if ahead != "0":
        problems.append(
            f"`main` に未pushのコミットが{ahead}件あります。\n"
            "  → このまま新しいブランチを切ると、PRのベースが古くなり、\n"
            "     squashマージが未pushのコミットを巻き込んで1つに潰します。\n"
            "  → 先に `git push origin main` してください"
        )

    behind = git("rev-list", "--count", "main..origin/main")
    if behind != "0":
        problems.append(
            f"`main` が origin より{behind}件遅れています。\n"
            "  → `git pull --ff-only` してから始めてください"
        )

    if problems:
        print("着手できません。", file=sys.stderr)
        for i, p in enumerate(problems, 1):
            print(f"\n{i}. {p}", file=sys.stderr)
        sys.exit(1)
    print("問題なし。着手できます。")


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

    dr = sub.add_parser("doctor", help="着手前の前提チェック")
    dr.add_argument("--offline", action="store_true", help="originへのfetchを省く")
    dr.set_defaults(func=cmd_doctor)

    args = p.parse_args()
    try:
        args.func(args)
    except BoardError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
