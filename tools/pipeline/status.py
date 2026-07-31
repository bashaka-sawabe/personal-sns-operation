#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投稿ステータス台帳（data/status.csv）の読み書き。

状態と更新責任は docs/06 5章。draft → checked → rendered → posted → measuring。

**ツールが確実に知っている遷移だけを機械が書く。** 事実確認（checked）は人の判断なので
手のまま。工程が進むたびに人が手で写していると必ず実態とずれ、
「どこまで進んだか」を台帳から判断できなくなる。
"""
import csv
import datetime
import os

from .common import ROOT

STATUS_CSV = os.path.join(ROOT, "data", "status.csv")

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

    tmp = STATUS_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, STATUS_CSV)  # 書き途中で落ちても台帳を壊さない
    return True
