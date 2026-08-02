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
      立ち絵は content/assets/characters/<キャラ>.png に手動で置く（docs/09 4-8）
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import fetch_f1, fetch_facts, fetch_threads
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


def load_cast(cfg: dict) -> list:
    """配役の立ち絵を集める。無いキャラは知らせた上で立ち絵なしで続ける。"""
    cast, missing = [], []
    for key in channels.cast_keys(cfg):
        img = media.character_image(key)
        cast.append((key, img))
        if not img:
            missing.append(key)
    if missing:
        print("  ⚠️ 立ち絵がありません（立ち絵なしで続けます・投稿品質ではありません）:",
              file=sys.stderr)
        for key in missing:
            print(f"     content/assets/characters/{key}.png に配置してください"
                  "（素材規約は docs/08）", file=sys.stderr)
    return cast


def build_from_script(data: dict, script_id: str, offline: bool = False) -> str:
    script_mod.validate(data)  # 旧形式（ナレーション形式）はここで明確に落とす
    channel = data.get("channel") or data.get("genre") or ""
    cfg = channels.load(channel)

    asset_dir = os.path.join(ASSETS_DIR, script_id)
    ensure_dirs(asset_dir, OUT_DIR)
    out_path = os.path.join(OUT_DIR, f"{script_id}.mp4")
    warn_unfilled(data, script_id, channel)

    print(f"  素材を生成中（{len(data['scenes'])}シーン）...")
    # 立ち絵のクレジット（サイドカー）は media 側が credits.txt に書く
    scenes = media.build_scene_assets(data, asset_dir, offline=offline)
    cast = load_cast(cfg)
    total = sum(s["dur"] for s in scenes)
    bgm = media.bgm_track(script_id)
    if bgm:
        # CC BY 楽曲はクレジット表記が利用条件。投稿時の説明文に自動で入る
        media.append_credit(asset_dir, media.bgm_credit(bgm))
    print(f"  合成中（尺 {total:.1f}秒{'・BGMあり' if bgm else '・BGMなし'}）...")
    render.build(scenes, out_path, asset_dir, bgm=bgm, cast=cast)
    if status_mod.advance(script_id, "rendered"):
        print(f"  台帳: {script_id} を rendered に更新しました")
    return out_path


def make_one(channel: str, theme: str, offline: bool, script_only: bool,
             thread_id: str | None = None, fact_id: str | None = None,
             news_id: str | None = None) -> str:
    cfg = channels.load(channel)
    thread = fact = news = race_data = None
    if thread_id:
        # 未採用スレは load_adopted が拒否する（目視選別を飛ばして生成させない）
        thread = fetch_threads.load_adopted(thread_id)
        theme = theme or thread["title"]
    if fact_id:
        # 裏取り（一次ソース）の無いネタは load_adopted が拒否する
        fact = fetch_facts.load_adopted(fact_id)
        theme = theme or fact["fact"][:40]
    if news_id:
        news = fetch_f1.load_adopted(news_id)
        theme = theme or news["title"]
        # 見出しだけでは順位も差も曖昧なので、数字は必ずAPIから渡す
        race_data = fetch_f1.race_context()
    scripts_dir = os.path.join(SCRIPTS_DIR, channel)
    ensure_dirs(scripts_dir)
    script_id = slugify(channel)
    print(f"[{script_id}] {channel} / {theme}")

    print("  台本を生成中...")
    data = script_mod.generate(cfg, theme, offline=offline, thread=thread, fact=fact,
                               news=news, race_data=race_data)
    data["channel"] = channel
    data["genre"] = channel  # 計測（fetch_metrics）の集計キーとの互換。値はチャンネル名
    data["theme"] = theme
    if thread:
        # 引用元の来歴。投稿前チェックリスト（docs/05 6章）の
        # 「引用元が転載自由ソース」をファイルだけで確認できるようにする
        data["source_thread"] = {"id": thread["id"], "url": thread["url"],
                                 "title": thread["title"]}
    if fact:
        # 裏取りの来歴。「一次ソースURLが台本に記録されている」チェックの実体
        data["source_fact"] = {"id": fact["id"], "fact": fact["fact"],
                               "backing_url": fact["backing_url"],
                               "backing_note": fact.get("backing_note", "")}
    if news:
        data["source_news"] = {"id": news["id"], "title": news["title"],
                               "url": news["url"], "source": news["source"]}
    path = script_mod.save(data, script_id, scripts_dir)
    print(f"  台本: {os.path.relpath(path)}")
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
    p.add_argument("--news", help="採用済みF1ニュースのID（fetch_f1.py --adopt 済み）")
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

        if not channel or not (args.theme or args.thread or args.fact or args.news):
            p.error("--channel と、ネタの指定（--theme / --thread / --fact / --news）"
                    "（または --batch / --from-script）を指定してください")

        out = make_one(channel, args.theme, args.offline, args.script_only,
                       thread_id=args.thread, fact_id=args.fact, news_id=args.news)
        print(f"完成: {os.path.relpath(out)}")

    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
