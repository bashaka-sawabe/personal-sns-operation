#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1日ぶんの動画を生成して限定公開まで投稿する（#168）。

    # 今日の在庫と投稿計画だけ見る
    .venv/bin/python tools/daily_run.py --dry-run

    # 生成→限定公開投稿まで自動で回す（公開への切り替えは本人がやる）
    .venv/bin/python tools/daily_run.py

    # 毎日回すなら crontab に1行（17時に生成、18時に本人がレビューして公開）:
    #   0 17 * * * cd /Users/bashaka/repo/personal/personal-sns-operation && .venv/bin/python tools/daily_run.py >> logs/daily_run.log 2>&1

設計（docs/08 の線引きに従う）:
- 採用済み（人・委任）の在庫を先に消費し、不足分は候補から**自動採用**する
  （2026-08-08 本人決定・#191。docs/08 1章）。fact系は裏取り（backing_url）付きの
  候補だけが対象で、**裏取りの無いネタは自動でも台本にしない**。
  自動採用は計画出力（←自動採用）と台帳（adopted_by: auto）で識別でき、
  本人はいつでも --reject で覆せる。
- 投稿は**限定公開まで**。公開切り替え（publish_youtube.py --release）は本人がやる。
- YouTubeのクォータは動画1本1,600ユニット・1プロジェクト1日10,000ユニット＝**6本**。
  チャンネル別の youtube_client_secret_<ch>.json を置いてプロジェクトを分けると
  その分だけ上限が増える。無い間は6本で止まり、残りは翌日に回る（黙って超えない）。
- heisei の日次は1ネタの掛け合い型。6選（fact6個消費）は在庫を食い潰すので
  手動の特別編としてのみ作る。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import fetch_facts, fetch_threads

# meme スレ候補の補充閾値（目標本数に対する倍率）。自動採用（#191）は目視選別より
# 消費が速いため、候補が目標を下回る前に収集して先回りする（#197）。
# ロンロン適性で絞るようにしたら合格率が実測12本中1本（8%）だったため、
# 3倍では1本も作れない日が出る。目標3本に対して30本の候補を持つ（#214）
THREAD_STOCK_FACTOR = 10
from tools.pipeline.common import OUT_DIR, PipelineError, read_secret, secret_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
THREADS_DIR = os.path.join(REPO, "data", "threads")
FACTS_DIR = os.path.join(REPO, "data", "facts")

# 各チャンネル毎日3本が目標（本人指示 2026-08-06）
TARGET_PER_CHANNEL = 3
CHANNELS = ["meme", "heisei", "showa"]
# 1プロジェクト1日のアップロード上限（10,000ユニット ÷ 1,600ユニット/本）
UPLOADS_PER_PROJECT = 6
SHARED_CLIENT_SECRET = "youtube_client_secret.json"


def _load_all(directory: str) -> list[dict]:
    items = []
    for p in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["_path"] = p
        items.append(d)
    return items


def stock_for(channel: str) -> list[dict]:
    """チャンネルの未消費ネタ（人・委任で採用済みのもの）を返す。"""
    if channel == "meme":
        return [t for t in _load_all(THREADS_DIR) if t["status"] == "adopted"]
    prefix = {"heisei": "heisei-", "showa": "showa-"}[channel]
    return [f for f in _load_all(FACTS_DIR)
            if f["status"] == "adopted" and os.path.basename(f["_path"]).startswith(prefix)]


def replenish_threads(per_channel: int) -> None:
    """meme のスレ候補が薄くなったら open2ch から収集する（#197）。

    重複排除（used / rejected 含む既存台帳との突き合わせ）と取得間隔
    （FETCH_INTERVAL・429対策 #99）は fetch_threads.collect が持つ。
    閾値以上あるときは外部を一切叩かない。
    """
    # 適性で落ちると分かっているスレは在庫として数えない（数えると補充が止まり、
    # 「候補は30本あるのに作れるものが0本」で詰まる。#214）
    candidates = [
        t for t in _load_all(THREADS_DIR)
        if t["status"] == "candidate"
        and (t.get("ronron_score") is None
             or t["ronron_score"] >= fetch_threads.RONRON_MIN)
    ]
    threshold = per_channel * THREAD_STOCK_FACTOR
    if len(candidates) >= threshold:
        return
    need = threshold - len(candidates)
    print(f"[meme] 使える候補{len(candidates)}本（閾値{threshold}本）のため収集中...")
    per_board = max(1, -(-need // len(fetch_threads.BOARDS)))
    for i, board in enumerate(fetch_threads.BOARDS):
        if i:
            # 板は違ってもサーバーは同じ（hayabusa）なので、板間でも間隔を空ける
            time.sleep(fetch_threads.FETCH_INTERVAL)
        try:
            saved = fetch_threads.collect(board, per_board)
            print(f"  {board}: {len(saved)}本を候補登録")
        except (PipelineError, urllib.error.URLError, OSError) as e:
            print(f"  {board}: 収集失敗（{e}）")


def auto_candidates(channel: str, scoring: bool = True) -> list[dict]:
    """採用済み在庫が足りないとき自動採用してよい候補（2026-08-08 本人決定・#191）。

    meme はロンロン適性（fetch_threads.score_candidates）で絞り、点の高い順に返す。
    合格が1本も無ければ**空を返す**。弱いネタで本数を埋めるより作らない方がよい（#214）。

    fact系は裏取り（backing_url）が付いた候補だけを対象にする。裏取り必須の線は
    採用の自動化とは別の品質保証なので、自動化しても緩めない（docs/08 1章）。
    """
    if channel == "meme":
        rows = [t for t in _load_all(THREADS_DIR) if t["status"] == "candidate"]
        # 目利きの代替は**レス数ではなくロンロン適性**（#214）。レス数順だと
        # 画像投稿スレ・順位表・実況が上位に来て、型に乗らないネタで作ってしまう
        if scoring:  # dry-run では外部を叩かない（採点済みのぶんだけ見る）
            try:
                scored = fetch_threads.score_candidates(rows)
                if scored:
                    print(f"[meme] スレ候補{scored}本の適性を採点しました")
                    rows = [t for t in _load_all(THREADS_DIR) if t["status"] == "candidate"]
            except PipelineError as e:
                print(f"[meme] 適性の採点に失敗（採点済みのぶんだけ使います）: "
                      f"{str(e).splitlines()[0]}")
        fit = [t for t in rows if (t.get("ronron_score") or 0) >= fetch_threads.RONRON_MIN]
        # 合格が無い日は**作らない**。弱いネタで1本埋めるより在庫不足として報告する
        return sorted(fit, key=lambda t: -(t.get("ronron_score") or 0))
    prefix = {"heisei": "heisei-", "showa": "showa-"}[channel]
    return [f for f in _load_all(FACTS_DIR)
            if f["status"] == "candidate" and (f.get("backing_url") or "").strip()
            and os.path.basename(f["_path"]).startswith(prefix)]


def _pick_powerword(thread: dict, feedback: str = "") -> dict:
    """スレからパワーワードとオチをLLMに選ばせる（人の目利きの代替。#191）。

    採用基準そのもの（本文に実在・内輪語なし・字数）は check_criteria が握っており、
    ここで選んだ結果も同じ検問を通る。基準を二重に実装しない。
    """
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if not api_key:
        raise PipelineError("ANTHROPIC_API_KEY が無いため自動採用できません")
    try:
        import anthropic
    except ImportError:
        raise PipelineError("anthropic SDK がありません（.venv/bin/pip install anthropic）") from None
    body = "\n".join(r["text"] for r in thread.get("res", []))[:6000]
    system = (
        "あなたは掲示板スレをショート動画にする編集者。スレから次の2つを選ぶ:\n"
        f"- powerword: 視聴者がコメント欄に書き写したくなる一番面白い語句。"
        f"**スレ本文にそのまま書かれている表現だけ**を使う（造語禁止）。"
        f"{fetch_threads.POWERWORD_MIN}〜{fetch_threads.POWERWORD_MAX}字。"
        "なんJ語・板の内輪語（ニキ・ワイ・草など）は選ばない\n"
        f"- ochi: このスレのオチを{fetch_threads.OCHI_MIN}〜{fetch_threads.OCHI_MAX}字の一文で"
    )
    if feedback:
        system += f"\n\n前回の選定は却下された。同じ間違いをしないこと:\n{feedback}"
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=500,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {"powerword": {"type": "string"}, "ochi": {"type": "string"}},
            "required": ["powerword", "ochi"],
            "additionalProperties": False,
        }}},
        messages=[{"role": "user", "content": f"タイトル: {thread['title']}\n\n{body}"}],
    )
    # content[0] を決め打ちで読まないこと。claude-opus-5 は adaptive thinking が既定で、
    # 思考が出た回だけ先頭が ThinkingBlock になり AttributeError で落ちる（#215）。
    # adaptive なので毎回は起きず、落ちた日は自動採用が静かに止まる
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise PipelineError("パワーワードの選定が空で返りました。")
    return json.loads(text)


def auto_adopt(channel: str, item: dict) -> bool:
    """候補を台帳上で採用に進める。基準を満たせなければ見送って False を返す。"""
    try:
        if channel == "meme":
            # 検問（check_criteria）に落ちたら、却下理由を渡して1回だけ選び直す
            feedback = ""
            for attempt in (1, 2):
                picked = _pick_powerword(item, feedback)
                try:
                    fetch_threads.mark(item["id"], "adopted",
                                       picked["powerword"], picked["ochi"])
                    break
                except PipelineError as e:
                    if attempt == 2:
                        raise
                    feedback = str(e)
            item["powerword"] = picked["powerword"].strip()
            item["ochi"] = picked["ochi"].strip()
        else:
            # fact系は裏取り済み候補だけが対象（auto_candidates）なので mark が通る
            fetch_facts.mark(item["id"], "adopted")
        item["status"] = "adopted"
        return True
    except PipelineError as e:
        print(f"  自動採用を見送り（{channel} / {item['id']}）: {str(e).splitlines()[0]}")
        return False


def mark_used(item: dict) -> None:
    """消費したネタに印を付ける（同じネタで2本作らないため）。"""
    path = item.pop("_path")
    item["status"] = "used"
    item["used_at"] = date.today().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False, indent=2)


def project_of(channel: str) -> str:
    """チャンネルが属するGoogle Cloudプロジェクト（クォータの単位）を返す。

    チャンネル専用の client secret があれば独立プロジェクト、無ければ共有。
    """
    own = secret_path(f"youtube_client_secret_{channel}.json")
    return channel if os.path.exists(own) else SHARED_CLIENT_SECRET


def _run(args: list[str]) -> tuple[int, str]:
    res = subprocess.run(args, capture_output=True, text=True, cwd=REPO)
    return res.returncode, (res.stdout + res.stderr)


def generate(channel: str, item: dict) -> str | None:
    """1本生成して動画パスを返す。失敗は None（理由は呼び出し側で表示済みの出力から）。"""
    if channel == "meme":
        args = [PYTHON, "tools/make_video.py", "--channel", channel, "--thread", item["id"]]
    else:
        args = [PYTHON, "tools/make_video.py", "--channel", channel, "--fact", item["id"]]
    code, out = _run(args)
    m = re.search(r"完成: (\S+\.mp4)", out)
    if code != 0 or not m:
        print(f"  生成失敗（{channel} / {item['id']}）:")
        print("    " + out.strip().splitlines()[-1] if out.strip() else "    (出力なし)")
        return None
    return os.path.join(REPO, m.group(1))


def upload(video: str) -> str | None:
    """限定公開で投稿してURLを返す。"""
    code, out = _run([PYTHON, "tools/publish_youtube.py", video])
    m = re.search(r"完了: (https://\S+)", out)
    if code != 0 or not m:
        print(f"  投稿失敗（{os.path.basename(video)}）:")
        print("    " + (out.strip().splitlines()[-1] if out.strip() else "(出力なし)"))
        return None
    return m.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="1日ぶんの生成と限定公開投稿をまとめて回す")
    parser.add_argument("--dry-run", action="store_true", help="在庫と計画だけ表示する")
    parser.add_argument("--per-channel", type=int, default=TARGET_PER_CHANNEL,
                        help=f"1チャンネルの本数（既定{TARGET_PER_CHANNEL}）")
    args = parser.parse_args()

    plan: list[tuple[str, dict]] = []
    quota_left: dict[str, int] = {}
    short_stock: list[str] = []
    deferred = 0

    if not args.dry_run:
        replenish_threads(args.per_channel)

    stocks: dict[str, list[dict]] = {}
    for ch in CHANNELS:
        adopted = stock_for(ch)
        # 採用済みを先に消費し、不足分だけ候補から自動採用する（人・委任の選別を無駄にしない）
        extra = auto_candidates(ch, scoring=not args.dry_run)[
            :max(0, args.per_channel - len(adopted))]
        need = args.per_channel - len(adopted) - len(extra)
        if need > 0 and ch != "meme" and not args.dry_run:
            # 事実ネタは発見→裏取りまで自動補充できる（#196）。dry-run では外部を叩かない
            print(f"[{ch}] 在庫不足のためネタを自動補充中（{need}本）...")
            try:
                added = fetch_facts.discover(ch, need)
                print(f"  {len(added)}本を裏取り付き候補として登録")
                extra = auto_candidates(ch)[:max(0, args.per_channel - len(adopted))]
            except PipelineError as e:
                print(f"  補充失敗: {str(e).splitlines()[0]}")
        for item in extra:
            item["_auto"] = True
        stocks[ch] = adopted + extra
        if len(stocks[ch]) < args.per_channel:
            short_stock.append(f"{ch}: 在庫{len(stocks[ch])}本（目標{args.per_channel}本）")
        quota_left.setdefault(project_of(ch), UPLOADS_PER_PROJECT)

    # クォータが足りない日でも特定チャンネルが0本にならないよう、1本ずつ順に取る
    for round_i in range(args.per_channel):
        for ch in CHANNELS:
            if round_i >= len(stocks[ch]):
                continue
            project = project_of(ch)
            if quota_left[project] <= 0:
                deferred += 1
                continue
            quota_left[project] -= 1
            plan.append((ch, stocks[ch][round_i]))

    print(f"本日の計画: {len(plan)}本")
    for ch, item in plan:
        label = item.get("title") or item.get("fact", "")[:40]
        mark = "　←自動採用" if item.get("_auto") else ""
        print(f"  {ch}: {item['id']}  {label}{mark}")
    if deferred:
        print(f"クォータ都合で翌日回し: {deferred}本"
              f"（プロジェクト分割かクォータ引き上げで解消できます。docs/09 2-5章）")
    if short_stock:
        if args.dry_run:
            print("⚠️ 在庫が目標に足りません。本実行では fact系（heisei / showa）を"
                  "fetch_facts --discover で自動補充します。meme は fetch_threads で候補を増やしてください:")
        else:
            print("⚠️ 自動補充・自動採用を含めても在庫が目標に足りません:")
        for line in short_stock:
            print(f"  {line}")
    if args.dry_run or not plan:
        return

    posted: list[str] = []
    for ch, item in plan:
        if item.get("_auto") and not auto_adopt(ch, item):
            continue
        print(f"[{ch}] {item['id']} を生成中...")
        video = generate(ch, item)
        if not video:
            continue
        if item.pop("_auto", False):
            # 人の採用を経ていないことを台帳に残す（後から --reject で覆す判断材料になる）
            item["adopted_by"] = "auto"
            item["adopted_at"] = date.today().isoformat()
        mark_used(item)
        url = upload(video)
        if url:
            posted.append(f"{ch}: {url}")

    print(f"\n投稿完了（限定公開）: {len(posted)}本")
    for line in posted:
        print(f"  {line}")
    if posted:
        print("\nレビュー後の公開切り替え:")
        print("  .venv/bin/python tools/publish_youtube.py --release <video_id> ...")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
