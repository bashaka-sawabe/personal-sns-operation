---
name: issue-run
description: 着手できるIssueを優先度順に取って、ブランチ→実装→PR→マージ→クローズまでを最後まで回す。「Issueを消化して」「溜まってるやつやって」「ループで回して」と言われたときに使う。
---

# issue-run — Issueを取って終わらせる

**1周 = 1 Issue = 1 PR = 1 squashマージ。** 途中で止めない。
着手できるIssueが無くなるまで繰り返す。

## 0. 前提を1回だけ確認する

```bash
python3 tools/gh_board.py doctor
python3 tools/gh_board.py list --auto
```

`doctor` は次を見て、問題があれば**何をすればよいかを出して終了コード1で落ちる**。

| 検査 | 落ちると何が起きるか |
|---|---|
| 作業ツリーが汚れていないか | 無関係な変更が混ざったPRになる |
| `main` にいるか | 前の作業のブランチから枝分かれする |
| `main` に未pushコミットが無いか | **PRのベースが古くなり、squashマージが未pushのコミットを1つに潰す**（実際に起きた） |
| `main` が origin より遅れていないか | 無用なコンフリクト |

**落ちたら直してから始める。** 読み飛ばして進めない。

## 1周のループ

### 1. 次のIssueを取る

```bash
python3 tools/gh_board.py next --auto --json
```

`--auto` は `manual` / `decision` ラベルと、他Issue待ちを除外する。
**終了コード1（着手できるものが無い）でループを抜ける。**

```bash
gh issue view <n> --json title,body,labels --jq '.title, .body'
```

完了条件を読む。**ここに書かれていないことはやらない。** 気づいた別の問題は
新しいIssueにする（`issue-add`）。1つのPRに混ぜない。

### 2. 着手を宣言する

```bash
python3 tools/gh_board.py add <n> --status "In Progress"
git switch -c issue-<n>-<英数字のslug>
```

### 3. 実装する

- 既存コードの書き方に合わせる（stdlib優先、日本語コメント、`PipelineError` での失敗）
- `any` 型・lint抑制コメント・デバッグ用 `print` を残さない
- **完了条件を1つずつ潰す。** 全部埋まるまでコミットしない

### 4. 検証する

完了条件に書かれたコマンドを実際に流す。通らなければ直す。
**「たぶん動く」でPRを作らない。** 検証できなかった条件があるなら、
PR本文にその旨を書く（黙って飛ばさない）。

### 5. コミットしてPRを出す

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(scope): 何をしたか（日本語・現在形）

- 変更点を箇条書きで
- なぜそうしたかを1行添える

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin HEAD

gh pr create --fill --body "$(cat <<'EOF'
## 変更内容
（要点を3〜5行）

## 検証
（実際に流したコマンドと結果）

Closes #<n>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

`Closes #<n>` を**必ず入れる**。これがマージ時の自動クローズの唯一の仕掛け。

prefix は `feat` / `fix` / `docs` / `refactor` / `chore`。

### 6. マージして片付ける

```bash
gh pr merge --squash --delete-branch
git switch main && git pull --ff-only
gh issue view <n> --json state --jq .state        # CLOSED を確認する
python3 tools/gh_board.py add <n> --status Done
```

`Closes` が効かずIssueが開いたままなら、手で `gh issue close <n>` して**報告する**。
黙って閉じると、次の周で同じIssueをまた引く。

### 7. 次の周へ

1に戻る。

## 止まる条件

| 条件 | どうするか |
|---|---|
| `next` が終了コード1 | ループ終了。残りを一覧で報告する |
| 完了条件を満たせない | そのIssueをスキップし、**理由をIssueにコメント**して次へ |
| 検証が通らない | 直す。3回試して駄目ならスキップして報告 |
| Issueの内容が方針と矛盾する | 止めて確認する。勝手に解釈を変えない |
| 外部に影響する操作が必要 | 止めて確認する（下記） |

## 自動でやらないこと

| 操作 | 理由 |
|---|---|
| **SNSへの公開投稿** | 外部に出たものは取り消せない。`daily_run.py` の予約公開（毎日22時・23時・[#237](https://github.com/bashaka-sawabe/personal-sns-operation/issues/237)）は本人が承認済みだが、**Issue消化のついでに動画を出すのは不可**。既存動画の `--release` / `--reserve` も本人 |
| **チャンネル名・ハンドルの変更** | APIで不可。加えて14日2回の制限がある |
| **`main` への直接push** | 必ずPR経由。履歴とレビュー導線が消える |
| **`decision` ラベルのIssue** | 数字を見て人が決める |
| **`manual` ラベルのIssue** | 本人しかできない |
| **自動フォロー・いいね・DM・フォロワー購入** | 3PFすべてで規約違反（[docs/08](../../../docs/08_自動化.md) 2章） |

## 最後の報告

1周ごとに実況しない。**全周終わってからまとめて出す。**

- 閉じたIssue（番号・タイトル・PR番号）
- スキップしたIssue（番号・理由）
- 残っているIssue（人手待ち・依存待ちの別）
- 次に本人がやるべきこと
