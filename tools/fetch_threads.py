#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""おーぷん2ちゃんねるからスレを収集し、台本のネタ候補にする（docs/04 2-2章）。

    # 板から候補を集める（レス数が乗っているスレを新しい順に）
    .venv/bin/python tools/fetch_threads.py --board livejupiter

    # ブラウザで見つけたスレを直接取り込む（open2ch以外のURLは拒否される）
    .venv/bin/python tools/fetch_threads.py --url "https://hayabusa.open2ch.net/test/read.cgi/livejupiter/1785665449/"

    # 候補を眺めて、採用・不採用を付ける（採用したものだけが台本生成の入力になる）
    .venv/bin/python tools/fetch_threads.py --list
    .venv/bin/python tools/fetch_threads.py --adopt livejupiter-1785667068 \
        --powerword "スカート履けばいい" --ochi "解決策として夫が履けと言われて話が終わる"
    .venv/bin/python tools/fetch_threads.py --reject livejupiter-1785665449

引用元をおーぷん2ちゃんねるに限定する理由: 投稿がパブリックドメイン（転載自由）と
規約に明記されている唯一の主要掲示板だから。5chは運営の許可制、ガールズちゃんねる等は
許諾なき転載が不可（docs/04 2-2章の線引き表）。ここ以外のドメインはコードで拒否する。

**採用には基準がいる**（docs/05 3章）。以前はここに除外パターンしか無く、
採用は勘だった。結果、オチの無いスレから動画を作って本人評価「選定にセンスがない」。
ロンロンの天秤（51.2万）は「**コメントで復唱できる語が1個取れるか**」でスレを選んでいる
（docs/02 2章）。動画の26.7秒はその1語を届けるための助走でしかない。
--adopt はその1語（--powerword）とオチの一文（--ochi）を要求し、
**語がスレに実在すること**を検査する。造語を復唱させても滑るため。
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pipeline.common import PipelineError, normalize_powerword, read_secret

THREADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "threads")

# 引用してよいドメイン。これ以外は理由の如何を問わず拒否する（docs/04 2-2章）
ALLOWED_DOMAIN = "open2ch.net"

# 板 → サーバのサブドメイン。おーぷんの主要板はほぼ hayabusa に載っている
BOARDS = {
    "livejupiter": "hayabusa",   # なんでも実況（おんJ）。雑談・実話系の主力
    "news4vip": "hayabusa",      # VIP。ネタ・大喜利系
}

# 相手サーバーに負荷をかけない取得間隔（秒）。
# 1.5秒では8本連続の取得で429（Too Many Requests）が出て半分取りこぼした（#99）
FETCH_INTERVAL = 6.0
# 429を食らったときに待つ秒数。相手のレート制限が明けるのを待つ
RETRY_WAIT = 25.0

# 候補にするレス数の範囲。少なすぎると展開もオチも無く、
# 多すぎるとショートの尺（15〜40秒）に刈り込めない
MIN_RES = 10
MAX_RES = 400

# 保存するレス数の上限。選定と翻案に必要なのは序盤〜中盤の流れで、
# 長寿スレの後半は同じ話の繰り返しになりがち
KEEP_RES = 120

# 素直なUAで名乗る。datはHTTP/2のTLS指紋がCloudflareのチャレンジ対象になるが、
# urllibはHTTP/1.1なのでそのまま通る（curlで再現するときは --http1.1 が要る）
_UA = {"User-Agent": "personal-sns-operation/1.0 (content pipeline)"}


def _check_domain(url: str) -> None:
    """引用元の規約線引き（docs/04 2-2章）をコードで守る。ここは絶対に緩めない。"""
    host = urllib.parse.urlparse(url).hostname or ""
    if host != ALLOWED_DOMAIN and not host.endswith("." + ALLOWED_DOMAIN):
        raise PipelineError(
            f"引用できるのは転載自由の {ALLOWED_DOMAIN} だけです: {url}\n"
            "  5ch・ガールズちゃんねる等は規約上、許諾なき転載ができません（docs/04 2-2章）。"
        )


def _http(url: str, timeout: float = 20) -> bytes:
    """取得する。429は一度だけ待って引き直す（media.py の Openverse と同じ約束）。"""
    _check_domain(url)
    req = urllib.request.Request(url, headers=_UA)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                print(f"  レート制限のため{RETRY_WAIT:.0f}秒待ちます", file=sys.stderr)
                time.sleep(RETRY_WAIT)
                continue
            raise
    raise PipelineError(f"取得に失敗しました（429が続いています）: {url}")


def _decode(data: bytes) -> str:
    # 2ch互換のsubject.txt / datはCP932。壊れたバイトでスレ1本を捨てない
    return data.decode("cp932", errors="replace")


def _base_url(board: str) -> str:
    server = BOARDS.get(board)
    if not server:
        raise PipelineError(
            f"未登録の板です: {board}（登録済み: {', '.join(BOARDS)}）\n"
            "  板を増やすときは fetch_threads.py の BOARDS にサーバごと追記してください。"
        )
    return f"https://{server}.{ALLOWED_DOMAIN}/{board}"


def _clean_body(body: str) -> str:
    """datのレス本文を素のテキストにする。<br>は改行、タグは落とし、実体参照を戻す。"""
    text = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


# 候補から外すスレタイの型。目視選別のノイズを減らすためのもので、
# 面白いスレを機械が落とすリスクの方が高いので、確実に使えないものだけを対象にする
_NOISE_PATTERNS = (
    # 実況スレ: レスが試合経過の断片で、ショートのオチにならない
    re.compile(r"^【(実況|速報)】"),
    re.compile(r"(実況|放送)スレ"),
    # 続き物: 前提知識が要る。「Part10」「・22」「part995」「総合スレ69」など
    re.compile(r"(part|pt\.|★)\s*\d+", re.I),
    re.compile(r"[・･]\s*\d+\s*$"),
    # 末尾が2桁以上の通し番号だけのもの（「ファンクラブ69」「〜部10」）。
    # 1桁は「FF15」「Windows 7」のような題材そのものと紛れるので対象にしない
    re.compile(r"[^\d]\d{2,}\s*$"),
    # 定期の雑談・避難所系: 常連の内輪の会話でオチが無い
    re.compile(r"(雑談|待避所|避難所|総合)"),
)


def _is_noise(title: str) -> bool:
    """目視選別に載せる価値が無いスレタイか。"""
    return any(p.search(title) for p in _NOISE_PATTERNS)


def list_threads(board: str) -> list:
    """subject.txt からスレ一覧を取る。[{thread, title, res_count}] を新しい順で返す。"""
    text = _decode(_http(f"{_base_url(board)}/subject.txt"))
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(\d+)\.dat<>(.*)\s\((\d+)\)$", line.strip())
        if m:
            rows.append({
                "thread": m.group(1),
                "title": html.unescape(m.group(2)).strip(),
                "res_count": int(m.group(3)),
            })
    return rows


def fetch_thread(board: str, thread: str) -> dict:
    """dat を取ってレスを展開する。1レス目の後ろにスレタイが入っている（2ch互換形式）。"""
    dat = _decode(_http(f"{_base_url(board)}/dat/{thread}.dat"))
    title, res = "", []
    for i, line in enumerate(dat.splitlines(), 1):
        fields = line.split("<>")
        if len(fields) < 4:
            continue
        if i == 1 and len(fields) >= 5:
            title = html.unescape(fields[4]).strip()
        body = _clean_body(fields[3])
        if body:
            res.append({"no": i, "text": body})
        if len(res) >= KEEP_RES:
            break
    if not res:
        raise PipelineError(f"スレの本文が読めませんでした: {board}/{thread}")
    return {
        "id": f"{board}-{thread}",
        "board": board,
        "thread": thread,
        "url": f"https://{BOARDS[board]}.{ALLOWED_DOMAIN}/test/read.cgi/{board}/{thread}/",
        "title": title or res[0]["text"][:40],
        "res_count": len(res),
        "res": res,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # candidate → adopted / rejected。採用は必ず人の目視で決める（docs/05 3章）
        "status": "candidate",
    }


# ---------------------------------------------------------------- 採用基準
# 復唱できる語の長さ。ロンロンの実例「シャマルピー」は6字で、コメント欄が
# 「シャマルピー草」で埋まった。長いとコメントに書き写してもらえない
POWERWORD_MIN, POWERWORD_MAX = 2, 14
# オチを一文で言えないスレは、動画にしても着地しない
OCHI_MIN, OCHI_MAX = 10, 60

# 掲示板の内輪語。ロンロン（51.2万）は www も【悲報】もレス番も画面に出さず、
# 「2chまとめ」ではなく「短い笑い話」として売っている（docs/02 2章）。
# 内輪語を復唱の起点にすると、板を知らない層に届かず裾野が板の住人で頭打ちになる
_INSIDER = re.compile(r"(ワイ|イッチ|おん[JjＪ]|なん[JjＪ]|ｗｗ|ww|草生|>>\d|【悲報】|ニキ|ンゴ|クレメンス)")


def check_criteria(data: dict, powerword: str, ochi: str) -> None:
    """採用基準を満たすか。満たさないものは動画にしない（docs/05 3章）。

    ここを通ることが「面白いスレ」の定義。基準が無かった頃は勘で採用しており、
    オチの無いスレから動画を作っていた（#139）。
    """
    word = powerword.strip()
    if not (POWERWORD_MIN <= len(word) <= POWERWORD_MAX):
        raise PipelineError(
            f"パワーワードは{POWERWORD_MIN}〜{POWERWORD_MAX}字にしてください"
            f"（今: {len(word)}字「{word}」）。\n"
            "  コメント欄に書き写せる長さでないと復唱されません（docs/02 2章）。"
        )
    if _INSIDER.search(word):
        raise PipelineError(
            f"パワーワード「{word}」に掲示板の内輪語が入っています。\n"
            "  板を知らない層に届かず、復唱の裾野が住人で頭打ちになります（docs/02 2章）。\n"
            "  スレの中から、板を知らなくても笑える語を選び直してください。"
        )
    # スレ本文に無い語は、こちらが作った造語。復唱の起点にならない
    haystack = normalize_powerword(data["title"] + "".join(r["text"] for r in data["res"]))
    if normalize_powerword(word) not in haystack:
        raise PipelineError(
            f"パワーワード「{word}」がスレ本文にありません。\n"
            "  復唱される語はスレに元々あるものです。こちらで作った語では滑ります。\n"
            "  スレを読み直し、実際に書かれている語をそのまま使ってください。"
        )
    line = ochi.strip()
    if not (OCHI_MIN <= len(line) <= OCHI_MAX):
        raise PipelineError(
            f"オチは{OCHI_MIN}〜{OCHI_MAX}字の一文で書いてください（今: {len(line)}字）。\n"
            "  一文で言えないスレは、動画にしても着地しません。"
        )


# ロンロン適性の合格点（#214）。これ未満のスレでは作らない。
# レス数で選ぶと「荒れたスレ」「長い雑談」が上位に来る。実際、候補29本のうち
# 上位はほぼ画像投稿スレ・順位表・実況で、型に乗るスレは数本しか無かった
RONRON_MIN = 60
# 敗者復活の余地を残すボーダー線。採点済みでこれ未満のスレは RONRON_MIN に
# 届く見込みが無いので、sweep_scored_out() が candidate から下ろす（#279）
RONRON_SWEEP = 50
# 1回のAPI呼び出しで採点する候補数。まとめて渡して相対評価させる。
# max_tokens=12000 はもともと30本を見込んだ値（#241）。12本だと1日の収集分を
# 採点し切れず、未採点が翌日へ繰り越されて回転が詰まっていた（#279）
SCORE_BATCH = 30
# 採点に渡す本文の長さ。全文だと候補12本で数万字になる
_SCORE_BODY = 900

_SCORE_SYSTEM = """あなたは「ロンロンの天秤」型のショート動画にするスレを選ぶ編集者。
このチャンネルの面白さは**前提を1個だけ壊し、当事者が最後まで大真面目**という分裂にある。
知性は、感情ではなく**言葉かロジックのレベルで裏切りが起きる**ことから生まれる。

向くスレ（実測の型）:
- 同音異義・多義語の取り違えを本人が全力で押し通す
- ルールの穴を突く制度ハック（「き」から始まる食べ物→自分で作って命名すればよい）
- 語彙の接合ミス（壊れた敬語を完璧なつもりで喋る）
- 悪意ゼロの当事者が、異常に精密な言葉で自分の状況を語る

向かないスレ:
- 画像・写真の投稿スレ、絵の依頼スレ（会話が無い）
- 実況・順位表・競馬予想（断片の羅列でオチが無い）
- 常連の雑談・内輪の会話（前提知識が要る）
- ただ荒れているだけ、罵倒だけ（壊れているのが前提ではなく態度）
- 前提が2個以上壊れている（出鱈目になって知性が消える）

各スレに0〜100点を付ける。**60点以上を付けるのは、上の型に本当に乗るスレだけ**。
迷ったら低く付けること。弱いネタで作るより作らない方がよい。"""


def _path(thread_id: str) -> str:
    return os.path.join(THREADS_DIR, f"{thread_id}.json")


def _save(data: dict) -> str:
    os.makedirs(THREADS_DIR, exist_ok=True)
    path = _path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load(thread_id: str) -> dict:
    path = _path(thread_id)
    if not os.path.exists(path):
        raise PipelineError(f"候補がありません: {thread_id}（--list で確認してください）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def saved_threads() -> list:
    """保存済みのスレ全部。--list と後段（採用分の取り出し）が使う。"""
    rows = []
    for path in sorted(os.listdir(THREADS_DIR)) if os.path.isdir(THREADS_DIR) else []:
        if path.endswith(".json"):
            with open(os.path.join(THREADS_DIR, path), encoding="utf-8") as f:
                rows.append(json.load(f))
    return rows


def adopted_threads() -> list:
    """採用済みのスレだけ。台本生成（#89）はここからだけ読む。"""
    return [t for t in saved_threads() if t.get("status") == "adopted"]


def load_adopted(thread_id: str) -> dict:
    """採用済みスレを1本返す。未採用は拒否する（採用は人の目視で決める約束。docs/05 3章）。"""
    data = _load(thread_id)
    if data.get("status") != "adopted":
        raise PipelineError(
            f"{thread_id} は採用されていません（現在: {data.get('status')}）。\n"
            "  tools/fetch_threads.py --list で中身を確認し、--adopt を付けてください。"
        )
    # 基準ができる前に採用したスレを、そのまま台本に流さないための関門（#139）
    if not data.get("powerword"):
        raise PipelineError(
            f"{thread_id} にパワーワードがありません（採用基準ができる前の採用です）。\n"
            "  --adopt に --powerword / --ochi を付けて採用し直してください。"
        )
    return data


def score_candidates(threads: list) -> int:
    """未採点のスレにロンロン適性を付けて保存する。採点した本数を返す（#214）。

    レス数順に取ると「荒れたスレ」「長い雑談」が上位に来て、型に乗らないネタで
    動画を作ってしまう（本人指摘「ロンロンのスレ選定を真似して欲しい」）。

    1本ずつ聞くと候補の数だけAPIを叩くので、まとめて渡して**相対評価**させる。
    採点結果は台帳に残すので、同じスレを二度採点しない。
    """
    todo = [t for t in threads if t.get("ronron_score") is None][:SCORE_BATCH]
    if not todo:
        return 0
    api_key = read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt")
    if not api_key:
        raise PipelineError("ANTHROPIC_API_KEY が無いため適性を採点できません")
    try:
        import anthropic
    except ImportError:
        raise PipelineError("anthropic SDK がありません（.venv/bin/pip install anthropic）") from None

    blocks = []
    for i, t in enumerate(todo):
        body = "\n".join(r["text"] for r in t.get("res", []))[:_SCORE_BODY]
        blocks.append(f"### {i}\nタイトル: {t['title']}\n{body}")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        # 候補1本あたり4項目 × 最大30本に、adaptive thinking の思考ぶんが上乗せされる。
        # 足りないと本文が途中で切れて壊れたJSONが返る（#241）
        max_tokens=12000,
        system=_SCORE_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {"scores": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "premise": {"type": "string",
                                "description": "このスレで壊れている前提を1個だけ30字以内。無ければ空"},
                    "reason": {"type": "string", "description": "点の理由を40字以内で"},
                },
                "required": ["index", "score", "premise", "reason"],
                "additionalProperties": False,
            }}},
            "required": ["scores"],
            "additionalProperties": False,
        }}},
        messages=[{"role": "user", "content": "\n\n".join(blocks)}],
    )
    if response.stop_reason == "refusal":
        raise PipelineError("適性の採点を拒否されました（候補の内容が扱えません）。")
    # content[0] を決め打ちしない（adaptive thinking で先頭が思考になる。#215）
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise PipelineError("適性の採点が空で返りました。")
    if response.stop_reason == "max_tokens":
        raise PipelineError("適性の採点が途中で切れました（max_tokens）。候補を減らしてください。")
    try:
        rows = json.loads(text)["scores"]
    except (json.JSONDecodeError, KeyError) as e:
        # 呼び出し側（daily_run）は PipelineError だけを捕まえて「採点済みのぶんで進む」。
        # 素の例外を上げるとその日の生成ごと止まる（#241）
        raise PipelineError(f"適性の採点が壊れたJSONで返りました（{e}）。") from None

    scored = 0
    for row in rows:
        if not 0 <= row["index"] < len(todo):
            continue
        data = _load(todo[row["index"]]["id"])
        data["ronron_score"] = max(0, min(100, int(row["score"])))
        data["ronron_premise"] = row["premise"].strip()
        data["ronron_reason"] = row["reason"].strip()
        _save(data)
        scored += 1
    return scored


def sweep_scored_out() -> int:
    """採点済みで見込みの無い candidate を rejected へ落とす。落とした本数を返す。

    不合格スレを candidate のまま残すと、台帳は「候補あり」に見えるのに
    作れるものが無い、という見かけ倒しが膨らみ続ける（128本堆積・#279）。
    ボーダー層（RONRON_SWEEP 以上 RONRON_MIN 未満）は採点のブレで沈んだ
    可能性があるため候補に残す（敗者復活の検証は別Issueの材料）。
    """
    swept = 0
    for t in saved_threads():
        if (t.get("status") == "candidate"
                and t.get("ronron_score") is not None
                and t["ronron_score"] < RONRON_SWEEP):
            t["status"] = "rejected"
            _save(t)
            swept += 1
    return swept


def collect(board: str, limit: int) -> list:
    """板から候補を集めて保存する。選定は機械で絞りすぎず、人の目視に委ねる。"""
    known = {t["id"] for t in saved_threads()}
    picked, saved = [], []
    for row in list_threads(board):
        if not (MIN_RES <= row["res_count"] <= MAX_RES):
            continue
        if _is_noise(row["title"]):
            continue
        if f"{board}-{row['thread']}" in known:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    for i, row in enumerate(picked):
        if i:
            time.sleep(FETCH_INTERVAL)  # 相手サーバーへの負荷を抑える
        try:
            saved.append(_save(fetch_thread(board, row["thread"])))
        except (urllib.error.URLError, OSError) as e:
            print(f"  取得失敗: {row['title']} ({e})", file=sys.stderr)
    return saved


# 過去ログ発掘の検索クエリ。「当事者が大真面目に語り出す」タイトルの定型を狙う
# （used実績: JC・元警備員・Vtuber は全部「〜だけど質問ある」型）。
# 勢いと違って過去ログは尽きないので、日替わりで一巡させて検索結果の偏りを避ける。
# ヒットが薄い・ノイズが多い語は実測で入れ替えていく
KAKO_QUERIES = (
    "だけど質問ある",
    "気づいてしまった",
    "って言われたんやが",
    "納得いかない",
    "きんし",
    "教えてほしいんだが",
    "ガチで悩んでる",
    "俺だけ",
)


def kako_query_today() -> str:
    """今日使う発掘クエリ。日替わりで一巡させる（#281）。"""
    return KAKO_QUERIES[datetime.date.today().toordinal() % len(KAKO_QUERIES)]


def collect_kako(query: str, limit: int) -> list:
    """スレタイ検索（find.open2ch.net）→ dat直読みで過去スレを候補にする（#281）。

    勢い（subject.txt）は直近150件の窓しか無く、板を増やしても60点級は
    湧かなかった（#280）。datは板の窓から落ちても残り続ける（実測）ので、
    検索でIDさえ引ければ時間を遡って母集団を広げられる。
    過去ログ倉庫（/板/kako/）は中身が空で使えない（実測）。検索が唯一の入口。
    """
    page = _http("https://find.open2ch.net/?q=" + urllib.parse.quote(query))
    text = page.decode("utf-8", errors="replace")  # 検索はUTF-8（datのCP932と違う）
    known = {t["id"] for t in saved_threads()}
    saved = []
    for board, thread in dict.fromkeys(re.findall(r"read\.cgi/([a-z0-9]+)/(\d+)", text)):
        if len(saved) >= limit:
            break
        if f"{board}-{thread}" in known:
            continue
        if board not in BOARDS:
            # 検索は全板に当たる。サーバ名は結果のURLに入っているので、
            # from_url と同じ流儀でプロセス内だけ登録する
            m = re.search(
                rf"//([a-z0-9]+)\.{re.escape(ALLOWED_DOMAIN)}/test/read\.cgi/{board}/{thread}",
                text)
            if not m:
                continue
            BOARDS[board] = m.group(1)
        # 検索ページ取得の直後に dat を引くので、1本目の前にも間隔を空ける
        # （collect と違い直前のHTTPが同一ドメインへの検索アクセス。429の実測あり）
        time.sleep(FETCH_INTERVAL)
        try:
            data = fetch_thread(board, thread)
        except (PipelineError, urllib.error.URLError, OSError) as e:
            print(f"  取得失敗: {board}/{thread} ({e})", file=sys.stderr)
            continue
        # 検索結果にレス数が載らないので取得後に絞る。短すぎは展開もオチも無い。
        # 長すぎ側は KEEP_RES が刈るので検査しない
        if data["res_count"] < MIN_RES or _is_noise(data["title"]):
            continue
        saved.append(_save(data))
    return saved


def from_url(url: str) -> str:
    """read.cgi / dat のURLを1本取り込む。ドメイン検査は _http が必ず通す。"""
    _check_domain(url)
    m = (re.search(r"/test/read\.cgi/([a-z0-9]+)/(\d+)", url)
         or re.search(r"/([a-z0-9]+)/dat/(\d+)\.dat", url))
    if not m:
        raise PipelineError(f"スレのURLとして解釈できません: {url}")
    board, thread = m.group(1), m.group(2)
    if board not in BOARDS:
        # URL直指定は板の登録が無くてもサーバ名がURLに入っているので、そのまま使う
        server = urllib.parse.urlparse(url).hostname.split(".")[0]
        BOARDS[board] = server
    return _save(fetch_thread(board, thread))


def mark(thread_id: str, status: str, powerword: str = "", ochi: str = "",
         adopted_by: str = "") -> None:
    """状態を書き換える。採用は基準を満たすときだけ通す（docs/05 3章）。

    adopted_by は「人の目視を経ていない採用」を台帳に残すためのもの（#191）。
    後から --reject で覆すときの判断材料になる。
    """
    data = _load(thread_id)
    if status == "adopted":
        check_criteria(data, powerword, ochi)
        data["powerword"] = powerword.strip()
        data["ochi"] = ochi.strip()
        if adopted_by:
            data["adopted_by"] = adopted_by
            data["adopted_at"] = datetime.date.today().isoformat()
    data["status"] = status
    _save(data)


def mark_used(thread_id: str) -> bool:
    """消費したスレに印を付ける。既に used なら何もしない（False を返す）。

    同じネタで2本作らないための印。**台本が保存できてからだけ**呼ぶこと
    （先に付けると、生成が落ちたときにネタだけ失う）。

    used化を daily_run だけが持っていたため、make_video での単発生成では
    adopted のまま残り、次回の daily_run が同じスレでもう1本作っていた（#230）。
    """
    data = _load(thread_id)
    if data.get("status") == "used":
        return False
    data["status"] = "used"
    data["used_at"] = datetime.date.today().isoformat()
    _save(data)
    return True


def show_list() -> None:
    rows = saved_threads()
    if not rows:
        print("候補がありません。--board で収集してください。")
        return
    icons = {"candidate": "・", "adopted": "✅", "rejected": "❌"}
    for t in rows:
        print(f"{icons.get(t['status'], '?')} {t['id']}  ({t['res_count']}res)  {t['title']}")
        # 採用理由を一覧で見えるようにする。どの語を狙って作るかが動画の設計そのもの
        if t.get("powerword"):
            print(f"      💬「{t['powerword']}」／ オチ: {t.get('ochi', '')}")
    print(f"\n採用 {sum(t['status'] == 'adopted' for t in rows)} / "
          f"候補 {sum(t['status'] == 'candidate' for t in rows)} / "
          f"不採用 {sum(t['status'] == 'rejected' for t in rows)}")


def main() -> None:
    p = argparse.ArgumentParser(description="おーぷん2ちゃんねるからスレを収集する")
    p.add_argument("--board", help=f"収集する板（{' / '.join(BOARDS)}）")
    p.add_argument("--limit", type=int, default=10, help="収集する候補数（既定10）")
    p.add_argument("--url", help="スレのURLを1本だけ取り込む（open2ch限定）")
    p.add_argument("--kako", metavar="QUERY",
                   help="スレタイ検索で過去ログから収集する（--limit で本数指定）")
    p.add_argument("--list", action="store_true", help="保存済み候補の一覧")
    p.add_argument("--adopt", metavar="ID", help="候補を採用にする（--powerword / --ochi が要る）")
    p.add_argument("--powerword", default="",
                   help=f"コメントで復唱される語。スレに実在するものを{POWERWORD_MIN}〜{POWERWORD_MAX}字で")
    p.add_argument("--ochi", default="",
                   help=f"オチを一文で（{OCHI_MIN}〜{OCHI_MAX}字）")
    p.add_argument("--reject", metavar="ID", help="候補を不採用にする")
    args = p.parse_args()

    try:
        if args.list:
            show_list()
        elif args.adopt:
            mark(args.adopt, "adopted", args.powerword, args.ochi)
            print(f"採用: {args.adopt}  💬「{args.powerword.strip()}」")
        elif args.reject:
            mark(args.reject, "rejected")
            print(f"不採用: {args.reject}")
        elif args.url:
            print(f"取り込み: {os.path.relpath(from_url(args.url))}")
        elif args.kako:
            saved = collect_kako(args.kako, args.limit)
            print(f"{len(saved)}本を保存しました → data/threads/")
            print("次: --list で眺めて --adopt / --reject を付けてください（採用は人の目視）")
        elif args.board:
            saved = collect(args.board, args.limit)
            print(f"{len(saved)}本を保存しました → data/threads/")
            print("次: --list で眺めて --adopt / --reject を付けてください（採用は人の目視）")
        else:
            p.error("--board / --kako / --url / --list / --adopt / --reject のいずれかを指定してください")
    except PipelineError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
