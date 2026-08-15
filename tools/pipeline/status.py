#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投稿ステータス台帳（data/status.csv）の読み書き。

状態と更新責任は docs/06 5章。draft → checked → rendered → posted → measuring。

**ツールが確実に知っている遷移だけを機械が書く。** 事実確認（checked）は人の判断なので
手のまま。工程が進むたびに人が手で写していると必ず実態とずれ、
「どこまで進んだか」を台帳から判断できなくなる。

行そのものも `ensure()` がツール側で作る（#243）。生成が自動になった以上、
人が足すのを待つと台帳に載らない動画ができ、公開を止める手段が効かなくなる。
"""
import csv
import datetime
import os

from .common import ROOT

STATUS_CSV = os.path.join(ROOT, "data", "status.csv")

# 台帳が空のときに作る列。既存の台帳があればそちらの並びに従う
FIELDS = ["video_id", "channel", "status", "updated", "url", "note"]

# 進行順。後ろの状態を前の状態で上書きしない判定に使う
ORDER = ["draft", "checked", "rendered", "posted", "measuring"]


def _rank(status: str) -> int:
    """進行度。台帳に未知の値が入っていたら、比較しないよう -1 にする。"""
    return ORDER.index(status) if status in ORDER else -1


def load() -> tuple[list, list]:
    """(列名, 行) を返す。台帳が無ければ空。"""
    if not os.path.exists(STATUS_CSV):
        return [], []
    with open(STATUS_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def advance(video_id: str, status: str, url: str | None = None,
            note: str | None = None) -> bool:
    """台帳を1行進める。進んだら True。

    台帳に無い video_id（旧世代の動画など）は静かに False を返す。台帳を持たない
    動画のためにパイプラインを止める理由はない。

    すでに先の状態にある行は書き換えない。投稿済みの行を作り直しで `rendered` に
    戻すと、公開済みかどうかが台帳から読めなくなる。
    """
    fields, rows = load()
    if not rows:
        return False

    target = next((r for r in rows if r.get("video_id") == video_id), None)
    if target is None:
        return False
    if _rank(target.get("status", "")) > _rank(status):
        return False

    target["status"] = status
    target["updated"] = datetime.date.today().isoformat()
    if url is not None:
        target["url"] = url
    if note is not None:
        target["note"] = note

    _write(fields, rows)
    return True


def revert(video_id: str, status: str, note: str) -> bool:
    """台帳を**意図して**前の状態に戻す（公開の取り下げ。#267）。

    `advance` が後戻りを拒むのは、作り直しで `posted` の行が `rendered` に落ちると
    公開済みかどうかが台帳から読めなくなるからで、後戻り自体が禁じ手なわけではない。
    公開を取り下げたときは**実態が本当に戻る**ので、台帳も戻さないと嘘になる。

    間違って呼ばれても気づけるよう、理由（note）は省略できない。
    """
    fields, rows = load()
    target = next((r for r in rows if r.get("video_id") == video_id), None)
    if target is None:
        return False

    target["status"] = status
    target["updated"] = datetime.date.today().isoformat()
    target["note"] = note
    _write(fields, rows)
    return True


def ensure(video_id: str, channel: str, status: str, url: str | None = None,
           note: str | None = None) -> bool:
    """行が無ければ作り、あれば `advance` と同じに進める（#243）。

    行を足すのは人の仕事だった（下の表の `draft`）が、生成が自動になった今は
    誰も足さない。台帳に載らないまま投稿・公開予約まで進むと、
    **公開を止める `--unreserve` が効かない**（台帳の行を関所にしているため）。
    止めたい本ほど止められない、という一番まずい形になる。
    """
    fields, rows = load()
    if any(r.get("video_id") == video_id for r in rows):
        return advance(video_id, status, url=url, note=note)

    rows.append({
        "video_id": video_id,
        "channel": channel,
        "status": status,
        "updated": datetime.date.today().isoformat(),
        "url": url or "",
        "note": note or "",
    })
    _write(fields or FIELDS, rows)
    return True


def _write(fields: list, rows: list) -> None:
    tmp = STATUS_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, STATUS_CSV)  # 書き途中で落ちても台帳を壊さない
