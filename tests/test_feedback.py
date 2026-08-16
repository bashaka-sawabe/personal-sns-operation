# -*- coding: utf-8 -*-
"""実績係数（tools/feedback.py）の過学習ガードの検査（#293）。

    .venv/bin/python -m unittest discover tests

係数の計算は factors_from_samples() に閉じた純粋関数なので、
CSVや台帳を用意せずにここだけで検証できる。
"""
import unittest

from tools.feedback import FACTOR_MAX, FACTOR_MIN, KIND_MIN_N, factors_from_samples


class FactorsFromSamplesTest(unittest.TestCase):
    def test_n数が閾値未満の系統は中立(self):
        # tenbin は成績が良くても n=KIND_MIN_N-1 なら 1.0 のまま
        samples = [("tenbin", 0.9, 5.0)] * (KIND_MIN_N - 1) + \
                  [("hitokoto", 0.3, 0.5)] * KIND_MIN_N
        factors = factors_from_samples(samples)
        self.assertEqual(factors["tenbin"], 1.0)
        self.assertNotEqual(factors["hitokoto"], 1.0)

    def test_成績の良い系統は係数が1を超える(self):
        samples = [("tenbin", 0.8, None)] * KIND_MIN_N + \
                  [("hitokoto", 0.4, None)] * KIND_MIN_N
        factors = factors_from_samples(samples)
        self.assertGreater(factors["tenbin"], 1.0)
        self.assertLess(factors["hitokoto"], 1.0)

    def test_係数は可動域にクランプされる(self):
        # 1系統だけ極端に良くても FACTOR_MAX で止まる（バズ1本で振り切らせない）
        samples = [("tenbin", 0.9, 100.0)] * KIND_MIN_N + \
                  [("hitokoto", 0.01, 0.01)] * KIND_MIN_N
        factors = factors_from_samples(samples)
        self.assertEqual(factors["tenbin"], FACTOR_MAX)
        self.assertEqual(factors["hitokoto"], FACTOR_MIN)

    def test_指標が全部欠けている系統は中立(self):
        samples = [("gijinka", None, None)] * KIND_MIN_N + \
                  [(None, 0.5, 1.0)] * KIND_MIN_N
        factors = factors_from_samples(samples)
        self.assertEqual(factors["gijinka"], 1.0)

    def test_系統不明の動画はベースラインにだけ効く(self):
        # kind=None は係数の対象にならない（factors に現れない）
        samples = [(None, 0.5, 1.0)] * 5 + [("tenbin", 0.5, 1.0)] * KIND_MIN_N
        factors = factors_from_samples(samples)
        self.assertNotIn(None, factors)
        # 全体平均と同じ成績なら係数はほぼ1
        self.assertAlmostEqual(factors["tenbin"], 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
