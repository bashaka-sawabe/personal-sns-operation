#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事実ベースのネタ収集と裏取り管理（heisei / showa。docs/04 2-2章・docs/05 3章）。

    # 自分で見つけたネタを積む（既定は heisei。showa は --channel showa）
    .venv/bin/python tools/fetch_facts.py --add "たまごっちの発売は1996年" --from "https://..."
    .venv/bin/python tools/fetch_facts.py --add "土光敏夫はメザシで夕食" --channel showa

    # 裏取り（一次ソース）を付ける → 付いて初めて採用できる
    .venv/bin/python tools/fetch_facts.py --back til-abc123 --url "https://www.maff.go.jp/..." --note "農水省のQ&A"
    .venv/bin/python tools/fetch_facts.py --adopt til-abc123

    # 一覧・不採用
    .venv/bin/python tools/fetch_facts.py --list
    .venv/bin/python tools/fetch_facts.py --reject til-abc123

**裏取り（backing_url）の無いネタは採用できない。** 雑学は間違えた瞬間に
コメント欄で刺されて信用が飛ぶ。発見ルート（Reddit・スレ・伝聞）は仮説にすぎず、
一次ソース（官公庁・政府統計・学術機関 > 信頼できる一次報道 > Wikipedia経由で原典）で
確認できたものだけが台本になる。
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError

FACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "facts")

def _path(fact_id: str) -> str:
    return os.path.join(FACTS_DIR, f"{fact_id}.json")


def _save(data: dict) -> str:
    os.makedirs(FACTS_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(fact_id: str) -> dict:
    path = _path(fact_id)
    if not os.path.exists(path):
        raise PipelineError(f"候補がありません: {fact_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_facts() -> list:
    rows = []
    for name in sorted(os.listdir(FACTS_DIR)) if os.path.isdir(FACTS_DIR) else []:
        if name.endswith(".json"):
            with open(os.path.join(FACTS_DIR, name), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def load_adopted(fact_id: str) -> dict:
    """採用済みネタを1本返す。採用時に裏取りを検査済みだが、後から手で消される事故も防ぐ。"""
    data = _load(fact_id)
    if data.get("status") != "adopted":
        raise PipelineError(
            f"{fact_id} は採用されていません（現在: {data.get('status')}）。\n"
            "  --back で一次ソースを付けてから --adopt してください。"
        )
    if not (data.get("backing_url") or "").strip():
        raise PipelineError(f"{fact_id} に裏取り（backing_url）がありません。--back で付けてください。")
    return data


def _new_fact(fact_id: str, fact: str, discovered_from: str,
              channel: str) -> dict:
    return {
        "id": fact_id,
        "channel": channel,                   # heisei / showa。ネタの置き場を分ける
        "fact": fact,
        "discovered_from": discovered_from,   # 発見ルート（裏取りには使えない）
        "backing_url": "",                    # 一次ソース。空のままでは採用できない
        "backing_note": "",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "candidate",
    }


def add_manual(fact: str, discovered_from: str, channel: str = "heisei") -> str:
    prefix = channel if channel in ("heisei", "showa") else "fact"
    fact_id = f"{prefix}-" + hashlib.sha1(fact.encode()).hexdigest()[:10]
    return _save(_new_fact(fact_id, fact.strip(), discovered_from or "", channel))


def set_backing(fact_id: str, url: str, note: str) -> None:
    if not (url or "").strip():
        raise PipelineError("--url に一次ソースのURLを指定してください。")
    data = _load(fact_id)
    data["backing_url"] = url.strip()
    data["backing_note"] = (note or "").strip()
    _save(data)


def mark(fact_id: str, status: str) -> None:
    data = _load(fact_id)
    if status == "adopted" and not (data.get("backing_url") or "").strip():
        # ここが本丸。裏取りの無い雑学を台本に流さない（docs/05 3章）
        raise PipelineError(
            f"{fact_id} は裏取り（一次ソース）が無いため採用できません。\n"
            "  --back <id> --url <一次ソースURL> で付けてから --adopt してください。"
        )
    data["status"] = status
    _save(data)


def show_list(channel: str = "") -> None:
    # channel が無い旧ファイルは廃止前の trivia 台帳（til-*）。表示互換のため既定を trivia にする
    rows = [f for f in saved_facts()
            if not channel or f.get("channel", "trivia") == channel]
    if not rows:
        print("候補がありません。--reddit か --add で集めてください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌"}
    for f in rows:
        backed = "🔗" if f.get("backing_url") else "  "
        ch = f.get("channel", "trivia")
        print(f"{icons.get(f['status'], '?')}{backed} [{ch}] {f['id']}  {f['fact'][:55]}")
    print("\n🔗=裏取り済み。裏取りが無いと --adopt できません")


def main() -> None:
    p = argparse.ArgumentParser(description="事実ベースのネタ収集と裏取り管理")
    p.add_argument("--add", metavar="FACT", help="ネタを手で積む")
    p.add_argument("--from", dest="discovered_from", metavar="URL", help="--add の発見元URL")
    p.add_argument("--channel", default="heisei", choices=("heisei", "showa"),
                   help="--add の対象チャンネル（既定 heisei）／--list の絞り込み")
    p.add_argument("--back", metavar="ID", help="裏取りを付ける対象")
    p.add_argument("--url", help="--back で付ける一次ソースURL")
    p.add_argument("--note", help="--back の補足（何のソースか）")
    p.add_argument("--list", action="store_true", help="候補一覧")
    p.add_argument("--adopt", metavar="ID", help="採用にする（裏取り必須）")
    p.add_argument("--reject", metavar="ID", help="不採用にする")
    args = p.parse_args()

    try:
        if args.list:
            # --list は既定で全件。--channel を明示したときだけ絞る
            show_list(args.channel if "--channel" in sys.argv else "")
        elif args.back:
            set_backing(args.back, args.url or "", args.note or "")
            print(f"裏取りを記録: {args.back}")
        elif args.adopt:
            mark(args.adopt, "adopted")
            print(f"採用: {args.adopt}")
        elif args.reject:
            mark(args.reject, "rejected")
            print(f"不採用: {args.reject}")
        elif args.add:
            path = add_manual(args.add, args.discovered_from or "", args.channel)
            print(f"追加: {os.path.relpath(path)}（{args.channel}）")
        else:
            p.error("--add / --back / --list / --adopt / --reject のいずれかを指定してください")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
