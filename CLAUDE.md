# このリポジトリの進め方

個人SNS運用の戦略・企画・自動化を一箇所で管理する。**やることは全てIssueにする。**

## Issue駆動

```bash
python3 tools/gh_board.py list          # 盤の全体像（🔒=依存待ち / 👤=人手待ち）
python3 tools/gh_board.py next --auto   # 次に着手すべきIssue
```

| やりたいこと | 使うもの |
|---|---|
| やることをIssueにする | `/issue-add`（`.claude/skills/issue-add/`） |
| Issueを消化する | `/issue-run`（`.claude/skills/issue-run/`） |

Issueは GitHub Project「個人SNS運用」（#4）に載せ、`Priority`（P0〜P3）と
**GitHub公式の依存関係API**で `Blocked by` を張る。本文に「Blocked by #12」と
書くだけではブロック判定に使えない。

`decision`（数字を見て人が決める）と `manual`（本人しかできない）は自動実行しない。

## 変更の流れ

`main` に直接pushしない。`issue-<番号>-<slug>` ブランチ → PR（`Closes #N`）→ squashマージ。

## ドキュメント

方針が変わったら、コードより先に `docs/` を直す。ここが古いと以降の判断が全部ずれる。

| | |
|---|---|
| [00 サマリー](docs/00_サマリー.md) | 現在の方針。**まずここ** |
| [04 戦略](docs/04_戦略.md) | ポジショニングと勝ち筋 |
| [06 KPI・運用](docs/06_KPI・運用.md) | 何の数字で判断するか |
| [07 ロードマップ](docs/07_ロードマップ.md) | フェーズと顔出しの条件 |
| [08 自動化](docs/08_自動化.md) | 自動化の線引きと**禁止事項** |
| [09 パイプライン](docs/09_パイプライン.md) | 動画生成・投稿の実装 |

## コード

- stdlib優先。外部依存は理由があるときだけ増やす
- コメント・docstringは日本語。**何をしたかではなく、なぜそうしたかを書く**
- 失敗は `PipelineError` で投げ、CLIの入口で日本語メッセージにして落とす
- `any` 型・lint抑制コメント・デバッグ用 `print` を残さない
- Python は `.venv/bin/python` で動かす（macOSはPEP 668でシステムPythonに入らない）

## シークレット

`~/repo/.cowork-secrets/` に置く。**コミットしない。**
コードは複数の候補ディレクトリを順に探す（置き場が動いた実績があるため）。

## 絶対にやらないこと

自動フォロー・自動いいね・自動DM・フォロワー購入は3PFすべてで規約違反。
投稿直後30〜60分のコメント返信は手動でやる（アルゴリズム上もそのほうが有利）。
