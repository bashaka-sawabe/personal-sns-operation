#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成した縦動画を Instagram Reels に投稿する。

    # 1本投稿（台本と同名の content/out/<id>.mp4 を自動で探す）
    .venv/bin/python tools/publish_instagram.py content/scripts/meme/meme-016.json

    # 投稿せず、本文と動画の有無だけ確認する
    .venv/bin/python tools/publish_instagram.py --all --dry-run

Instagram の Content Publishing API は**動画を公開URLで渡す必要があり、
ローカルファイルを直接アップロードできない**。ここは cloudflared の quick tunnel
（無料・アカウント不要）で content/out を投稿の間だけ外に見せて解決する（docs/09 7章）。
恒久ホスティングを契約しないのは、URLが必要なのはIGが動画を取りに来る数十秒だけだから。

準備（初回のみ・人がやる必要がある）:
1. Instagramを**プロアカウント**にする（APIの前提。個人アカウントでは動かない）
2. https://developers.facebook.com/ でアプリを作り、Instagram API（Instagramログイン）を設定する
3. 長期アクセストークン（instagram_business_basic, instagram_business_content_publish）を
   ~/repo/.cowork-secrets/instagram_access_token.txt に置く
4. `brew install cloudflared`
"""
import argparse
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import OUT_DIR, SCRIPTS_DIR, PipelineError, read_secret, require, secret_path

API_BASE = "https://graph.instagram.com/v23.0"
TOKEN_FILE = "instagram_access_token.txt"
CAPTION_LIMIT = 2200
# コンテナ処理待ち。IGは動画の取得+変換に時間がかかることがあるため長めに取る
CONTAINER_WAIT_SEC = 600
TUNNEL_WAIT_SEC = 30
# 投稿済みの記録。--all で二重投稿しないために使う（publish_youtube.py と同じ形）
LEDGER = os.path.join(OUT_DIR, ".published_instagram.json")


def _token() -> str:
    token = read_secret("INSTAGRAM_ACCESS_TOKEN", TOKEN_FILE)
    if not token:
        raise PipelineError(
            "Instagramのアクセストークンがありません。\n"
            "1. Instagramをプロアカウントにする（個人アカウントではAPIが使えません）\n"
            "2. https://developers.facebook.com/ でアプリを作成し、Instagram APIを設定する\n"
            "3. 長期アクセストークン（instagram_business_basic, instagram_business_content_publish）を取得する\n"
            f"4. 次のファイルに保存する: {secret_path(TOKEN_FILE)}\n"
            "   （環境変数 INSTAGRAM_ACCESS_TOKEN でも可）"
        )
    return token


def _call(path: str, params: dict, method: str = "GET") -> dict:
    """Graph API を叩いて JSON を返す。失敗はエラーメッセージを日本語文脈に包んで投げる。"""
    url = f"{API_BASE}/{path}"
    if method == "GET":
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            message = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            message = body
        raise PipelineError(f"Instagram APIが失敗しました（HTTP {e.code}）: {message}") from None
    except urllib.error.URLError as e:
        raise PipelineError(f"Instagram APIに接続できません: {e.reason}") from None


def _user_id(token: str) -> str:
    return str(_call("me", {"fields": "user_id", "access_token": token})["user_id"])


class PublicHost:
    """content/out を投稿の間だけ公開URLにする（ローカルHTTP + cloudflared quick tunnel）。

    IGが動画を取りに来る間だけ立てて、終わったら必ず落とす。
    quick tunnel はアカウント不要だがURLが毎回変わる。恒久用途には使わない。
    """

    def __init__(self):
        self._procs = []
        self.base_url = ""

    def __enter__(self):
        cloudflared = require("cloudflared")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        # http.server は OUT_DIR だけを見せる（リポジトリ全体を晒さない）
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=OUT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        tunnel = subprocess.Popen(
            [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        self._procs = [tunnel, server]
        deadline = time.time() + TUNNEL_WAIT_SEC
        for line in tunnel.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                self.base_url = m.group(0)
                break
            if time.time() > deadline:
                break
        if not self.base_url:
            self.__exit__(None, None, None)
            raise PipelineError(
                f"cloudflaredのトンネルURLが{TUNNEL_WAIT_SEC}秒以内に取れませんでした。"
                "ネットワークを確認して再実行してください。"
            )
        return self

    def __exit__(self, *_):
        for p in self._procs:
            p.terminate()
        for p in self._procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    def url_for(self, video_path: str) -> str:
        return f"{self.base_url}/{urllib.parse.quote(os.path.basename(video_path))}"


def build_caption(script: dict) -> str:
    caption = (script.get("caption") or "").strip()
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in script.get("hashtags", []))
    text = "\n\n".join(p for p in (caption, tags) if p)
    if len(text) > CAPTION_LIMIT:
        raise PipelineError(f"キャプションが{len(text)}字あります（上限{CAPTION_LIMIT}字）。")
    return text


def _wait_container(container_id: str, token: str) -> None:
    """コンテナの処理完了を待つ。FINISHED前にpublishすると失敗する。"""
    deadline = time.time() + CONTAINER_WAIT_SEC
    while time.time() < deadline:
        res = _call(container_id, {"fields": "status_code,status", "access_token": token})
        code = res.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise PipelineError(f"コンテナの処理に失敗しました: {res.get('status', '(理由不明)')}")
        time.sleep(10)
    raise PipelineError(f"コンテナが{CONTAINER_WAIT_SEC}秒たっても処理中のままです。")


def video_path_for(script_path: str) -> str:
    stem = os.path.splitext(os.path.basename(script_path))[0]
    return os.path.join(OUT_DIR, f"{stem}.mp4")


def publish(script_path: str, dry_run: bool, ledger: dict) -> str | None:
    """1本投稿してパーマリンクを返す。dry_run なら確認表示のみで None。"""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    stem = os.path.splitext(os.path.basename(script_path))[0]
    caption = build_caption(script)
    video = video_path_for(script_path)

    if stem in ledger:
        print(f"投稿済みなのでスキップ: {stem} → {ledger[stem].get('permalink', '')}")
        return None
    if not os.path.exists(video):
        raise PipelineError(f"動画がありません: {video}（make_video.py --from-script で再生成できます）")
    if dry_run:
        print(f"投稿予定: {os.path.basename(video)}\n---\n{caption}\n---")
        return None

    token = _token()
    uid = _user_id(token)

    # コンテナ作成 → ステータスポーリング → publish の3段階
    with PublicHost() as host:
        container = _call(f"{uid}/media", {
            "access_token": token,
            "media_type": "REELS",
            "video_url": host.url_for(video),
            "caption": caption,
        }, method="POST")["id"]
        _wait_container(container, token)  # IGが取得し終えるまでトンネルを維持する
    media = _call(f"{uid}/media_publish",
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
    paths = glob.glob(os.path.join(SCRIPTS_DIR, "*", "*.json"))
    return sorted(paths, key=lambda p: os.path.basename(p), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="縦動画を Instagram Reels に投稿する")
    parser.add_argument("script", nargs="?", help="台本JSON")
    parser.add_argument("--all", action="store_true", help="未投稿の台本をまとめて出す")
    parser.add_argument("--dry-run", action="store_true", help="投稿せず本文と動画の有無だけ確認する")
    args = parser.parse_args()

    if not args.script and not args.all:
        parser.error("台本JSONか --all を指定してください")

    ledger = load_ledger()
    targets = all_scripts() if args.all else [args.script]
    skipped = []
    for path in targets:
        if not os.path.exists(path):
            raise PipelineError(f"台本がありません: {path}")
        try:
            publish(path, args.dry_run, ledger)
        except PipelineError as e:
            # --all は1本の不備（動画なし等）で全体を止めない。理由を出して次へ
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
