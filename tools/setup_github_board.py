#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Project（Projects v2）＋初期Issueの一括セットアップスクリプト。

ローカル（Mac等、api.github.com に直接アクセスできる環境）で実行する:

    python3 tools/setup_github_board.py

- PAT: 環境変数 GH_PAT、無ければ ~/dev/.cowork-secrets/gh_token_personal.txt を読む
- 必要権限: classic PAT なら repo + project / fine-grained なら Issues・Projects の Read and write
- 作成物: Project「個人SNS運用」、フィールド（イテレーション・開始日・目標日）、
  ラベル、マイルストーン（Phase 0〜3）、初期Issue約20件（カンバン配置済み）
- 再実行ガード: 同名Projectが既にある場合は中断する
"""
import getpass
import json
import os
import sys
import urllib.request
import urllib.error

OWNER = "bashaka-sawabe"
REPO = "personal-sns-operation"
PROJECT_TITLE = "個人SNS運用"
API = "https://api.github.com"
DOCS = f"https://github.com/{OWNER}/{REPO}/blob/main/docs"


def token() -> str:
    t = os.environ.get("GH_PAT", "").strip()
    if t:
        return t
    p = os.path.expanduser("~/dev/.cowork-secrets/gh_token_personal.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    sys.exit("PATが見つかりません。GH_PAT環境変数を設定するか、~/dev/.cowork-secrets/gh_token_personal.txt を配置してください。")


TOKEN = token()
TOKEN_KIND = (
    "fine-grained" if TOKEN.startswith("github_pat_")
    else "classic" if TOKEN.startswith("ghp_")
    else "unknown"
)


def call(method: str, url: str, data=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail[:300]}") from None


def rest(method: str, path: str, data=None):
    return call(method, f"{API}{path}", data)


def gql(query: str, variables=None, allow_partial: bool = False):
    res = call("POST", f"{API}/graphql", {"query": query, "variables": variables or {}})
    if res.get("errors") and not (allow_partial and res.get("data")):
        raise RuntimeError(f"GraphQL error: {json.dumps(res['errors'], ensure_ascii=False)[:400]}")
    return res.get("data")


PERMISSION_HINT = {
    "fine-grained": (
        "fine-grained PATはProjects v2 API（GraphQL）に対応していません（GitHub公式仕様。\n"
        "classic PATのprojectスコープ、またはGitHub Appトークンのみ対応）。次のどちらかで実行してください:\n"
        "  A) classicトークンを作成: https://github.com/settings/tokens → Generate new token (classic)\n"
        "     → スコープ「repo」「project」にチェック → 生成\n"
        "     → GH_PAT=ghp_xxxx python3 tools/setup_github_board.py\n"
        "     （セットアップ後はこのclassicトークンをRevokeしてOK）\n"
        "  B) gh CLI利用者: gh auth refresh -s project,repo && GH_PAT=$(gh auth token) python3 tools/setup_github_board.py"
    ),
    "classic": (
        "このPAT（classic）に project スコープがありません。\n"
        "修正手順: https://github.com/settings/tokens → 該当トークン → 「project」(と「repo」)にチェック → 保存して再実行"
    ),
}
PERMISSION_HINT["unknown"] = (
    PERMISSION_HINT["fine-grained"] + "\n\n（classicトークン ghp_... でこのエラーが出た場合）\n" + PERMISSION_HINT["classic"]
)


def main():
    global TOKEN, TOKEN_KIND
    if TOKEN_KIND == "fine-grained":
        print("検出: 手元のPATは fine-grained です。Projects v2 APIはclassic PATのみ対応（GitHub公式仕様）。")
        print("classicトークンの作成: https://github.com/settings/tokens → Generate new token (classic)")
        print("  → スコープ「repo」「project」にチェック → Generate（セットアップ後にRevokeしてOK）")
        pasted = getpass.getpass("classicトークン(ghp_...)を貼り付けてEnter（空Enterで中断）: ").strip()
        if not pasted:
            sys.exit("中断しました。環境変数でも指定できます: GH_PAT=ghp_xxxx python3 tools/setup_github_board.py")
        if pasted.startswith("github_pat_"):
            sys.exit("貼り付けられたトークンもfine-grainedです。classic（ghp_...で始まる）を作成してください。")
        TOKEN = pasted
        TOKEN_KIND = "classic" if pasted.startswith(("ghp_", "gho_")) else "unknown"

    me = gql("query{viewer{login id}}")["viewer"]
    print(f"認証OK: {me['login']}（トークン種別: {TOKEN_KIND}）")

    # 権限プリフライト: Projects v2 に触れるかを先に確認
    try:
        gql("query{viewer{projectsV2(first:1){totalCount}}}")
    except RuntimeError as e:
        if "FORBIDDEN" in str(e) or "Resource not accessible" in str(e):
            sys.exit(f"\n中断: Projects APIへの権限がありません。\n\n{PERMISSION_HINT[TOKEN_KIND]}")
        raise

    # 再実行ガード（権限外のProjectが混ざっていても落ちないようにする）
    try:
        existing = gql(
            "query($l:String!){user(login:$l){projectsV2(first:50){nodes{title url}}}}",
            {"l": OWNER},
            allow_partial=True,
        )["user"]["projectsV2"]["nodes"] or []
    except RuntimeError as e:
        print(f"警告: 既存Project一覧を確認できませんでした（同名の重複作成に注意）: {str(e)[:150]}")
        existing = []
    for p in filter(None, existing):
        if p["title"] == PROJECT_TITLE:
            sys.exit(f"中断: 同名のProjectが既に存在します → {p['url']}\n（作り直す場合は既存Projectを削除してから再実行）")

    repo = gql(
        "query($o:String!,$r:String!){repository(owner:$o,name:$r){id}}",
        {"o": OWNER, "r": REPO},
    )["repository"]

    # Project作成 + リポジトリ紐付け
    proj = gql(
        "mutation($o:ID!,$t:String!){createProjectV2(input:{ownerId:$o,title:$t}){projectV2{id url number}}}",
        {"o": me["id"], "t": PROJECT_TITLE},
    )["createProjectV2"]["projectV2"]
    print(f"Project作成: {proj['url']}")
    gql(
        "mutation($p:ID!,$r:ID!){linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r}){repository{id}}}",
        {"p": proj["id"], "r": repo["id"]},
    )

    # Statusフィールド（デフォルト）のオプションID取得
    status = gql(
        """query($p:ID!){node(id:$p){... on ProjectV2{
             field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}}}""",
        {"p": proj["id"]},
    )["node"]["field"]
    status_opt = {o["name"]: o["id"] for o in status["options"]}

    # イテレーション（単一選択）フィールド作成
    itr = gql(
        """mutation($p:ID!){createProjectV2Field(input:{
             projectId:$p,dataType:SINGLE_SELECT,name:"イテレーション",
             singleSelectOptions:[
               {name:"Current",color:GREEN,description:"今週やる（3枚まで）"},
               {name:"Next",color:BLUE,description:"来週やる"},
               {name:"Backlog",color:GRAY,description:"いつかやる"},
               {name:"Previous",color:PURPLE,description:"完了済み（先週以前）"}]}){
             projectV2Field{... on ProjectV2SingleSelectField{id options{id name}}}}}""",
        {"p": proj["id"]},
    )["createProjectV2Field"]["projectV2Field"]
    itr_opt = {o["name"]: o["id"] for o in itr["options"]}

    date_fields = {}
    for name in ("開始日", "目標日"):
        f = gql(
            "mutation($p:ID!,$n:String!){createProjectV2Field(input:{projectId:$p,dataType:DATE,name:$n})"
            "{projectV2Field{... on ProjectV2Field{id}}}}",
            {"p": proj["id"], "n": name},
        )["createProjectV2Field"]["projectV2Field"]
        date_fields[name] = f["id"]
    print("フィールド作成: イテレーション / 開始日 / 目標日")

    # ラベル
    labels = [
        ("pillar:sakanaction", "1d76db", "ピラーA: サカナクション研究室"),
        ("pillar:datalab", "0e8a16", "ピラーB: ピアノ×データラボ"),
        ("pillar:classic", "5319e7", "ピラーC: クラシック翻訳機"),
        ("content", "d93f0b", "動画の制作・投稿"),
        ("ops", "fbca04", "運用・環境・レビュー"),
        ("research", "c5def5", "調査・分析"),
    ]
    for name, color, desc in labels:
        try:
            rest("POST", f"/repos/{OWNER}/{REPO}/labels", {"name": name, "color": color, "description": desc})
        except RuntimeError as e:
            if "422" not in str(e):
                raise
    print(f"ラベル: {len(labels)}件")

    # マイルストーン
    ms_defs = [
        ("Phase 0: 準備", "2026-08-09", "環境・アカウント・権利フロー・初投稿"),
        ("Phase 1: 検証", "2026-10-31", "3ピラーABテスト＋サカナクション月間"),
        ("Phase 2: 集中", "2027-01-31", "勝ちピラーへ70%配分"),
        ("Phase 3: 拡張", "2027-04-30", "コラボ・収益化・海外の設計"),
    ]
    existing_ms = {m["title"]: m["number"] for m in rest("GET", f"/repos/{OWNER}/{REPO}/milestones?state=all")}
    ms = {}
    for title, due, desc in ms_defs:
        if title in existing_ms:
            ms[title] = existing_ms[title]
        else:
            m = rest("POST", f"/repos/{OWNER}/{REPO}/milestones",
                     {"title": title, "due_on": f"{due}T23:59:59Z", "description": desc})
            ms[title] = m["number"]
    print(f"マイルストーン: {len(ms)}件")

    # 初期Issue定義: (title, body, labels, milestone, iteration, status, start, target, close)
    P0, P1, P2 = "Phase 0: 準備", "Phase 1: 検証", "Phase 2: 集中"
    issues = [
        ("[調査] 市場・競合・トレンド調査と戦略ドキュメント整備",
         f"完了済み。成果物:\n- [00 サマリー]({DOCS}/00_サマリー.md) 〜 [07 ロードマップ]({DOCS}/07_ロードマップ.md)\n\n次回更新: 仮置き前提（ゴール/顔出し/稼働）の確定時、および実データ4週分が溜まった時点。",
         ["research"], P0, "Previous", "Done", "2026-07-18", "2026-07-18", True),
        ("[準備] 撮影・録音環境を確定する",
         f"スマホ直録りと外部マイクを比較して初期構成を決める（企画B-10の素材にもなる）。\n\n- [ ] 画角（手元）とライティング確認\n- [ ] 音質AB（スマホ vs マイク）\n- [ ] 30秒テスト動画を1本書き出し\n\n参考: [05 コンテンツ企画]({DOCS}/05_コンテンツ企画.md)",
         ["ops"], P0, "Current", "Todo", "2026-07-20", "2026-07-26", False),
        ("[準備] アカウント3面を開設・プロフィール設計（YT/TikTok/IG）＋X整備",
         f"- [ ] 統一ハンドル決定\n- [ ] プロフィール文（ポジショニング一文: [04 戦略]({DOCS}/04_戦略.md) 2章）\n- [ ] アイコン・ヘッダー\n- [ ] Xは告知・データ発信専用と明記（演奏動画は上げない: 権利ルール）",
         ["ops"], P0, "Current", "Todo", "2026-07-20", "2026-07-26", False),
        ("[準備] 権利チェックフローを確立（J-WID/NexTone確認テンプレ）",
         f"[01 市場分析]({DOCS}/01_市場分析.md) 4章のフローを実際に3曲（夜の踊り子・怪獣・ラ・カンパネラ）で回してテンプレ化する。\n\n- [ ] J-WID検索手順の確認\n- [ ] NexTone検索手順の確認\n- [ ] 投稿前チェックリスト（[05]({DOCS}/05_コンテンツ企画.md) 5章）を運用に組込",
         ["ops"], P0, "Current", "Todo", "2026-07-20", "2026-07-26", False),
        ("[制作] A-1「夜の踊り子」リフカバーを収録・投稿（初投稿）",
         f"**最優先。ミーム進行中のため7月中に投稿する。**\n\n- [ ] 原曲キー・テンポでリフ耳コピ\n- [ ] ループ設計（終わり→冒頭が繋がる15〜30秒）\n- [ ] 3面書き出し（YT Shorts/TikTok/Reels・Reelsは透かしなし）\n- [ ] 投稿時間帯ルール（[01]({DOCS}/01_市場分析.md) 2-4）\n- [ ] 48時間後に初速を記録\n\n根拠: [03 トレンド調査]({DOCS}/03_トレンド調査.md) 3-2章",
         ["content", "pillar:sakanaction"], P0, "Current", "Todo", "2026-07-21", "2026-07-31", False),
        ("[準備] 計測シートの運用開始（data/週次CSV）",
         f"[data/template.csv](https://github.com/{OWNER}/{REPO}/blob/main/data/template.csv) を複製して `data/2026-W30.csv` から記録開始。\n\n- [ ] 各PFのアナリティクス画面の場所を確認\n- [ ] 週次レビュー（毎週日曜30分）をカレンダーに登録",
         ["ops"], P0, "Current", "Todo", "2026-07-20", "2026-07-26", False),
        ("[制作] A-2「夜の踊り子はなぜ中毒なのか」60秒解説",
         f"A-1とセット運用。反復×シンコペーション×ミニマルの構造を弾いて示す（型②）。\n\n参考: [05 コンテンツ企画]({DOCS}/05_コンテンツ企画.md) ピラーA",
         ["content", "pillar:sakanaction"], P1, "Next", "Todo", "2026-07-27", "2026-08-02", False),
        ("[制作] C-4「ラ・カンパネラがストピでバズる理由」60秒",
         "クラシック翻訳機ピラーの1本目。STPIAランキング6位の実績ある定番曲×解説（型②）。",
         ["content", "pillar:classic"], P1, "Next", "Todo", "2026-07-27", "2026-08-02", False),
        ("[制作] B-7 フォロワー0→1,000全公開企画の設計・開始",
         "データラボピラーの連載軸。週次でX投稿＋月次でショート化。初回は「戦略と現在地」。",
         ["content", "pillar:datalab"], P1, "Next", "Todo", "2026-07-27", "2026-08-02", False),
        ("[運用] 週次レビュー1回目（数値記録・カンバン棚卸し）",
         f"アジェンダ: [06 KPI・運用]({DOCS}/06_KPI・運用.md) 4章。結果はこのIssueにコメントで残す。",
         ["ops"], P1, "Next", "Todo", "2026-08-02", "2026-08-02", False),
        ("[企画] サカナクション月間（8/15〜9/30）の詳細計画",
         f"サマソニ（8月）→ シングル『怪獣/いらない』（9/2）→ SAKANAQUARIUM 2026（9/8〜10/4）の時流に乗せる。A-3〜A-10の投稿順・収録計画を確定。\n\n根拠: [03 トレンド調査]({DOCS}/03_トレンド調査.md) 3章",
         ["pillar:sakanaction", "research"], P1, "Backlog", "Todo", "2026-08-04", "2026-08-14", False),
        ("[制作] A-6「いらない Piano ver.」研究（シングル発売週に投稿）",
         "9/2発売シングルに公式Piano ver.収録（報道）。公式ピアノ版と自分アレンジの弾き比べ企画。発売週に照準。",
         ["content", "pillar:sakanaction"], P1, "Backlog", "Todo", "2026-08-31", "2026-09-06", False),
        ("[制作] A-7 SAKANAQUARIUM 2026 セトリ予習メドレー",
         "ツアー開幕（9/8）直前に投稿。ライブ遠征層（ペルソナP1）の保存需要を狙う。",
         ["content", "pillar:sakanaction"], P1, "Backlog", "Todo", "2026-09-01", "2026-09-08", False),
        ("[制作] A-5 新宝島 難易度Lv.1→10",
         "公認ミーム×Level型フォーマット。1本で初心者〜ガチ勢の両方にリーチ。",
         ["content", "pillar:sakanaction"], P1, "Backlog", "Todo", None, None, False),
        ("[制作] B-1 電子ピアノ3台弾き比べ（価格帯別）",
         "ピアノ界に機材検証文化が無い空白を突く（02の空白9）。購入検討層の検索需要。",
         ["content", "pillar:datalab"], P1, "Backlog", "Todo", None, None, False),
        ("[制作] C-1 もしショパンが「好きすぎて滅！」を弾いたら",
         "2026上半期TikTok最強曲×ノクターン様式のギャップ企画。",
         ["content", "pillar:classic"], P1, "Backlog", "Todo", None, None, False),
        ("[制作] B-4「30日でIRIS OUT」連載設計",
         "高難度曲×成長ドキュメンタリー（毎日15秒）。C-5（リスト様式）との連動も検討。",
         ["content", "pillar:datalab"], P1, "Backlog", "Todo", None, None, False),
        ("[運用] 初月データまとめ→ピラー配分見直し",
         f"8月末。ピラー別の保存率・完走率を算出して9月の配分を決める。数値目標の再設定もここで（[06]({DOCS}/06_KPI・運用.md) 2章）。",
         ["ops", "research"], P1, "Backlog", "Todo", "2026-08-29", "2026-08-31", False),
        ("[判断] Phase 1判定: 勝ちピラー決定・顔出し再検討",
         f"10月末。判定基準: [07 ロードマップ]({DOCS}/07_ロードマップ.md) Phase 1完了条件。",
         ["ops"], P1, "Backlog", "Todo", "2026-10-26", "2026-10-31", False),
        ("[運用] 包括契約一覧の四半期再確認（JASRAC/NexTone）",
         "10月頭。対象サービス一覧の変更を確認し、[01 市場分析]の4章を必要に応じて更新。",
         ["ops"], P1, "Backlog", "Todo", "2026-10-01", "2026-10-07", False),
    ]

    created = []
    for title, body, lbls, mstone, itr_name, st, start, target, close in issues:
        iss = rest("POST", f"/repos/{OWNER}/{REPO}/issues",
                   {"title": title, "body": body, "labels": lbls, "milestone": ms[mstone]})
        item = gql(
            "mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}",
            {"p": proj["id"], "c": iss["node_id"]},
        )["addProjectV2ItemById"]["item"]

        def set_field(field_id, value):
            gql(
                "mutation($p:ID!,$i:ID!,$f:ID!,$v:ProjectV2FieldValue!){updateProjectV2ItemFieldValue("
                "input:{projectId:$p,itemId:$i,fieldId:$f,value:$v}){projectV2Item{id}}}",
                {"p": proj["id"], "i": item["id"], "f": field_id, "v": value},
            )

        set_field(itr["id"], {"singleSelectOptionId": itr_opt[itr_name]})
        if st in status_opt:
            set_field(status["id"], {"singleSelectOptionId": status_opt[st]})
        if start:
            set_field(date_fields["開始日"], {"date": start})
        if target:
            set_field(date_fields["目標日"], {"date": target})
        if close:
            rest("PATCH", f"/repos/{OWNER}/{REPO}/issues/{iss['number']}",
                 {"state": "closed", "state_reason": "completed"})
        created.append(iss["number"])
        print(f"  Issue #{iss['number']}: {title[:40]}")

    # ビュー自動作成を試行（APIが未対応なら手動手順を案内）
    manual_views = True
    try:
        for name, layout in (("カンバン", "BOARD_LAYOUT"), ("ロードマップ", "ROADMAP_LAYOUT")):
            gql(
                "mutation($p:ID!,$n:String!,$l:ProjectV2ViewLayout!){createProjectV2View("
                "input:{projectId:$p,name:$n,layout:$l}){projectV2View{id}}}",
                {"p": proj["id"], "n": name, "l": layout},
            )
        manual_views = False
        print("ビュー作成: カンバン / ロードマップ")
    except Exception:
        pass

    print("\n===== 完了 =====")
    print(f"Project: {proj['url']}")
    print(f"Issues: {len(created)}件（#{created[0]}〜#{created[-1]}）")
    if manual_views:
        print("""
残り2ステップ（GitHubのAPIがビュー作成に未対応のため手動で）:
 1. Project → New view → Board → 「Column by」を イテレーション に → 名前「カンバン」
 2. Project → New view → Roadmap → 「Date fields」を 開始日/目標日 に → 名前「ロードマップ」""")


if __name__ == "__main__":
    main()
