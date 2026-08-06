#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台本の caption / hashtags を Threads に投稿する。

    # 1本投稿（テキスト。テーマの反応を動画化の前に安く見る用途）
    .venv/bin/python tools/publish_threads.py content/scripts/meme/meme-016.json

    # 動画つき投稿（Threads APIは公開URLしか受け取れない。ローカルファイル不可）
    .venv/bin/python tools/publish_threads.py content/scripts/meme/meme-016.json \
        --video-url https://example.com/meme-016.mp4

    # 投稿せず、組み立てた本文だけ確認する
    .venv/bin/python tools/publish_threads.py --all --dry-run

    # 未投稿の台本をまとめて出す
    .venv/bin/python tools/publish_threads.py --all

なぜThreadsか（docs/09）: APIが4面で最も軽く（コンテナ作成→publishの2段階のみ）、
**テキストで反応を見てから動画化する**テスト場になる。

準備（初回のみ・人がやる必要がある）:
1. https://developers.facebook.com/ でアプリを作り、Threads API を有効にする
2. 長期アクセストークンを取得して ~/repo/.cowork-secrets/threads_access_token.txt に置く
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import OUT_DIR, SCRIPTS_DIR, PipelineError, read_secret, secret_path

API_BASE = "https://graph.threads.net/v1.0"
TOKEN_FILE = "threads_access_token.txt"
# Threads の本文上限。超えると API が落ちるので手前で検知して分かる形で止める
TEXT_LIMIT = 500
# 動画コンテナの処理待ち。長尺でも数十秒で終わるが、余裕を持って5分で諦める
VIDEO_WAIT_SEC = 300
# 投稿済みの記録。--all で二重投稿しないために使う（publish_youtube.py と同じ形）
LEDGER = os.path.join(OUT_DIR, ".published_threads.json")


def _token() -> str:
    token = read_secret("THREADS_ACCESS_TOKEN", TOKEN_FILE)
    if not token:
        raise PipelineError(
            "Threadsのアクセストークンがありません。\n"
            "1. https://developers.facebook.com/ でアプリを作成し、Threads API を有効にする\n"
            "2. 長期アクセストークン（threads_basic, threads_content_publish 権限）を取得する\n"
            f"3. 次のファイルに保存する: {secret_path(TOKEN_FILE)}\n"
            "   （環境変数 THREADS_ACCESS_TOKEN でも可）"
        )
    return token


def _call(path: str, params: dict, method: str = "GET") -> dict:
    """Graph API を叩いて JSON を返す。失敗はエラーメッセージを日本語文脈に包んで投げる。"""
    url = f"{API_BASE}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            message = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            message = body
        raise PipelineError(f"Threads APIが失敗しました（HTTP {e.code}）: {message}") from None
    except urllib.error.URLError as e:
        raise PipelineError(f"Threads APIに接続できません: {e.reason}") from None


def _user_id(token: str) -> str:
    return _call("me", {"fields": "id", "access_token": token})["id"]


def build_text(script: dict) -> str:
    """caption と hashtags から本文を組み立てる。"""
    caption = (script.get("caption") or "").strip()
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in script.get("hashtags", []))
    text = "\n\n".join(p for p in (caption, tags) if p)
    if not text:
        raise PipelineError("台本に caption も hashtags もありません。投稿する本文が作れません。")
    if len(text) > TEXT_LIMIT:
        raise PipelineError(
            f"本文が{len(text)}字あります（Threadsの上限は{TEXT_LIMIT}字）。caption を短くしてください。"
        )
    return text


def _wait_container(container_id: str, token: str) -> None:
    """動画コンテナの処理完了を待つ。FINISHED になる前に publish すると失敗する。"""
    deadline = time.time() + VIDEO_WAIT_SEC
    while time.time() < deadline:
        res = _call(container_id, {"fields": "status,error_message", "access_token": token})
        status = res.get("status")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise PipelineError(f"動画コンテナの処理に失敗しました: {res.get('error_message', '(理由不明)')}")
        time.sleep(5)
    raise PipelineError(f"動画コンテナが{VIDEO_WAIT_SEC}秒たっても処理中のままです。時間を置いて再実行してください。")


def publish(script_path: str, video_url: str | None, dry_run: bool,
            ledger: dict) -> str | None:
    """1本投稿してパーマリンクを返す。dry_run なら本文表示のみで None。"""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    stem = os.path.splitext(os.path.basename(script_path))[0]
    text = build_text(script)

    if stem in ledger:
        print(f"投稿済みなのでスキップ: {stem} → {ledger[stem].get('permalink', '')}")
        return None
    if dry_run:
        kind = "動画つき" if video_url else "テキスト"
        print(f"投稿予定（{kind}）: {stem}\n---\n{text}\n---")
        return None

    token = _token()
    uid = _user_id(token)

    params = {"access_token": token, "text": text}
    if video_url:
        params.update(media_type="VIDEO", video_url=video_url)
    else:
        params["media_type"] = "TEXT"
    container = _call(f"{uid}/threads", params, method="POST")["id"]
    if video_url:
        _wait_container(container, token)
    media = _call(f"{uid}/threads_publish",
                  {"access_token": token, "creation_id": container}, method="POST")["id"]
    permalink = _call(media, {"fields": "permalink", "access_token": token}).get("permalink", "")

    ledger[stem] = {"media_id": media, "permalink": permalink,
                    "posted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_ledger(ledger)
    print(f"完了: {stem} → {permalink or media}")
    return permalink


def load_ledger() -> dict:
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(ledger: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def all_scripts() -> list[str]:
    """全チャンネルの台本を新しいものから返す（採番の大きい順）。"""
    paths = glob.glob(os.path.join(SCRIPTS_DIR, "*", "*.json"))
    return sorted(paths, key=lambda p: os.path.basename(p), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="台本の caption / hashtags を Threads に投稿する")
    parser.add_argument("script", nargs="?", help="台本JSON")
    parser.add_argument("--all", action="store_true", help="未投稿の台本をまとめて出す")
    parser.add_argument("--video-url", help="動画つきで投稿する（公開URLのみ。ローカルファイル不可）")
    parser.add_argument("--dry-run", action="store_true", help="投稿せず本文だけ確認する")
    args = parser.parse_args()

    if not args.script and not args.all:
        parser.error("台本JSONか --all を指定してください")
    if args.all and args.video_url:
        parser.error("--video-url は1本指定のときだけ使えます（URLは動画ごとに違うため）")

    ledger = load_ledger()
    targets = all_scripts() if args.all else [args.script]
    if not targets:
        print("台本がありません。")
        return
    skipped = []
    for path in targets:
        if not os.path.exists(path):
            raise PipelineError(f"台本がありません: {path}")
        try:
            publish(path, args.video_url, args.dry_run, ledger)
        except PipelineError as e:
            # --all は1本の不備（captionなし等）で全体を止めない。理由を出して次へ
            if not args.all:
                raise
            skipped.append((os.path.basename(path), str(e)))
    if skipped:
        print(f"\n投稿しなかった台本（{len(skipped)}本）:")
        for name, reason in skipped:
            print(f"  {name}: {reason.splitlines()[0]}")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
