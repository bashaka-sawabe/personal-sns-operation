#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定番ネットミーム・コピペを台本のネタとして管理する（#205）。

    # 一覧（権利の扱いつき）
    .venv/bin/python tools/fetch_memes.py --list

    # 登録する（rights は下の表のキーから選ぶ）
    .venv/bin/python tools/fetch_memes.py --add "面接で座右の銘を左右の目と聞き違える" \
        --name "座右の銘" --origin "https://..." --rights open2ch

    # 採用・不採用
    .venv/bin/python tools/fetch_memes.py --adopt meme-classic-ab12cd
    .venv/bin/python tools/fetch_memes.py --reject meme-classic-ab12cd

スレ（fetch_threads）と分けている理由:
スレは**本文を引用してよい**（おーぷん2ちゃんねるはパブリックドメイン）が、
定番コピペの多くは**5ch起源で転載が許可制**、あるいは**実写動画が元**で
肖像権・著作権が絡む。**引用できないものを引用できるものと同じ棚に置かない。**

ここに置くのは「**話の骨格（何が起きてどう落ちるか）**」だけで、本文は持たない。
アイデアと事実に著作権は無く、保護されるのは表現なので、骨格から**書き下ろす**
（docs/04 2-2章「参考に留める」の線）。
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError

MEMES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "memes")

# 権利の扱い。**usable が False のものは採用できない**（コードで止める）。
# 判断の根拠は docs/04 2-2章。ここを緩めるときは必ずドキュメントを先に直すこと
RIGHTS = {
    "open2ch": {
        "usable": True,
        "label": "おーぷん2ちゃんねる発（転載自由）",
        "note": "本文を引用してよい唯一の区分。それでも翻案はする（再利用コンテンツ判定を避ける）",
    },
    "5ch_paraphrase": {
        "usable": True,
        "label": "5ch起源（転載は許可制）→ 骨格のみ翻案",
        "note": "**表現を写さない。** 展開とオチの骨格だけを借りて全文を書き下ろす",
    },
    "classic_unknown": {
        "usable": True,
        "label": "作者・初出不明の古典コピペ → 骨格のみ翻案",
        "note": "初出が特定できない以上、許諾を取る相手がいない。表現は必ず書き下ろす",
    },
    "video_unusable": {
        "usable": False,
        "label": "実写動画・放送が元（肖像権・著作権）",
        "note": "被写体と撮影者の権利が絡む。**翻案でも使わない**（人物が特定される）",
    },
    "rights_holder": {
        "usable": False,
        "label": "権利者が明確な作品（漫画・アニメ・楽曲等）",
        "note": "キャラクター・台詞・画のいずれも使わない",
    },
}


def _path(meme_id: str) -> str:
    return os.path.join(MEMES_DIR, f"{meme_id}.json")


def _save(data: dict) -> str:
    os.makedirs(MEMES_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(meme_id: str) -> dict:
    path = _path(meme_id)
    if not os.path.exists(path):
        raise PipelineError(f"ミームがありません: {meme_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_memes() -> list:
    rows = []
    for name in sorted(os.listdir(MEMES_DIR)) if os.path.isdir(MEMES_DIR) else []:
        if name.endswith(".json"):
            with open(os.path.join(MEMES_DIR, name), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def check_rights(data: dict) -> None:
    """使ってよいミームか。使えないものは**採用の時点で止める**（docs/04 2-2章）。"""
    rights = data.get("rights", "")
    spec = RIGHTS.get(rights)
    if not spec:
        raise PipelineError(
            f"rights が未設定か不正です: {rights or '(空)'}\n"
            f"  使えるキー: {', '.join(RIGHTS)}"
        )
    if not spec["usable"]:
        raise PipelineError(
            f"{data['id']}「{data.get('name', '')}」は使えません: {spec['label']}\n"
            f"  {spec['note']}"
        )
    if not (data.get("skeleton") or "").strip():
        raise PipelineError(
            f"{data['id']} に skeleton（話の骨格）がありません。\n"
            "  何が起きてどう落ちるかを一文で書いてください（本文の引用は置かない）。"
        )


def load_adopted(meme_id: str) -> dict:
    """採用済みミームを1本返す。権利の検査は採用時に済んでいるが、ここでも見る。"""
    data = _load(meme_id)
    if data.get("status") != "adopted":
        raise PipelineError(
            f"{meme_id} は採用されていません（現在: {data.get('status')}）。\n"
            "  --adopt を付けてください。"
        )
    check_rights(data)   # 台帳を手で書き換えられても止まるように、使う直前にもう一度見る
    return data


def add(skeleton: str, name: str, origin: str, rights: str, note: str = "") -> str:
    """ミームを候補として登録する。同じ骨格は二重登録しない。"""
    if rights not in RIGHTS:
        raise PipelineError(f"rights は {', '.join(RIGHTS)} から選んでください（指定: {rights}）")
    meme_id = "meme-classic-" + hashlib.sha1(skeleton.encode("utf-8")).hexdigest()[:10]
    if os.path.exists(_path(meme_id)):
        raise PipelineError(f"同じ骨格が登録済みです: {meme_id}")
    return _save({
        "id": meme_id,
        "name": name or skeleton[:20],
        # 本文は置かない。**何が起きてどう落ちるか**だけ（docs/04 2-2章）
        "skeleton": skeleton,
        "origin": origin,
        "rights": rights,
        "rights_label": RIGHTS[rights]["label"],
        "note": note,
        "added_at": datetime.date.today().isoformat(),
        "status": "candidate",
    })


def mark(meme_id: str, status: str) -> None:
    data = _load(meme_id)
    if status == "adopted":
        check_rights(data)
    data["status"] = status
    _save(data)


def mark_used(meme_id: str) -> bool:
    """消費したミームに印を付ける。既に used なら何もしない（#230と同じ約束）。"""
    data = _load(meme_id)
    if data.get("status") == "used":
        return False
    data["status"] = "used"
    data["used_at"] = datetime.date.today().isoformat()
    _save(data)
    return True


def show_list() -> None:
    rows = saved_memes()
    if not rows:
        print("ミームがありません。--add で登録してください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌", "used": "🎬"}
    for m in rows:
        usable = RIGHTS.get(m.get("rights", ""), {}).get("usable")
        gate = "" if usable else "  🚫使用不可"
        print(f"{icons.get(m['status'], '?')} {m['id']}  {m['name']}{gate}")
        print(f"      {m['skeleton'][:70]}")
        print(f"      権利: {m.get('rights_label', m.get('rights', ''))}")
        if m.get("origin"):
            print(f"      出典: {m['origin']}")


def main() -> None:
    p = argparse.ArgumentParser(description="定番ネットミーム・コピペをネタとして管理する")
    p.add_argument("--list", action="store_true", help="一覧を表示する")
    p.add_argument("--add", metavar="骨格", help="話の骨格（本文ではない）を登録する")
    p.add_argument("--name", default="", help="表示名")
    p.add_argument("--origin", default="", help="出典（URL か説明）")
    p.add_argument("--rights", default="", help=f"権利の扱い（{', '.join(RIGHTS)}）")
    p.add_argument("--note", default="", help="補足")
    p.add_argument("--adopt", metavar="id", help="採用する（使えないものは止まる）")
    p.add_argument("--reject", metavar="id", help="不採用にする")
    p.add_argument("--rights-help", action="store_true", help="権利の扱いの一覧を出す")
    args = p.parse_args()

    try:
        if args.rights_help:
            for key, spec in RIGHTS.items():
                # 変数名を mark にしないこと。同名の関数を丸ごと隠して
                # --adopt が UnboundLocalError で落ちる
                verdict = "使える" if spec["usable"] else "**使えない**"
                print(f"{key:18s} {verdict}  {spec['label']}\n{'':20s}{spec['note']}")
            return
        if args.add:
            path = add(args.add, args.name, args.origin, args.rights, args.note)
            print(f"登録しました: {os.path.relpath(path)}")
            return
        if args.adopt:
            mark(args.adopt, "adopted")
            print(f"採用しました: {args.adopt}")
            return
        if args.reject:
            mark(args.reject, "rejected")
            print(f"不採用にしました: {args.reject}")
            return
        show_list()
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
