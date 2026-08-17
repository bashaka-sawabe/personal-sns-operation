# -*- coding: utf-8 -*-
"""jiji の座組検査と専属キャストのスコープの検査（#310）。

    .venv/bin/python -m unittest discover tests

座組（泊開幕・頭取/野木締め・登場2〜3人・政党名なし）はプロンプトに書くだけでは
守られない前提で form_issues が数える。ここではその検査自体を、実際に使う
チャンネル設定（data/channels/jiji.json）で検証する。
"""
import unittest

from tools.pipeline import channels
from tools.pipeline.script import _allowed_speakers, form_issues

JIJI_KEYS = ("banzawa", "owada", "todori", "gondo",
             "tomari", "shirosaki", "kobikado", "nogi")


def _script(speaker_lines):
    """座組検査に必要な最小限の台本を組む。"""
    return {
        "title": "【査定不能】テストの出来事が起きた【だいたい銀行】",
        "emotion": "呆れ",
        "powerword": "テスト",
        "first_hand": "",
        "scenes": [{
            "caption": "テストの見出し",
            "dialogue": [{"speaker": sp, "text": t} for sp, t in speaker_lines],
            "image_prompt": "office window dusk",
        }],
        "caption": "テスト",
        "hashtags": [],
    }


def _jiji_only(issues):
    """座組検査の指摘だけを取り出す（行数・タイトル等の一般検査と切り分ける）。"""
    kws = ("泊", "締め", "登場人物", "野木", "政党名")
    return [i for i in issues if any(k in i for k in kws)]


class CastScopeTest(unittest.TestCase):
    def test_専属キャストは他チャンネルの脇役に混ざらない(self):
        extras = channels.extra_speakers()
        for key in JIJI_KEYS:
            self.assertNotIn(key, extras)

    def test_jijiの話者は配役の8人だけ(self):
        cfg = channels.load("jiji")
        self.assertEqual(set(_allowed_speakers(cfg)), set(JIJI_KEYS))

    def test_memeの話者にjiji専属が混ざらない(self):
        cfg = channels.load("meme")
        allowed = _allowed_speakers(cfg)
        self.assertIn("zundamon", allowed)
        for key in JIJI_KEYS:
            self.assertNotIn(key, allowed)


class JijiFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = channels.load("jiji")

    def test_座組が守られていれば座組の指摘は出ない(self):
        s = _script([
            ("tomari", "ここだけの話だけど、例の件が動いたらしい"),
            ("banzawa", "ガチャ"),
            ("banzawa", "静かに聞かせてもらおう。" + "その話の続きを詳しく頼む。" * 3),
            ("todori", "……聞いただけで、偉い"),
        ])
        self.assertEqual(_jiji_only(form_issues(s, self.cfg)), [])

    def test_泊以外の開幕は差し戻し(self):
        s = _script([("banzawa", "私から話そう"),
                     ("todori", "……偉い")])
        self.assertTrue(any("泊" in i for i in form_issues(s, self.cfg)))

    def test_締めが頭取でも野木でもないと差し戻し(self):
        s = _script([("tomari", "ここだけの話だけど"),
                     ("banzawa", "落とし前はつけてもらう")])
        self.assertTrue(any("締め" in i for i in form_issues(s, self.cfg)))

    def test_登場人物が4人いると差し戻し(self):
        s = _script([("tomari", "ここだけの話だけど"),
                     ("banzawa", "ほう"), ("owada", "施しですなあ"),
                     ("nogi", "……解散")])
        self.assertTrue(any("登場人物" in i for i in form_issues(s, self.cfg)))

    def test_野木の長台詞は差し戻し(self):
        s = _script([("tomari", "ここだけの話だけど"),
                     ("nogi", "私は普段は喋らないが今日だけは事情を最初から最後まで全部説明させてもらうことにする")])
        self.assertTrue(any("野木" in i for i in form_issues(s, self.cfg)))

    def test_政党名が入ると差し戻し(self):
        s = _script([("tomari", "ここだけの話だけど、自民党がどうこうという話だ"),
                     ("todori", "……偉い")])
        self.assertTrue(any("政党名" in i for i in form_issues(s, self.cfg)))

    def test_明治維新は政党名として誤検知しない(self):
        s = _script([("tomari", "ここだけの話だけど、明治維新の頃からある制度らしい"),
                     ("todori", "……古いだけで、偉い")])
        self.assertFalse(any("政党名" in i for i in form_issues(s, self.cfg)))
