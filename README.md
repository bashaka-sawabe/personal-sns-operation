# 個人SNS運用（personal-sns-operation）

情報系ショート（お金・人間関係・世の中のしくみ）による個人SNS運用の
**戦略・企画・自動化・運用データを一元管理するリポジトリ**。
調査 → 戦略 → 企画 → 動画生成 → 投稿 → 計測 → 判定、までをここで回す。

> **コンセプト: 「大人のメモ帳」— 知っておくと少しだけ得することを、1本30秒でメモしていく。**
> 映像はAIで生成し、**ジャンルは決め打ちせず3つを同じ型で回して数字で決める**。

- 👉 まず読む: [docs/00_サマリー.md](./docs/00_サマリー.md)
- 🗂 タスク: [GitHub Project「個人SNS運用」](https://github.com/users/bashaka-sawabe/projects/4) ／ `python3 tools/gh_board.py list`
- 🤖 進め方: [CLAUDE.md](./CLAUDE.md)

## いま何ができるか

```bash
# 1本作る
.venv/bin/python tools/make_video.py --genre money --theme "経費で落ちるのに9割が申告してないもの"

# APIキー無しで疎通確認（テンプレ台本＋ローカル素材で最後まで通る）
.venv/bin/python tools/make_video.py --genre money --theme "テスト" --offline

# 2週間テストの本体。3ジャンル12本をまとめて生成する（約14分）
.venv/bin/python tools/make_video.py --batch data/themes.md

# YouTube Shortsへ投稿（既定は限定公開）
.venv/bin/python tools/publish_youtube.py --all
```

企画1行 → 台本 → 素材 → 縦動画（1080×1920 / 30fps）まで**1本あたり約68秒**。
詳細は [docs/09_パイプライン.md](./docs/09_パイプライン.md)。

## ドキュメント

| # | ドキュメント | 内容 |
|---|---|---|
| 00 | [サマリー](./docs/00_サマリー.md) | 結論・コンセプト・いまの状態 |
| 01 | [市場分析](./docs/01_市場分析.md) | PF統計・アルゴリズム・**2026年のオリジナリティ規制** |
| 02 | [競合分析](./docs/02_競合分析.md) | 発信者の4類型・ポジショニング・空白 |
| 03 | [トレンド調査](./docs/03_トレンド調査.md) | ショートの作法の変化・AI規制の流れ・テーマの旬 |
| 04 | [戦略](./docs/04_戦略.md) | 3C・STP・SWOT・ジャンル設計・PF戦略 |
| 05 | [コンテンツ企画](./docs/05_コンテンツ企画.md) | 台本の型・12本のテーマ・**投稿前チェックリスト** |
| 06 | [KPI・運用](./docs/06_KPI・運用.md) | 判定指標・週次レビュー・Issue運用 |
| 07 | [ロードマップ](./docs/07_ロードマップ.md) | Phase 0〜3・**顔出しの条件** |
| 08 | [自動化](./docs/08_自動化.md) | 自動化の線引き・**禁止事項**・PF別可否 |
| 09 | [パイプライン](./docs/09_パイプライン.md) | 動画生成・投稿の実装 |

## Issue運用

やることは**全てIssueにする**。

```bash
python3 tools/gh_board.py doctor        # 着手前の前提チェック
python3 tools/gh_board.py list          # 盤の全体像（🔒=依存待ち / 👤=人手待ち）
python3 tools/gh_board.py next --auto   # 次に着手すべきIssue
```

優先度は `P0`〜`P3`、順番は**GitHub公式の依存関係API（Blocked by）**で定義する。
`main` に直接pushしない。`issue-<番号>-<slug>` → PR（`Closes #N`）→ squashマージ。

詳細は [CLAUDE.md](./CLAUDE.md) と [docs/06_KPI・運用.md](./docs/06_KPI・運用.md) 6章。

## データ運用

- `data/themes.md` — テーマ一覧（3ジャンル）。`--batch` の入力
- `data/YYYY-Www.csv` — 週次の投稿実績（テンプレは [data/template.csv](./data/template.csv)）
- `content/scripts/` — 台本JSON（**git追跡する**。手で直して作り直せる）
- `content/assets/` `content/out/` — 中間物と完成動画（**git除外**）

### 計測

```bash
.venv/bin/python tools/fetch_metrics.py              # インサイト取得 → 週次CSV
.venv/bin/python tools/weekly_report.py --issue 10   # レポートを週次レビューIssueへ
.venv/bin/python tools/fetch_metrics.py --refresh-token  # 長期トークンを延長（月1回）
```

トークンは `~/repo/.cowork-secrets/` に置く（**コミットしないこと**）。
セットアップ手順は [docs/08_自動化.md](./docs/08_自動化.md) 4章。

## 絶対にやらないこと

自動フォロー・自動いいね・自動DM・フォロワー購入は3PFすべてで規約違反。
**投稿直後30〜60分のコメント返信は手動**（アルゴリズム上も有利）。
**合成音声を使う間はTikTokでAI生成ラベルを付ける**（隠しても自動検知される）。

## 更新ルール

- docsは生きた文書。**意思決定・前提変更は必ずコミットで残す**（会話は消えるがリポジトリは残る）
- 方針が変わったら、コードより先に [docs/04_戦略.md](./docs/04_戦略.md) を直す
- 絶対値の数値目標は実データ4週分が溜まった時点で設定（[docs/06](./docs/06_KPI・運用.md) 2章）
- **PFのAIポリシーは動きが速い。** 変更があったら [docs/01](./docs/01_市場分析.md) 4章を更新する

## 更新履歴

- **2026-07-27 v3**: 情報系ショート（お金・人間関係・雑学）× AI生成パイプラインへ全面ピボット。
  ジャンルを決め打ちせず2週間テストで決める設計に変更。Issue駆動の運用ハーネスを整備
- 2026-07-18 v2: 「サカナクション愛×トーク×ピアノ＋AI分析」へピボット（v3で破棄）
- 2026-07-18 v1: 初版（調査・戦略・企画・ボード構築）
