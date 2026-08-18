# -*- coding: utf-8 -*-
"""表情差分の出し分け（#311）の検査。

    .venv/bin/python -m unittest discover tests

表情はセリフ行の属性で、(話者, 表情) がアイコンのオーバーレイ単位になる。
表情なしの台本（既存3チャンネル）が従来と同じ「話者ごと1枚」に退化することを
ここで固定する——退化しないと既存チャンネルの出力が変わってしまう。
"""
import os
import tempfile
import unittest
from unittest import mock

from tools.pipeline import media
from tools.pipeline.render import _speaker_windows


def _scene(phrases):
    return {"phrases": phrases, "dur": sum(p["dur"] for p in phrases) + 0.1}


class SpeakerWindowsTest(unittest.TestCase):
    def test_表情なしは従来どおり話者ごと1キー(self):
        wins = _speaker_windows(_scene([
            {"speaker": "zundamon", "text": "a", "dur": 1.0},
            {"speaker": "metan", "text": "b", "dur": 1.0},
            {"speaker": "zundamon", "text": "c", "dur": 1.0},
        ]))
        self.assertEqual(set(wins), {("zundamon", ""), ("metan", "")})
        self.assertEqual(len(wins[("zundamon", "")]), 2)

    def test_表情つきは話者と表情の組で分かれる(self):
        wins = _speaker_windows(_scene([
            {"speaker": "banzawa", "text": "a", "dur": 1.0, "expression": "normal"},
            {"speaker": "banzawa", "text": "b", "dur": 1.0, "expression": "angry"},
        ]))
        self.assertEqual(set(wins), {("banzawa", "normal"), ("banzawa", "angry")})


class IconImageTest(unittest.TestCase):
    def test_表情差分があれば優先し無ければ基本に落ちる(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("banzawa.png", "banzawa_angry.png"):
                open(os.path.join(d, name), "wb").close()
            with mock.patch.object(media, "ICONS_DIR", d):
                self.assertTrue(media.icon_image("banzawa", "angry").endswith("_angry.png"))
                # 用意していない表情は基本アイコンに落ちる（素材をブロッカーにしない）
                self.assertTrue(media.icon_image("banzawa", "smug").endswith("banzawa.png"))
                # normal は基本アイコンそのもの
                self.assertTrue(media.icon_image("banzawa", "normal").endswith("banzawa.png"))
                self.assertIsNone(media.icon_image("owada", "angry"))
