#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実績（公開後の数字）をネタ選定に還元する（#293）。

    # チャンネル×ネタ系統の実績集計と係数を表示する
    .venv/bin/python tools/feedback.py

ネタ採点は ronron_score（LLMの主観）だけで、公開済み動画の実測が生成側に
一切戻っていなかった。ここは
  週次CSV（data/YYYY-Www.csv・#291）× 投稿台帳（.published_youtube.json）
  × 台本（source_thread）× ネタ台帳（ronron_kind）
を突き合わせて、ネタ系統ごとの**実績係数**を出す。daily_run は係数を
自動採用の**並び順にだけ**効かせる（合格ライン RONRON_MIN には触れない）。

過学習ガード（docs/09「測っていない型を機械に守らせると、根拠のない形に固定される」）:
- n数が KIND_MIN_N 未満の系統は係数1.0（中立）。少数のバズ・事故で振り切らせない
- 係数は FACTOR_MIN〜FACTOR_MAX にクランプする
"""
import csv
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import publish_youtube as yt

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
THREADS_DIR = os.path.join(DATA_DIR, "threads")

# 系統として集計する最低本数。2本以下の平均は「実績」ではなく「たまたま」
KIND_MIN_N = 3
# 係数の可動域。順位の入れ替えには十分で、1系統を事実上の出禁にはしない幅
FACTOR_MIN = 0.85
FACTOR_MAX = 1.15

_SHORTS_URL = re.compile(r"(?:shorts/|v=)([\w-]{6,})")


def _metric_rows() -> dict:
    """週次CSV全部から video_id → 実績行。同じ動画は数値の新しい行で上書きする。"""
    rows = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "????-W??.csv"))):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("platform") != "youtube_shorts":
                    continue
                m = _SHORTS_URL.search(row.get("url") or "")
                if m:
                    rows[m.group(1)] = row
    return rows


def _kind_of(stem: str) -> str | None:
    """動画（ファイル名の拡張子なし）→ ネタ系統。台本の source_thread から引く。"""
    script = yt.script_for(f"{stem}.mp4")
    thread_id = (script or {}).get("source_thread")
    if not thread_id:
        return None
    path = os.path.join(THREADS_DIR, f"{thread_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("ronron_kind")


def _to_float(value) -> float | None:
    try:
        v = float(str(value or "").strip())
    except ValueError:
        return None
    return v


def collect_samples(channel: str) -> list:
    """公開済み動画の (系統, 完走率, 登録者数/1000再生) を集める。

    数字がまだ無い動画（公開直後・Analytics欠測）は含めない。0埋めすると
    「数字が無い」が「成績が悪い」にすり替わる。
    """
    metrics = _metric_rows()
    samples = []
    for name, rec in yt.load_ledger().items():
        stem = os.path.splitext(name)[0]
        if stem.split("-")[0] != channel:
            continue
        row = metrics.get(rec.get("video_id") or "")
        if not row:
            continue
        completion = _to_float(row.get("completion_rate"))
        views = _to_float(row.get("views_total")) or 0.0
        follows = _to_float(row.get("follows_gained"))
        per_1k = (follows / views * 1000) if (follows is not None and views > 0) else None
        if completion is None and per_1k is None:
            continue
        samples.append((_kind_of(stem), completion, per_1k))
    return samples


def factors_from_samples(samples: list) -> dict:
    """(系統, 完走率, 登録/1k) の一覧から 系統→係数 を計算する（純粋関数・テスト対象）。

    係数 = 系統平均 ÷ 全体平均 を、取れている指標ごとに出して平均したもの。
    全体平均は系統不明（旧動画）も含めて取る。ベースラインを新しい動画だけで
    作ると、比較の土台が系統の分布と一緒に動いてしまう。
    """
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return (sum(xs) / len(xs)) if xs else None

    base_completion = mean([c for _, c, _ in samples])
    base_per_1k = mean([p for _, _, p in samples])

    factors = {}
    kinds = {k for k, _, _ in samples if k}
    for kind in kinds:
        rows = [(c, p) for k, c, p in samples if k == kind]
        if len(rows) < KIND_MIN_N:
            factors[kind] = 1.0
            continue
        ratios = []
        kc = mean([c for c, _ in rows])
        if kc is not None and base_completion:
            ratios.append(kc / base_completion)
        kp = mean([p for _, p in rows])
        if kp is not None and base_per_1k:
            ratios.append(kp / base_per_1k)
        if not ratios:
            factors[kind] = 1.0
            continue
        factors[kind] = max(FACTOR_MIN, min(FACTOR_MAX, sum(ratios) / len(ratios)))
    return factors


def kind_factors(channel: str) -> dict:
    """系統→実績係数。データが無い系統・不明系統は呼び出し側で1.0として扱う。"""
    return factors_from_samples(collect_samples(channel))


def describe_factors(channel: str) -> str:
    """daily_run のログ用。空文字なら実績データがまだ無い。"""
    samples = collect_samples(channel)
    if not samples:
        return ""
    factors = factors_from_samples(samples)
    if not factors:
        # 係数が動き出す前から行を出す。ループが繋がっていること自体を毎朝可視化する
        return f"実績係数: 中立（実績{len(samples)}本・系統別の蓄積待ち）"
    counts = {}
    for k, _, _ in samples:
        if k:
            counts[k] = counts.get(k, 0) + 1
    parts = []
    for kind in sorted(factors):
        n = counts.get(kind, 0)
        neutral = "・中立" if n < KIND_MIN_N else ""
        parts.append(f"{kind}×{factors[kind]:.2f}(n={n}{neutral})")
    return "実績係数: " + "　".join(parts)


def main() -> None:
    for channel in ("meme", "heisei", "showa"):
        samples = collect_samples(channel)
        print(f"[{channel}] 実績あり {len(samples)}本")
        if not samples:
            continue
        factors = factors_from_samples(samples)
        by_kind = {}
        for k, c, p in samples:
            by_kind.setdefault(k or "(系統なし)", []).append((c, p))
        for kind, rows in sorted(by_kind.items()):
            cs = [c for c, _ in rows if c is not None]
            ps = [p for _, p in rows if p is not None]
            avg_c = f"{sum(cs) / len(cs):.3f}" if cs else "-"
            avg_p = f"{sum(ps) / len(ps):.2f}" if ps else "-"
            factor = factors.get(kind, 1.0)
            print(f"  {kind}: n={len(rows)} 完走率={avg_c} 登録/1k={avg_p} 係数={factor:.2f}")


if __name__ == "__main__":
    main()
