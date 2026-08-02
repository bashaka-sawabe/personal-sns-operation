# テーマ一覧（廃止・アーカイブ）

> ⚠️ **手書きテーマ→LLM創作の方式は v5/v6 で廃止した**（LLMの0からの創作はつまらない。
> [docs/04](../docs/04_戦略.md) 2-2章）。このファイルは `--batch` の入力形式の記録として
> 残しているだけで、テーマを追記しても新方式では使われない。

現行のネタ供給ルート:

| ch | ルート |
|---|---|
| `meme` / `heisei` | `tools/fetch_threads.py`（おーぷん2ちゃんねる収集→目視選別） |
| `trivia` | [#103](https://github.com/bashaka-sawabe/personal-sns-operation/issues/103)（発見→一次ソース裏取り） |
| `f1` | [#104](https://github.com/bashaka-sawabe/personal-sns-operation/issues/104)（公式RSS検知＋Jolpica-F1） |

書式の記録: `## チャンネル` の見出しと `- テーマ` の箇条書き。行頭が `-` の行だけを拾う。
