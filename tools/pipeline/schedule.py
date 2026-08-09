#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開予約の枠割り（#237）。

本人指示（2026-08-10・#239）: **1チャンネルあたり1日2本を、22時と23時に1本ずつ**。

同じ時刻に2本並べると登録者への通知が重なり、2本が互いの初速を食い合う
（初版の #237 は「2本とも23時」だった）。

公開そのものは YouTube の `publishAt` が時刻に行うので、公開のための常駐プロセスは
要らない。朝10時の `daily_run.py` がアップロード時に枠を取るだけで、
13時間後に勝手に公開される（その間は本人が予約を外せる＝取り消しの余地が残る）。

ここは**日付の計算だけ**を持つ。「いま何が予約済みか」を YouTube から読むのは
`publish_youtube.py` の仕事（`pipeline/` を外部APIに依存させない）。
"""
import datetime

JST = datetime.timezone(datetime.timedelta(hours=9))

# 公開スロット（JST）。並び順がそのまま埋める順になるので、早い時刻から書く
PUBLISH_HOURS_JST = [22, 23]
# 1日の本数はスロット数そのもの。別々に持つと必ずどちらかを直し忘れる
PER_DAY_PER_CHANNEL = len(PUBLISH_HOURS_JST)

# これより近い枠は取らない。アップロードした瞬間の時刻を取ると、本人が中身を見て
# 予約を外す余地が無くなる。朝10時の自動実行なら当日23時が普通に取れる幅
MIN_LEAD = datetime.timedelta(hours=1)


def now_jst() -> datetime.datetime:
    return datetime.datetime.now(JST)


def parse(value: str) -> datetime.datetime:
    """YouTubeが返す publishAt（RFC3339）を読む。"""
    # fromisoformat は末尾の 'Z' を受け付けないので明示的なオフセットに直す
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_rfc3339(dt: datetime.datetime) -> str:
    """publishAt に渡す形（UTCのZ表記）にする。"""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_jst_label(value: str) -> str:
    """予約時刻を人が読む形にする。ログとdry-runの表示用。"""
    return parse(value).astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def slots_of(day: datetime.date) -> list[datetime.datetime]:
    """その日の公開スロット（早い順）。"""
    return [datetime.datetime.combine(day, datetime.time(h), tzinfo=JST)
            for h in PUBLISH_HOURS_JST]


def next_slots(taken: list[str], count: int,
               now: datetime.datetime | None = None,
               per_day: int = PER_DAY_PER_CHANNEL) -> list[str]:
    """予約済み `taken` を避けて、空いている公開枠を古い順に `count` 個返す。

    `taken` は**同じチャンネルの**予約時刻（RFC3339）。チャンネルをまたいで
    数えると「1チャンネル1日2本」ではなく「全体で2本」になる。

    空きは**時刻ごと**に見る。日ごとの本数だけで数えると、23時が埋まっている日の
    22時を空きとして拾えない（#239）。あわせて1日の上限も見るのは、本人がStudioで
    22時でも23時でもない時刻に入れた日を数え落とさないため。

    枠が埋まっている日は飛ばして翌日以降に伸ばす。生成が公開ペースを上回る日が
    続けば予約は先へ延びるが、それは**待ち行列として正しい**（本数を増やして
    帳尻を合わせると、1日2本という指示のほうが壊れる）。
    """
    if per_day < 1:
        raise ValueError("per_day は1以上でなければ枠が永久に空かない")
    if count < 1:
        return []

    now = now or now_jst()
    reserved = {parse(v).astimezone(JST) for v in taken}
    used: dict[datetime.date, int] = {}
    for dt in reserved:
        used[dt.date()] = used.get(dt.date(), 0) + 1

    slots: list[str] = []
    day = now.date()
    while len(slots) < count:
        for slot in slots_of(day):
            if len(slots) >= count or used.get(day, 0) >= per_day:
                break
            if slot < now + MIN_LEAD or slot in reserved:
                continue
            slots.append(to_rfc3339(slot))
            reserved.add(slot)
            used[day] = used.get(day, 0) + 1
        day += datetime.timedelta(days=1)
    return slots


def calendar(taken: list[str], days: int = 7,
             now: datetime.datetime | None = None,
             per_day: int = PER_DAY_PER_CHANNEL) -> list[tuple[datetime.date, int]]:
    """今日から `days` 日ぶんの (日付, 予約本数) を返す。空き枠の確認用。"""
    now = now or now_jst()
    used: dict[datetime.date, int] = {}
    for value in taken:
        day = parse(value).astimezone(JST).date()
        used[day] = used.get(day, 0) + 1
    start = now.date()
    return [(start + datetime.timedelta(days=i), used.get(start + datetime.timedelta(days=i), 0))
            for i in range(days)]
