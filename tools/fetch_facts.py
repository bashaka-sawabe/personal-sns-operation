#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事実ベースのネタ収集と裏取り管理（heisei / showa。docs/04 2-2章・docs/05 3章）。

    # 自分で見つけたネタを積む（既定は heisei。showa は --channel showa）
    .venv/bin/python tools/fetch_facts.py --add "たまごっちの発売は1996年" --from "https://..."
    .venv/bin/python tools/fetch_facts.py --add "土光敏夫はメザシで夕食" --channel showa

    # Claude+web検索でネタを発見し、一次ソース付き候補として自動登録する（#196）
    .venv/bin/python tools/fetch_facts.py --discover --channel heisei --limit 5

    # 裏取り（一次ソース）を付ける → 付いて初めて採用できる
    .venv/bin/python tools/fetch_facts.py --back til-abc123 --url "https://www.maff.go.jp/..." --note "農水省のQ&A"
    .venv/bin/python tools/fetch_facts.py --adopt til-abc123

    # 一覧・不採用
    .venv/bin/python tools/fetch_facts.py --list
    .venv/bin/python tools/fetch_facts.py --reject til-abc123

**裏取り（backing_url）の無いネタは採用できない。** 雑学は間違えた瞬間に
コメント欄で刺されて信用が飛ぶ。発見ルート（Reddit・スレ・伝聞）は仮説にすぎず、
一次ソース（官公庁・政府統計・学術機関 > 信頼できる一次報道 > Wikipedia経由で原典）で
確認できたものだけが台本になる。
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError, read_secret

FACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "facts")

def _path(fact_id: str) -> str:
    return os.path.join(FACTS_DIR, f"{fact_id}.json")


def _save(data: dict) -> str:
    os.makedirs(FACTS_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(fact_id: str) -> dict:
    path = _path(fact_id)
    if not os.path.exists(path):
        raise PipelineError(f"候補がありません: {fact_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_facts() -> list:
    rows = []
    for name in sorted(os.listdir(FACTS_DIR)) if os.path.isdir(FACTS_DIR) else []:
        if name.endswith(".json"):
            with open(os.path.join(FACTS_DIR, name), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def load_adopted(fact_id: str) -> dict:
    """採用済みネタを1本返す。採用時に裏取りを検査済みだが、後から手で消される事故も防ぐ。"""
    data = _load(fact_id)
    if data.get("status") != "adopted":
        raise PipelineError(
            f"{fact_id} は採用されていません（現在: {data.get('status')}）。\n"
            "  --back で一次ソースを付けてから --adopt してください。"
        )
    if not (data.get("backing_url") or "").strip():
        raise PipelineError(f"{fact_id} に裏取り（backing_url）がありません。--back で付けてください。")
    return data


def _new_fact(fact_id: str, fact: str, discovered_from: str,
              channel: str) -> dict:
    return {
        "id": fact_id,
        "channel": channel,                   # heisei / showa。ネタの置き場を分ける
        "fact": fact,
        "discovered_from": discovered_from,   # 発見ルート（裏取りには使えない）
        "backing_url": "",                    # 一次ソース。空のままでは採用できない
        "backing_note": "",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "candidate",
    }


# チャンネル別の発見方針。裏取りの優先順位は docs/05 3章の正本に合わせる
_DISCOVER_BRIEF = {
    "heisei": (
        "平成時代（1989〜2019年）の懐かしい事実。製品・文化・出来事の"
        "「あったあった！」と語りたくなるネタ（発売日・販売数・終了年などの具体的な数字つき）。"
        "視聴者は平成小学生世代（30〜40代）。"
    ),
    "showa": (
        "昭和の経営者・ビジネスマンの実話・逸話。熱血・無茶・義理人情で"
        "「実話なのにこれ」と驚ける具体的なエピソード（人物名・年・出来事を特定できるもの）。"
        "誹謗中傷にならない、本人の名誉を損なわないものだけ。"
    ),
}


def discover(channel: str, limit: int) -> list[str]:
    """Claude+web検索でネタを発見し、一次ソース付き候補として登録する（#196）。

    裏取り（backing_url）の無いネタは登録しない。裏取り必須の線は
    発見の自動化とは別の品質保証なので、ここでも緩めない（docs/08 1章）。
    戻り値は保存したファイルパスの一覧。
    """
    if channel not in _DISCOVER_BRIEF:
        raise PipelineError(f"--discover は heisei / showa 専用です（指定: {channel}）")
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if not api_key:
        raise PipelineError("ANTHROPIC_API_KEY が無いため自動補充できません")
    try:
        import anthropic
    except ImportError:
        raise PipelineError("anthropic SDK がありません（.venv/bin/pip install anthropic）") from None

    # used / rejected を含む全既存ネタを渡して重複登録を防ぐ
    existing = [f["fact"] for f in saved_facts()]
    system = (
        f"あなたは日本のショート動画チャンネルのネタ収集係。次のネタを{limit}本発見する:\n"
        f"{_DISCOVER_BRIEF[channel]}\n\n"
        "ルール:\n"
        "- web検索で事実を確認し、**一次ソースのURLを backing_url に必ず入れる**。"
        "優先順位: 官公庁・政府統計・学術機関 > 信頼できる一次報道 > 企業公式 > "
        "Wikipedia経由で原典にたどる（Wikipedia自体をソースにしない）\n"
        "- 一次ソースが確認できないネタは**出力しない**（本数が減ってよい）\n"
        "- fact は事実を一文で。数字・固有名詞・年を含める\n"
        "- backing_note にはソースが何か（例: 農水省Q&A）を一言で\n"
        "- discovered_from には発見のきっかけになったURLを入れる\n"
        "- 医療・投資・税務の助言に踏み込む事実は避ける\n"
        "- 以下の既存ネタと重複・類似するものは出さない:\n"
        + "\n".join(f"  - {t[:60]}" for t in existing[-80:])
    )
    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "backing_url": {"type": "string"},
                        "backing_note": {"type": "string"},
                        "discovered_from": {"type": "string"},
                    },
                    "required": ["fact", "backing_url", "backing_note", "discovered_from"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["facts"],
        "additionalProperties": False,
    }

    client = anthropic.Anthropic(api_key=api_key)
    tools = [{"type": "web_search_20260209", "name": "web_search",
              "max_uses": max(3, limit * 3)}]

    def _run() -> object:
        # 1段目: web検索で発見・裏取りし、本文にメモとして書かせる。
        # web検索と json_schema の併用は overloaded_error で確実に落ちる（実測 2026-08-08）
        # ため、構造化は2段目のツールなしターンに分離する
        messages = [{"role": "user", "content": (
            f"{channel} のネタを{limit}本、裏取り付きで発見してください。"
            "各ネタごとに fact（一文）・一次ソースURL・ソース種別・発見元URLを本文に明記すること。"
        )}]
        # web検索はサーバー側ループで動く。上限到達時は pause_turn で返るので送り直して続きを回す
        for _ in range(3):
            with client.messages.stream(
                model="claude-opus-5",
                max_tokens=8000,
                system=system,
                tools=tools,
                messages=messages,
            ) as stream:
                response = stream.get_final_message()
            if response.stop_reason != "pause_turn":
                break
            messages = messages[:1] + [{"role": "assistant", "content": response.content}]
        if response.stop_reason == "refusal":
            return response
        notes = "\n".join(b.text for b in response.content if b.type == "text")
        if not notes.strip():
            raise PipelineError(f"{channel} のネタ発見で調査メモが得られませんでした")
        # 2段目: 調査メモからスキーマどおりに抽出（ツールなしなので json_schema が使える）
        with client.messages.stream(
            model="claude-opus-5",
            max_tokens=4000,
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": (
                "次の調査メモから、一次ソースURLが明記されているネタだけを抽出して"
                "スキーマどおりに出力してください。URLの無いネタは含めない。\n\n" + notes
            )}],
        ) as stream:
            return stream.get_final_message()

    # 過負荷（529）等の一時エラーはストリーム途中でも起きる。少し待って取り直す
    last_err = None
    response = None
    for attempt in range(3):
        if attempt:
            time.sleep(20 * attempt)
        try:
            response = _run()
            break
        except anthropic.APIStatusError as e:
            last_err = e
    if response is None:
        raise PipelineError(f"ネタ発見のAPI呼び出しに失敗しました（3回試行）: {last_err}")
    if response.stop_reason == "refusal":
        raise PipelineError(f"{channel} のネタ発見を拒否されました（発見方針を見直してください）")

    # 検索ブロックが混ざるため、最後の text ブロックが構造化出力
    text = next((b.text for b in reversed(response.content) if b.type == "text"), "")
    try:
        found = json.loads(text)["facts"]
    except (json.JSONDecodeError, KeyError) as e:
        raise PipelineError(f"ネタ発見の出力を解釈できませんでした: {e}") from None

    known_texts = set(existing)
    saved = []
    for f in found:
        fact = (f.get("fact") or "").strip()
        url = (f.get("backing_url") or "").strip()
        # 裏取りURLの無いネタはここで落とす（プロンプト任せにしない）
        if not fact or not url.startswith(("http://", "https://")) or fact in known_texts:
            continue
        fact_id = f"{channel}-" + hashlib.sha1(fact.encode()).hexdigest()[:10]
        if os.path.exists(_path(fact_id)):
            continue
        data = _new_fact(fact_id, fact, (f.get("discovered_from") or "").strip(), channel)
        data["backing_url"] = url
        data["backing_note"] = (f.get("backing_note") or "").strip()
        known_texts.add(fact)
        saved.append(_save(data))
    if not saved:
        raise PipelineError(
            f"{channel} の裏取り付きネタを登録できませんでした"
            "（一次ソースが確認できなかったか、既存と重複）。"
        )
    return saved


def add_manual(fact: str, discovered_from: str, channel: str = "heisei") -> str:
    prefix = channel if channel in ("heisei", "showa") else "fact"
    fact_id = f"{prefix}-" + hashlib.sha1(fact.encode()).hexdigest()[:10]
    return _save(_new_fact(fact_id, fact.strip(), discovered_from or "", channel))


def set_backing(fact_id: str, url: str, note: str) -> None:
    if not (url or "").strip():
        raise PipelineError("--url に一次ソースのURLを指定してください。")
    data = _load(fact_id)
    data["backing_url"] = url.strip()
    data["backing_note"] = (note or "").strip()
    _save(data)


def mark(fact_id: str, status: str) -> None:
    data = _load(fact_id)
    if status == "adopted" and not (data.get("backing_url") or "").strip():
        # ここが本丸。裏取りの無い雑学を台本に流さない（docs/05 3章）
        raise PipelineError(
            f"{fact_id} は裏取り（一次ソース）が無いため採用できません。\n"
            "  --back <id> --url <一次ソースURL> で付けてから --adopt してください。"
        )
    data["status"] = status
    _save(data)


def show_list(channel: str = "") -> None:
    # channel が無い旧ファイルは廃止前の trivia 台帳（til-*）。表示互換のため既定を trivia にする
    rows = [f for f in saved_facts()
            if not channel or f.get("channel", "trivia") == channel]
    if not rows:
        print("候補がありません。--reddit か --add で集めてください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌"}
    for f in rows:
        backed = "🔗" if f.get("backing_url") else "  "
        ch = f.get("channel", "trivia")
        print(f"{icons.get(f['status'], '?')}{backed} [{ch}] {f['id']}  {f['fact'][:55]}")
    print("\n🔗=裏取り済み。裏取りが無いと --adopt できません")


def main() -> None:
    p = argparse.ArgumentParser(description="事実ベースのネタ収集と裏取り管理")
    p.add_argument("--add", metavar="FACT", help="ネタを手で積む")
    p.add_argument("--from", dest="discovered_from", metavar="URL", help="--add の発見元URL")
    p.add_argument("--discover", action="store_true",
                   help="Claude+web検索でネタを発見し、一次ソース付き候補を登録する")
    p.add_argument("--limit", type=int, default=5, help="--discover の本数（既定5）")
    p.add_argument("--channel", default="heisei", choices=("heisei", "showa"),
                   help="--add / --discover の対象チャンネル（既定 heisei）／--list の絞り込み")
    p.add_argument("--back", metavar="ID", help="裏取りを付ける対象")
    p.add_argument("--url", help="--back で付ける一次ソースURL")
    p.add_argument("--note", help="--back の補足（何のソースか）")
    p.add_argument("--list", action="store_true", help="候補一覧")
    p.add_argument("--adopt", metavar="ID", help="採用にする（裏取り必須）")
    p.add_argument("--reject", metavar="ID", help="不採用にする")
    args = p.parse_args()

    try:
        if args.list:
            # --list は既定で全件。--channel を明示したときだけ絞る
            show_list(args.channel if "--channel" in sys.argv else "")
        elif args.back:
            set_backing(args.back, args.url or "", args.note or "")
            print(f"裏取りを記録: {args.back}")
        elif args.adopt:
            mark(args.adopt, "adopted")
            print(f"採用: {args.adopt}")
        elif args.reject:
            mark(args.reject, "rejected")
            print(f"不採用: {args.reject}")
        elif args.add:
            path = add_manual(args.add, args.discovered_from or "", args.channel)
            print(f"追加: {os.path.relpath(path)}（{args.channel}）")
        elif args.discover:
            saved = discover(args.channel, args.limit)
            print(f"{len(saved)}本を裏取り付き候補として登録しました（{args.channel}）:")
            for path in saved:
                print(f"  {os.path.relpath(path)}")
            print("--list で確認できます。採用は --adopt か daily_run の自動採用で")
        else:
            p.error("--add / --discover / --back / --list / --adopt / --reject のいずれかを指定してください")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
