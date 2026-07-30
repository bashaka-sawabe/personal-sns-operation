#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成した縦動画を YouTube Shorts に投稿する。

    # 初回のみ: ブラウザが開いて認可 → トークンが保存される
    .venv/bin/python tools/publish_youtube.py --auth

    # 1本投稿（既定は限定公開。中身を確認してから公開に切り替える運用）
    .venv/bin/python tools/publish_youtube.py content/out/money-001.mp4

    # 台本JSONからタイトル・説明・タグを自動で埋めて投稿
    .venv/bin/python tools/publish_youtube.py content/out/money-001.mp4 \
        --script content/scripts/money-001.json --privacy public

    # 生成済みを全部まとめて（未投稿のものだけ）
    .venv/bin/python tools/publish_youtube.py --all

    # 投稿せずに、何が上がって何が止まるかだけ見る
    .venv/bin/python tools/publish_youtube.py --all --dry-run

なぜYouTubeを入れるか（docs/09 6章）:
- Shortsのエンゲージメント率はTikTokの約2倍と報告されている
- YouTubeは検索エンジンでもあるため、IG/TikTokと違い**投稿の寿命が長い**。
  同じ1本が数ヶ月後も再生され続ける＝ストック資産になる
- 収益化条件は登録1,000人＋90日で1,000万Shorts再生。3PFで最も明確

準備（初回のみ・人がやる必要がある）: 手順は docs/09_パイプライン.md 3章を参照。

**最重要**: OAuth同意画面の公開ステータスを必ず「本番環境（In production）」にすること。
「テスト中」のままだと**リフレッシュトークンが7日で失効**し、自動投稿が静かに止まる。
症状は `invalid_grant: Token has been expired or revoked`。

クォータ: 1日10,000ユニット。動画1本のアップロードが1,600ユニットなので
**1日6本まで**。週5本運用なら十分に収まる。
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline import script as script_mod
from tools.pipeline.common import ASSETS_DIR, OUT_DIR, SCRIPTS_DIR, PipelineError, secret_path

# upload だけでは channels.list / channels.update ができないため youtube も要求する。
# スコープを増やしたら再認可が必要（get_service が不足を検知して自動で促す）。
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
CLIENT_SECRET_FILE = "youtube_client_secret.json"
TOKEN_FILE = "youtube_token.json"
CATEGORY_PEOPLE_AND_BLOGS = "22"
# 投稿済みの記録。--all で二重投稿しないために使う
LEDGER = os.path.join(OUT_DIR, ".published_youtube.json")


def _imports():
    """依存は投稿時だけ必要。未導入でも他のツールが壊れないよう遅延importする。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise PipelineError(
            "YouTube投稿には追加パッケージが必要です:\n"
            "  .venv/bin/pip install google-api-python-client google-auth-oauthlib"
        ) from None
    return Request, Credentials, InstalledAppFlow, build, HttpError, MediaFileUpload


def get_service(interactive: bool = False, force_reauth: bool = False):
    """認証済みの YouTube API クライアントを返す。"""
    Request, Credentials, InstalledAppFlow, build, _, _ = _imports()

    token_path = secret_path(TOKEN_FILE)
    creds = None
    if os.path.exists(token_path) and not force_reauth:
        # scopes を渡さないこと。渡すと「実際に付与されたスコープ」ではなく
        # 渡した値で上書きされ、has_scopes() が常に True になって検知が効かなくなる
        creds = Credentials.from_authorized_user_file(token_path)
        # スコープを追加した場合、古いトークンは権限不足のまま「有効」に見える。
        # ここで弾かないと実行時に分かりにくい403になる
        if not creds.has_scopes(SCOPES):
            if not interactive:
                raise PipelineError(
                    "権限が不足しています（チャンネル操作の権限が追加されました）。再認可してください:\n"
                    "  .venv/bin/python tools/publish_youtube.py --auth"
                )
            creds = None

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not interactive:
            raise PipelineError(
                "YouTubeの認可がまだです。先に実行してください:\n"
                "  .venv/bin/python tools/publish_youtube.py --auth"
            )
        client_secret = secret_path(CLIENT_SECRET_FILE)
        if not os.path.exists(client_secret):
            raise PipelineError(
                f"OAuthクライアントJSONがありません: {client_secret}\n"
                "Google Cloud Console で「デスクトップアプリ」のOAuthクライアントIDを作り、\n"
                "ダウンロードしたJSONをこのパスに置いてください（手順は本ファイル冒頭）。"
            )
        # prompt="consent" を付けないと、Googleが「許可済み」と判断して
        # 追加スコープの同意画面を出さず、権限不足のトークンが返ることがある
        creds = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES).run_local_server(
            port=0, access_type="offline", prompt="consent",
        )
        if not creds.has_scopes(SCOPES):
            missing = [s for s in SCOPES if s not in (creds.scopes or [])]
            raise PipelineError(
                "必要な権限が許可されませんでした。同意画面で全てのチェックを入れてください。\n"
                "不足: " + ", ".join(missing)
            )

    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.chmod(token_path, 0o600)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def read_credits(stem: str) -> str:
    """生成時に media.py が書き出したクレジット（VOICEVOXは表記が利用条件）。"""
    path = os.path.join(ASSETS_DIR, stem, "credits.txt")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    return ""


def build_metadata(video_path: str, script: dict | None) -> dict:
    """台本があればそこから、無ければファイル名からタイトル等を作る。"""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    if not script:
        return {"title": f"{stem} #Shorts", "description": read_credits(stem), "tags": []}

    # Shortsとして認識させるため #Shorts を必ず入れる。タイトルは100字上限
    title = f"{script.get('title', stem)} #Shorts"[:100]
    tags = [t.lstrip("#") for t in script.get("hashtags", [])][:15]
    description = "\n\n".join(filter(None, [
        script.get("caption", ""),
        " ".join(script.get("hashtags", [])),
        # 合成音声を使っている間は明示する。YouTubeは改変コンテンツの開示を求めている
        "※ナレーションに音声合成を使用しています。",
        read_credits(stem),
    ]))[:5000]
    return {"title": title, "description": description, "tags": tags}


def blocking_reason(script: dict | None) -> str | None:
    """投稿してはいけない台本かを判定する。問題なければ None。

    make_video.py の警告（warn_unfilled）は処理を止めないため、未記入のままでも
    レンダリングまで通ってしまう。外部に出る直前のここで機械的に止めないと、
    画面に目印が焼き込まれたまま公開される（docs/09 4章）。

    台本JSONが見つからない動画（旧世代のmp4など）は検査しようがないので対象外。
    """
    if not script:
        return None
    spots = script_mod.unfilled(script)
    if not spots:
        return None
    return (
        f"一次情報が未記入です（{len(spots)}箇所: {'、'.join(spots)}）。"
        f"台本の「{script_mod.PLACEHOLDER}」を実際の内容に置き換え、"
        "make_video.py --from-script で動画を作り直してから投稿してください。"
    )


def upload(video_path: str, script: dict | None, privacy: str, interactive: bool = False) -> str:
    _, _, _, _, HttpError, MediaFileUpload = _imports()
    if not os.path.exists(video_path):
        raise PipelineError(f"動画が見つかりません: {video_path}")
    # 呼び出し側の事前チェックを信用しない。投稿は取り消せないので最後にもう一度見る
    reason = blocking_reason(script)
    if reason:
        raise PipelineError(f"{os.path.basename(video_path)}: {reason}")

    meta = build_metadata(video_path, script)
    service = get_service(interactive=interactive)
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": CATEGORY_PEOPLE_AND_BLOGS,
        },
        # 「子ども向けではない」は毎回明示しないとYouTube側で保留になることがある
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
    )
    try:
        response = request.execute()
    except HttpError as e:
        detail = getattr(e, "reason", "") or str(e)
        if e.resp.status == 403 and "quota" in detail.lower():
            raise PipelineError(
                "1日のクォータ上限（10,000ユニット＝アップロード6本）に達しました。翌日に再実行してください。"
            ) from None
        raise PipelineError(f"アップロードに失敗しました: {detail}") from None
    return response["id"]


def _scope_error(e) -> PipelineError:
    """403の権限不足は原因が分かりにくいので、対処法つきのメッセージに置き換える。"""
    if getattr(e, "resp", None) is not None and e.resp.status == 403 and "scope" in str(e).lower():
        return PipelineError(
            "権限が不足しています。トークンを作り直してください:\n"
            "  .venv/bin/python tools/publish_youtube.py --auth\n"
            "（--auth は毎回まっさらな同意画面を出すので、そこで全ての権限を許可してください）"
        )
    return PipelineError(f"APIエラー: {getattr(e, 'reason', '') or e}")


def show_channel(interactive: bool = False) -> dict:
    """投稿先チャンネルの現状を表示する。投稿前に「どこに上がるのか」を確認するため。"""
    _, _, _, _, HttpError, _ = _imports()
    service = get_service(interactive=interactive)
    try:
        res = service.channels().list(part="snippet,statistics,brandingSettings", mine=True).execute()
    except HttpError as e:
        raise _scope_error(e) from None
    items = res.get("items", [])
    if not items:
        raise PipelineError(
            "このGoogleアカウントにYouTubeチャンネルがありません。\n"
            "youtube.com でチャンネルを作成してから再実行してください。"
        )
    ch = items[0]
    snippet, stats = ch["snippet"], ch.get("statistics", {})
    print(f"チャンネル名 : {snippet.get('title', '')}")
    print(f"ID           : {ch['id']}")
    print(f"ハンドル     : {snippet.get('customUrl', '(未設定)')}")
    print(f"登録者       : {stats.get('subscriberCount', '?')}")
    print(f"動画本数     : {stats.get('videoCount', '?')}")
    print(f"総再生       : {stats.get('viewCount', '?')}")
    print(f"作成日       : {snippet.get('publishedAt', '')[:10]}")
    desc = (snippet.get("description") or "").strip()
    print(f"説明         : {desc[:80] + '...' if len(desc) > 80 else desc or '(未設定)'}")
    return ch


def rename_channel(new_title: str | None, new_description: str | None = None,
                   interactive: bool = False) -> None:
    """チャンネル名・説明文を変更する。

    既存の動画・登録者がある場合は破壊的な操作になり得るので、
    呼ぶ前に必ず --channel で中身を確認すること。

    ハンドル（@xxx）はAPIから変更できない。YouTube Studio で手で変える必要がある。
    """
    _, _, _, _, HttpError, _ = _imports()
    service = get_service(interactive=interactive)
    try:
        res = service.channels().list(part="snippet,statistics", mine=True).execute()
    except HttpError as e:
        raise _scope_error(e) from None
    items = res.get("items", [])
    if not items:
        raise PipelineError("チャンネルが見つかりません。")
    ch = items[0]
    old = ch["snippet"].get("title", "")
    videos = int(ch.get("statistics", {}).get("videoCount", 0) or 0)
    subs = int(ch.get("statistics", {}).get("subscriberCount", 0) or 0)
    if videos or subs:
        print(f"注意: このチャンネルには動画{videos}本・登録者{subs}人があります。", file=sys.stderr)

    channel = {}
    if new_title:
        channel["title"] = new_title
    if new_description is not None:
        channel["description"] = new_description
    if not channel:
        raise PipelineError("変更内容がありません（--rename か --description を指定してください）。")

    try:
        service.channels().update(
            part="brandingSettings",
            body={"id": ch["id"], "brandingSettings": {"channel": channel}},
        ).execute()
    except HttpError as e:
        raise PipelineError(
            f"変更に失敗しました: {getattr(e, 'reason', '') or e}\n"
            "YouTubeは名前変更の頻度を制限しています（14日で2回まで）。\n"
            "APIで通らない場合は YouTube Studio → カスタマイズ → ブランディング から手で変更してください。"
        ) from None

    # APIは brandingSettings.channel.title を受け付けて200を返すが、実際には
    # 無視することがある（既知の挙動）。成功したと偽らないよう必ず読み直して検証する
    after = service.channels().list(part="snippet", mine=True).execute()["items"][0]
    now_title = after["snippet"].get("title", "")

    if new_description is not None:
        print("説明文: 更新しました")
    if new_title:
        if now_title == new_title:
            print(f"チャンネル名: 「{old}」 → 「{new_title}」")
        else:
            print(
                f"チャンネル名: 変更されませんでした（現在も「{now_title}」）。\n"
                "  YouTube Data API はチャンネル名の変更を受け付けても実際には反映しません。\n"
                "  YouTube Studio → カスタマイズ → 基本情報 から手で変更してください:\n"
                "  https://studio.youtube.com/channel/" + ch["id"] + "/editing/details",
                file=sys.stderr,
            )
    print("※ ハンドル（@xxx）もAPIからは変更できません。同じ画面から手で変更してください。")


def load_ledger() -> dict:
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(ledger: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def script_for(video_path: str) -> dict | None:
    stem = os.path.splitext(os.path.basename(video_path))[0]
    # v4はチャンネル別ディレクトリ（content/scripts/<ch>/）。直下は旧配置の互換
    candidates = [os.path.join(SCRIPTS_DIR, f"{stem}.json")] + sorted(
        glob.glob(os.path.join(SCRIPTS_DIR, "*", f"{stem}.json"))
    )
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return None


def main() -> None:
    p = argparse.ArgumentParser(description="縦動画を YouTube Shorts に投稿する")
    p.add_argument("video", nargs="?", help="投稿する動画ファイル")
    p.add_argument("--script", help="台本JSON（省略時は同名のものを自動で探す）")
    p.add_argument("--all", action="store_true", help="content/out/ の未投稿を全部投稿する")
    p.add_argument("--auth", action="store_true", help="認可を行う（初回・スコープ追加時）")
    p.add_argument("--channel", action="store_true", help="投稿先チャンネルの現状を表示する")
    p.add_argument("--rename", metavar="名前", help="チャンネル名を変更する")
    p.add_argument("--description", metavar="説明", help="チャンネルの説明文を変更する")
    p.add_argument("--description-file", metavar="パス", help="説明文をファイルから読んで変更する")
    p.add_argument("--privacy", default="unlisted",
                   choices=["private", "unlisted", "public"],
                   help="公開範囲（既定: unlisted＝限定公開）")
    p.add_argument("--dry-run", action="store_true",
                   help="投稿せず、対象と検査結果だけ表示する")
    args = p.parse_args()

    try:
        if args.auth:
            # 既存トークンは無視して必ず取り直す。権限不足のトークンが残っていると
            # 「認可したのに動かない」状態になり原因が分からなくなる
            get_service(interactive=True, force_reauth=True)
            print(f"認可が完了しました。トークン: {secret_path(TOKEN_FILE)}")
            print("投稿先の確認: .venv/bin/python tools/publish_youtube.py --channel")
            return

        if args.channel:
            show_channel(interactive=True)
            return

        description = args.description
        if args.description_file:
            with open(args.description_file, encoding="utf-8") as f:
                description = f.read().strip()
        if args.rename or description is not None:
            rename_channel(args.rename, description, interactive=True)
            return

        targets = []
        if args.all:
            ledger = load_ledger()
            targets = sorted(
                os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR)
                if f.endswith(".mp4") and f not in ledger
            ) if os.path.isdir(OUT_DIR) else []
            if not targets:
                print("未投稿の動画はありません。")
                return
        elif args.video:
            targets = [args.video]
        else:
            p.error("動画ファイルか --all を指定してください")

        ledger = load_ledger()
        skipped = []
        for path in targets:
            script = None
            if args.script:
                with open(args.script, encoding="utf-8") as f:
                    script = json.load(f)
            else:
                script = script_for(path)
            name = os.path.basename(path)
            # --dry-run が存在しない動画を「投稿予定」と言わないよう、ここでも見る
            if not os.path.exists(path):
                raise PipelineError(f"動画が見つかりません: {path}")

            reason = blocking_reason(script)
            if reason:
                # --all は1本の未記入で残りを道連れにしない。末尾にまとめて報告する
                if args.all:
                    skipped.append((name, reason))
                    continue
                raise PipelineError(f"{name}: {reason}")

            if args.dry_run:
                print(f"投稿予定: {name} → 「{build_metadata(path, script)['title']}」"
                      f" ({args.privacy})")
                continue

            print(f"投稿中: {name} ...")
            video_id = upload(path, script, args.privacy)
            ledger[name] = {"video_id": video_id, "privacy": args.privacy}
            save_ledger(ledger)
            print(f"  完了: https://youtube.com/shorts/{video_id}  ({args.privacy})")

        if skipped:
            # パイプに繋ぐとstdoutがまとめて後から出るため、明示的に吐き切ってから
            # まとめを出す。そうしないと「末尾の一覧」が先頭に現れる
            sys.stdout.flush()
            # 終了コードは0のまま。恒久的に止まる動画があるだけで全体が失敗扱いになると、
            # 残りが投稿できているのに毎回エラーとして扱うことになり運用が回らない
            print(f"\n投稿しなかった動画（{len(skipped)}本）:", file=sys.stderr)
            for name, reason in skipped:
                print(f"  - {name}: {reason}", file=sys.stderr)

    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
