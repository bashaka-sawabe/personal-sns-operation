#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開予約中の動画を、作り直したmp4で同時刻のまま差し替える（#208）。

    # 1. 予約中の動画を走査して差し替え計画を作る（動画は触らない）
    .venv/bin/python tools/rebuild_scheduled.py --scan

    # 2. 計画に従って差し替える（何度実行しても途中から続きをやる）
    .venv/bin/python tools/rebuild_scheduled.py --run

    # 進捗だけ見る
    .venv/bin/python tools/rebuild_scheduled.py --status

なぜこのツールがあるか:
- 演出の修正（#203/#204 など）は投稿済みの動画に遡って効かない。予約公開まで
  設定された動画が旧演出のまま世に出るのを防ぐには、**旧動画の予約を解除して、
  作り直したmp4を同じ publishAt で上げ直す**しかない（YouTubeは動画の差し替え不可）。
- アップロードはクォータ（1本1,600ユニット）に阻まれて日をまたぐことがあるため、
  計画ファイルに進捗を残して**冪等**にする。失敗した分は次の実行が拾う。

安全側の順序: 先に旧動画を予約解除（private化）してから新動画を上げる。
逆にすると、アップロードがクォータで落ちた日に旧演出の動画がそのまま公開される。
予約解除された枠は、差し替えが遅れても「何も公開されない」だけで済む。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import publish_youtube as yt
from tools.pipeline import status as status_mod
from tools.pipeline.common import OUT_DIR, PipelineError

# 進捗つきの差し替え計画。ledger（.published_youtube.json）と同じく実行時の状態なのでgit外
PLAN = os.path.join(OUT_DIR, ".reschedule_plan.json")

# 台帳に残す理由の既定値。以前は「新演出で作り直し（#208）」を焼き込んでいたが、
# この道具は理由を問わず使うので、実際は別件（#261 のチカチカ）で差し替えた6本まで
# #208 として記録されてしまった。既定は**確実に真であること**だけを書き、
# 理由は --reason で呼び出し側が渡す
DEFAULT_REASON = "作り直したmp4で差し替え"


def load_plan() -> list[dict]:
    if os.path.exists(PLAN):
        with open(PLAN, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_plan(plan: list[dict]) -> None:
    tmp = PLAN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PLAN)  # 書き途中で落ちても計画を壊さない


def pending(plan: list[dict]) -> list[dict]:
    return [e for e in plan if not e.get("new_id")]


def scan() -> None:
    """投稿台帳にある動画のうち「非公開＋公開予約あり」を計画に起こす。"""
    plan = load_plan()
    if pending(plan):
        raise PipelineError(
            f"未完了の計画が残っています（{len(pending(plan))}本）。先に --run で消化するか、"
            f"{PLAN} を確認してください。"
        )

    ledger = yt.load_ledger()
    by_channel: dict[str, list[tuple[str, str]]] = {}
    for name, rec in ledger.items():
        by_channel.setdefault(name.split("-")[0], []).append((name, rec["video_id"]))

    _, _, _, _, HttpError, _ = yt._imports()
    plan = []
    for ch, items in sorted(by_channel.items()):
        try:
            service = yt.get_service(channel=ch)
        except PipelineError as e:
            print(f"[{ch}] 認可なしのため走査を飛ばします: {str(e).splitlines()[0]}")
            continue
        try:
            res = service.videos().list(
                part="status,snippet", id=",".join(v for _, v in items)
            ).execute()
        except HttpError as e:
            print(f"[{ch}] 走査失敗: {getattr(e, 'reason', '') or e}")
            continue
        for it in res.get("items", []):
            st, sn = it["status"], it["snippet"]
            if st.get("privacyStatus") != "private" or not st.get("publishAt"):
                continue
            name = next(n for n, v in items if v == it["id"])
            plan.append({
                "file": name,
                "stem": os.path.splitext(name)[0],
                "channel": ch,
                "old_id": it["id"],
                "publish_at": st["publishAt"],
                # 本人がStudioで直したかもしれないので、メタデータは再生成せず旧動画から写す
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "category_id": sn.get("categoryId", "22"),
                "old_neutralized": False,
                "new_id": None,
            })

    save_plan(plan)
    print(f"差し替え計画: {len(plan)}本 → {PLAN}")
    for e in plan:
        print(f"  {e['stem']} ({e['channel']})  公開予定 {e['publish_at']}  旧 {e['old_id']}")


def show_status() -> None:
    plan = load_plan()
    if not plan:
        print("計画はありません（--scan で作成）。")
        return
    for e in plan:
        mark = "済" if e.get("new_id") else ("旧解除のみ" if e.get("old_neutralized") else "未")
        new = f" 新 {e['new_id']}" if e.get("new_id") else ""
        print(f"  [{mark}] {e['stem']}  公開予定 {e['publish_at']}  旧 {e['old_id']}{new}")
    print(f"残り {len(pending(plan))}本")


def neutralize(service, HttpError, entry: dict) -> bool:
    """旧動画の公開予約を外して非公開にする。

    videos.update は part に含めた可変フィールドを**省略すると消す**仕様なので、
    status だけを private で送れば publishAt も消える。消えたことは読み直して確かめる
    （旧演出の動画が予約時刻に公開されるのが、この作業で一番起きてはいけないこと）。

    直後の read が更新前の値を返すことがある（実測 2026-08-09）ため、
    先に現状を読んで解除済みなら成功扱いにし、update 後の検証は間を置いて2回見る。
    """
    try:
        got = service.videos().list(part="status", id=entry["old_id"]).execute()
        st = (got.get("items") or [{}])[0].get("status", {})
        if st.get("privacyStatus") == "private" and not st.get("publishAt"):
            return True  # すでに解除済み（前回実行の反映が読めなかっただけ）
        service.videos().update(part="status", body={
            "id": entry["old_id"],
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        }).execute()
        for attempt in (1, 2):
            got = service.videos().list(part="status", id=entry["old_id"]).execute()
            st = (got.get("items") or [{}])[0].get("status", {})
            if not st.get("publishAt"):
                return True
            if attempt == 1:
                time.sleep(10)  # 反映待ち
        print(f"  {entry['stem']}: 予約が残っています（{st.get('publishAt')}）。中断します")
        return False
    except HttpError as e:
        print(f"  {entry['stem']}: 旧動画の予約解除に失敗: {getattr(e, 'reason', '') or e}")
        return False


def upload_replacement(entry: dict) -> str | None:
    """作り直したmp4を旧動画と同じメタデータ・同じ publishAt で予約アップロードする。"""
    _, _, _, _, HttpError, MediaFileUpload = yt._imports()
    path = os.path.join(OUT_DIR, entry["file"])
    service = yt.get_service(channel=entry["channel"])
    body = {
        "snippet": {
            "title": entry["title"],
            "description": entry["description"],
            "tags": entry["tags"],
            "categoryId": entry["category_id"],
        },
        # publishAt は privacyStatus=private のときだけ有効。時刻が来るとYouTubeが公開する
        "status": {
            "privacyStatus": "private",
            "publishAt": entry["publish_at"],
            "selfDeclaredMadeForKids": False,
        },
    }
    try:
        res = service.videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(path, chunksize=-1, resumable=True),
        ).execute()
    except HttpError as e:
        detail = getattr(e, "reason", "") or str(e)
        if e.resp.status == 403 and "quota" in detail.lower():
            print(f"  {entry['stem']}: クォータ上限。次回の実行に持ち越します")
        else:
            print(f"  {entry['stem']}: アップロード失敗: {detail}")
        return None
    return res["id"]


def run(reason: str = DEFAULT_REASON) -> None:
    plan = load_plan()
    todo = pending(plan)
    if not todo:
        print("差し替えは全て完了しています。")
        return

    _, _, _, _, HttpError, _ = yt._imports()

    # 1周目: 旧動画の予約を全部外す。アップロードより先に済ませないと、
    # クォータで日をまたいだとき旧演出のまま公開されてしまう
    for entry in todo:
        if entry["old_neutralized"]:
            continue
        service = yt.get_service(channel=entry["channel"])
        if neutralize(service, HttpError, entry):
            entry["old_neutralized"] = True
            save_plan(plan)
            print(f"  {entry['stem']}: 旧動画（{entry['old_id']}）の予約を解除しました")

    # 2周目: 作り直したmp4を同時刻で予約アップロード
    for entry in todo:
        if not entry["old_neutralized"]:
            continue  # 旧が生きたまま新を上げると同時刻に2本公開される
        path = os.path.join(OUT_DIR, entry["file"])
        if not os.path.exists(path):
            print(f"  {entry['stem']}: mp4がありません（{path}）。再レンダしてください")
            continue
        new_id = upload_replacement(entry)
        if not new_id:
            continue
        entry["new_id"] = new_id
        save_plan(plan)
        url = f"https://youtube.com/shorts/{new_id}"
        ledger = yt.load_ledger()
        ledger[entry["file"]] = {"video_id": new_id, "privacy": "private",
                                 "publish_at": entry["publish_at"],
                                 "replaced": entry["old_id"]}
        yt.save_ledger(ledger)
        status_mod.advance(entry["stem"], "posted", url=url,
                           note=f"{reason}・{entry['publish_at']}公開予約")
        print(f"  {entry['stem']}: {url} を {entry['publish_at']} で予約しました")

    remain = pending(plan)
    print(f"\n残り {len(remain)}本" if remain else "\n差し替えが全て完了しました。")
    if remain:
        sys.exit(1)  # ログで「まだ終わっていない」が一目で分かるように


def main() -> None:
    p = argparse.ArgumentParser(description="公開予約中の動画を作り直したmp4で差し替える")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true", help="予約中の動画から差し替え計画を作る")
    g.add_argument("--run", action="store_true", help="計画を実行する（冪等）")
    g.add_argument("--status", action="store_true", help="計画の進捗を表示する")
    p.add_argument("--reason", default=DEFAULT_REASON,
                   help="台帳に残す差し替えの理由（既定: 差し替えた事実だけを書く）")
    args = p.parse_args()
    try:
        if args.scan:
            scan()
        elif args.status:
            show_status()
        else:
            run(args.reason)
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
