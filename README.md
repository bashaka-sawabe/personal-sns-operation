# 個人SNS運用（personal-sns-operation）

ピアノ演奏コンテンツによる個人SNS運用の**戦略・企画・運用データを一元管理するリポジトリ**。調査（市場・競合・トレンド）→ フレームワーク分析 → 企画 → 週次カンバン運用、までをここで回す。

- 👉 まず読む: [docs/00_サマリー.md](./docs/00_サマリー.md)（結論と根拠の要約）
- 🗂 タスク管理: GitHub Project「個人SNS運用」（下記セットアップ参照）

## ドキュメント

| # | ドキュメント | 内容 |
|---|---|---|
| 00 | [サマリー](./docs/00_サマリー.md) | 結論・ポジショニング・直近アクション |
| 01 | [市場分析](./docs/01_市場分析.md) | PF統計・アルゴリズム・収益化・**権利ルール（重要）** |
| 02 | [競合分析](./docs/02_競合分析.md) | 競合マップ・勝ちパターン・空白地帯10仮説 |
| 03 | [トレンド調査](./docs/03_トレンド調査.md) | バズ曲・サカナクション動向・選曲プール |
| 04 | [戦略](./docs/04_戦略.md) | 3C・STP・SWOT・コンセプト3案・ペルソナ |
| 05 | [コンテンツ企画](./docs/05_コンテンツ企画.md) | 制作の型・企画30本・投稿前チェックリスト |
| 06 | [KPI・運用](./docs/06_KPI・運用.md) | KPIツリー・週次レビュー・カンバンのルール |
| 07 | [ロードマップ](./docs/07_ロードマップ.md) | Phase 0〜3と完了条件 |

## カンバン（GitHub Project）の運用

- レーン: **Backlog → Next（来週）→ Current(今週) → Previous（完了済み）**。イテレーション=1週間
- 週次レビュー（毎週日曜30分）で棚卸し: Current完了分→Previous、Next→Current、Backlogから補充
- **Currentは3枚まで**（週3〜7時間の稼働制約。積みすぎ防止）
- 企画・タスクはすべてIssue化。投稿後は実績数値をIssueコメントに残してClose（文脈をリポジトリに残す）
- 詳細ルール: [docs/06_KPI・運用.md](./docs/06_KPI・運用.md) 6章

## 初回セットアップ（Project・Issue一括作成）

ローカル（Mac）で以下を実行すると、GitHub Project「個人SNS運用」・フィールド（イテレーション/開始日/目標日）・ラベル・マイルストーン・初期Issue約20件が自動作成される:

```bash
python3 tools/setup_github_board.py
```

- PATは `GH_PAT` 環境変数、または `~/dev/.cowork-secrets/gh_token_personal.txt` から自動で読む（PAT はコミットしないこと）
- PATに `repo` と `project` 権限（classic）／ Issues・Projects の Read and write（fine-grained）が必要
- ※ この手順だけローカル実行なのは、Claude（Cowork）のクラウド環境からはGitHubのProjects API（GraphQL）に接続できないため。git push・ドキュメント整備はClaudeが直接行える

実行後、ブラウザで2ステップだけ手動設定（APIで作成できないため）:

1. Projectを開く → **New view → Board** → Column by を「イテレーション」に → 名前を「カンバン」に
2. **New view → Roadmap** → Date fields を「開始日 / 目標日」に → 名前を「ロードマップ」に

## データ運用

- `data/`: 週次の投稿実績CSV（`data/2026-W30.csv` の形式、テンプレは [data/template.csv](./data/template.csv)）
- 集計・可視化スクリプトは `tools/` に追加していく（企画B-8「ダッシュボード自作」と兼用）

## 更新ルール

- docsは生きた文書。**意思決定・前提変更は必ずコミットで残す**（会話ログは消えるがリポジトリは残る）
- 数値目標は実データ4週分が溜まった時点で見直し（[docs/06](./docs/06_KPI・運用.md) 2章）
- 包括契約（JASRAC/NexTone）の対象サービス一覧は四半期ごとに再確認
