#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ショート動画を1本作る（掛け合い台本 → 素材 → 縦動画）。

    # 1本作る
    python3 tools/make_video.py --channel biz --theme "法人化で税金は減るのか"

    # APIキー無しで疎通確認（テンプレ台本＋ローカル素材だけで最後まで通す）
    python3 tools/make_video.py --channel biz --theme "テスト" --offline

    # 3チャンネル分をまとめて（2週間テストの本体）
    python3 tools/make_video.py --batch data/themes.md

    # 台本だけ先に作って、中身を見てから動画化する
    python3 tools/make_video.py --channel biz --theme "..." --script-only
    python3 tools/make_video.py --from-script content/scripts/biz/biz-001.json

出力:
    content/scripts/<ch>/<id>.json  台本（手直しして再実行できる）
    content/assets/<id>/            背景・音声・シーンmp4（中間物）
    content/out/<id>.mp4            完成した縦動画

前提: ffmpeg（必須）／台本生成に anthropic SDK と ANTHROPIC_API_KEY
      （どちらも無ければ --offline 相当で動く）
      話者アイコンは自動生成される（差し替えたいときだけ
      content/assets/icons/<キャラ>.png に置く。docs/09 4-8）
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import fetch_facts, fetch_memes, fetch_threads
from tools.pipeline import channels, media, render, script as script_mod, status as status_mod
from tools.pipeline.common import (
    ASSETS_DIR, OUT_DIR, SCRIPTS_DIR, PipelineError, ensure_dirs,
)


def slugify(channel: str) -> str:
    """ファイル名に使える id を作る。日本語は落ちるので連番で担保する。"""
    base = re.sub(r"[^a-z0-9]+", "-", f"{channel}".lower()).strip("-") or "video"
    n = 1
    while os.path.exists(os.path.join(SCRIPTS_DIR, channel, f"{base}-{n:03d}.json")):
        n += 1
    return f"{base}-{n:03d}"


def warn_unfilled(data: dict, script_id: str, channel: str) -> None:
    """本人が埋めるべき一次情報が残っていたら知らせる。

    止めはしない（絵を先に確認したいことがある）が、この状態で投稿すると
    画面に「【要実体験】」が焼き込まれたまま公開される。
    """
    spots = script_mod.unfilled(data)
    if not spots:
        return
    print(f"  ⚠️ 一次情報が未記入です（{len(spots)}箇所）: {'、'.join(spots)}", file=sys.stderr)
    print(f"     content/scripts/{channel}/{script_id}.json を開いて "
          f"「{script_mod.PLACEHOLDER}」を実際の内容に置き換え、"
          f"--from-script で作り直してください。", file=sys.stderr)
    print("     このまま投稿すると、画面に目印が焼き込まれたまま公開されます。",
          file=sys.stderr)


def warn_flat_dialogue(data: dict, cfg: dict | None = None) -> None:
    """会話が平坦（相槌だらけ・語尾が同じ）なら知らせる。

    止めはしない。会話の善し悪しは最後は人が見るものだが、
    「形容詞＋のだ」だけの台本が黙って通ると気づけない（#127）。
    """
    for issue in script_mod.dialogue_issues(data, cfg):
        print(f"  ⚠️ 会話が平坦です: {issue}", file=sys.stderr)


def flicker_issue(out_path: str) -> str:
    """完成した動画が明滅していたら、その内容を一行で返す（問題なければ空文字）。

    素材の取得時にも同じ検査をしている（media.stock_background・#261）が、
    **入り口が1つとは限らない。** 背景を生成AIに替えれば素材の検査は通らなくなり（#35）、
    render.py を触れば演出側から明滅が戻る（#177 のズーム振動が実際そうだった）。
    チカチカは3度別の入り口から再発しているので、原因がどこでも必ず通る
    「出来上がった mp4 そのもの」で測る。
    """
    at, rate = media.flicker_peak(out_path)
    if rate < media.FLICKER_MAX:
        return ""
    return f"明滅 {rate:.1f}回/秒（{at:.0f}秒付近）"


def build_from_script(data: dict, script_id: str, offline: bool = False) -> str:
    script_mod.validate(data)  # 旧形式（ナレーション形式）はここで明確に落とす
    channel = data.get("channel") or data.get("genre") or ""
    cfg = channels.load(channel)

    asset_dir = os.path.join(ASSETS_DIR, script_id)
    ensure_dirs(asset_dir, OUT_DIR)
    out_path = os.path.join(OUT_DIR, f"{script_id}.mp4")
    warn_unfilled(data, script_id, channel)

    print(f"  素材を生成中（{len(data['scenes'])}シーン）...")
    # 演出（尺・テンポ・効果音・カット）はチャンネルごとに違う（docs/02 1章）
    style = cfg.get("style", {})
    scenes = media.build_scene_assets(data, asset_dir, offline=offline, style=style)
    total = sum(s["dur"] for s in scenes)
    bgm = media.bgm_track(script_id)
    if bgm:
        # CC BY 楽曲はクレジット表記が利用条件。投稿時の説明文に自動で入る
        media.append_credit(asset_dir, media.bgm_credit(bgm))
    print(f"  合成中（尺 {total:.1f}秒{'・BGMあり' if bgm else '・BGMなし'}）...")
    render.build(scenes, out_path, asset_dir, bgm=bgm, style=style,
                 powerword=data.get("powerword", ""))
    flicker = flicker_issue(out_path)
    # 行が無ければここで作る。作らないと、自動生成した動画が台帳に載らないまま
    # 投稿・公開予約まで進み、公開を止める --unreserve が効かなくなる（#243）
    # 明滅で落とす場合も、行だけは残して理由を書く（後から「なぜ出さなかったか」を読める）
    if status_mod.ensure(script_id, channel, "rendered", note=flicker or None):
        print(f"  台帳: {script_id} を rendered に更新しました")
    if flicker:
        # ここで落とすと daily_run は**この1本だけ**を投稿せず次の本へ進む（#242）。
        # mp4 は消さずに残す。目で確かめないと素材と演出のどちらが原因か分からない
        raise PipelineError(
            f"完成した動画がチカチカしています（{flicker}）。投稿しません。\n"
            f"  {os.path.relpath(out_path)} の該当箇所を見て、"
            f"背景素材（台本の image_prompt）か演出（render.py）を直してから作り直してください。"
        )
    return out_path


def make_one(channel: str, theme: str, offline: bool, script_only: bool,
             thread_id: str | None = None, fact_id: str | None = None,
             fact_ids: list | None = None, meme_id: str | None = None) -> str:
    cfg = channels.load(channel)
    thread = fact = meme = None
    facts = None
    if meme_id:
        # 権利で使えないミームは load_adopted が拒否する（docs/04 2-2章）
        meme = fetch_memes.load_adopted(meme_id)
        theme = theme or meme["name"]
    if thread_id:
        # 未採用スレは load_adopted が拒否する（目視選別を飛ばして生成させない）
        thread = fetch_threads.load_adopted(thread_id)
        theme = theme or thread["title"]
    if fact_id:
        # 裏取り（一次ソース）の無いネタは load_adopted が拒否する
        fact = fetch_facts.load_adopted(fact_id)
        theme = theme or fact["fact"][:40]
    if fact_ids:
        # 複数指定は「◯◯選」リスト形式になる（heisei。docs/02 4章）
        facts = [fetch_facts.load_adopted(i) for i in fact_ids]
        theme = theme or f"{len(facts)}選"
    scripts_dir = os.path.join(SCRIPTS_DIR, channel)
    ensure_dirs(scripts_dir)
    script_id = slugify(channel)
    print(f"[{script_id}] {channel} / {theme}")

    print("  台本を生成中...")
    data = script_mod.generate(cfg, theme, offline=offline, thread=thread, fact=fact,
                               facts=facts, meme=meme)
    data["channel"] = channel
    data["genre"] = channel  # 計測（fetch_metrics）の集計キーとの互換。値はチャンネル名
    data["theme"] = theme
    if thread:
        # 引用元の来歴。投稿前チェックリスト（docs/05 6章）の
        # 「引用元が転載自由ソース」をファイルだけで確認できるようにする
        data["source_thread"] = {"id": thread["id"], "url": thread["url"],
                                 "title": thread["title"]}
    if meme:
        # 来歴。「原文を引用していない」ことを後から確認できるようにする
        data["source_meme"] = {"id": meme["id"], "name": meme["name"],
                               "skeleton": meme["skeleton"],
                               "rights": meme["rights"], "origin": meme.get("origin", "")}
    for f in ([fact] if fact else []) + (facts or []):
        # 裏取りの来歴。「一次ソースURLが台本に記録されている」チェックの実体
        data.setdefault("source_facts", []).append(
            {"id": f["id"], "fact": f["fact"], "backing_url": f["backing_url"],
             "backing_note": f.get("backing_note", "")})
    path = script_mod.save(data, script_id, scripts_dir)
    print(f"  台本: {os.path.relpath(path)}")
    # 消費した印は**台本が保存できてから**付ける。先に付けると生成が落ちたときに
    # ネタだけ失う。ここで付けないと単発生成のぶんが adopted のまま残り、
    # 次回の daily_run が同じネタでもう1本作る（#230）
    used = [i for i in ([thread["id"]] if thread else []) if fetch_threads.mark_used(i)]
    used += [meme["id"]] if meme and fetch_memes.mark_used(meme["id"]) else []
    used += [f["id"] for f in ([fact] if fact else []) + (facts or [])
             if fetch_facts.mark_used(f["id"])]
    if used:
        print(f"  ネタ台帳: {'、'.join(used)} を used にしました")
    warn_flat_dialogue(data, cfg)
    if script_only:
        warn_unfilled(data, script_id, channel)
        return path
    return build_from_script(data, script_id, offline=offline)


def read_themes(path: str) -> list:
    """Markdownのテーマ一覧を読む。

    `## チャンネル` の見出し以下の `- テーマ` を拾う。見出しの前にある箇条書きは
    説明文とみなして無視する。表・引用・コードブロックは自然に対象外になる。
    """
    rows, channel = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            heading = re.match(r"^##\s+(.+)$", line)
            if heading:
                title = heading.group(1).strip()
                # 「## biz — お金」のような注釈付きでも先頭の識別子だけを使う
                token = re.split(r"[\s—–\-:：(（]", title, maxsplit=1)[0].strip()
                # 設計の説明セクションはチャンネルではないので拾わない
                channel = token if re.fullmatch(r"[A-Za-z0-9_]+", token) else None
                continue
            item = re.match(r"^\s*[-*]\s+(.+)$", line)
            if item and channel:
                rows.append((channel, item.group(1).strip()))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="掛け合いショート動画を生成する")
    p.add_argument("--channel", help=f"チャンネル（{' / '.join(channels.available()) or 'girls / biz / meme'}）")
    p.add_argument("--genre", help=argparse.SUPPRESS)  # 旧名。--channel の別名として残す
    p.add_argument("--theme", help="テーマ（動画1本の中身。--thread があれば省略可）")
    p.add_argument("--thread", help="採用済みスレのID（fetch_threads.py --adopt 済みのもの）")
    p.add_argument("--fact", help="採用済みネタのID（fetch_facts.py --adopt 済み・裏取り必須）")
    p.add_argument("--meme", help="採用済みミームのID（fetch_memes.py --adopt 済み・骨格から書き下ろす）")
    p.add_argument("--facts", nargs="+", metavar="ID",
                   help="採用済みネタを複数指定して「◯◯選」形式にする（heisei）")
    p.add_argument("--batch", help="チャンネルとテーマの一覧ファイル（1行1本）")
    p.add_argument("--from-script", help="既存の台本JSONから動画だけ作り直す")
    p.add_argument("--offline", action="store_true", help="APIを一切使わず疎通確認する")
    p.add_argument("--script-only", action="store_true", help="台本だけ作って止める")
    args = p.parse_args()
    channel = args.channel or args.genre

    try:
        if args.from_script:
            data = script_mod.load(args.from_script)
            script_id = data.get("id") or os.path.splitext(os.path.basename(args.from_script))[0]
            print(f"[{script_id}] 台本から再生成")
            print(f"完成: {os.path.relpath(build_from_script(data, script_id, offline=args.offline))}")
            return

        if args.batch:
            rows = read_themes(args.batch)
            if not rows:
                sys.exit(f"{args.batch} に有効な行がありません。")
            print(f"{len(rows)}本を生成します\n")
            done, failed = [], []
            for ch, theme in rows:
                try:
                    done.append(make_one(ch, theme, args.offline, args.script_only))
                except PipelineError as e:
                    # 1本の失敗でバッチ全体を止めない。残りを作り切ってから報告する
                    print(f"  失敗: {e}\n", file=sys.stderr)
                    failed.append((ch, theme))
            print(f"\n完了: {len(done)}本 / 失敗: {len(failed)}本")
            for ch, theme in failed:
                print(f"  - {ch} / {theme}", file=sys.stderr)
            return

        if not channel or not (args.theme or args.thread or args.fact
                               or args.facts or args.meme):
            p.error("--channel と、ネタの指定（--theme / --thread / --fact / --facts / --meme）"
                    "（または --batch / --from-script）を指定してください")

        out = make_one(channel, args.theme, args.offline, args.script_only,
                       thread_id=args.thread, fact_id=args.fact, fact_ids=args.facts,
                       meme_id=args.meme)
        print(f"完成: {os.path.relpath(out)}")

    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
