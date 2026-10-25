# -*- coding: utf-8 -*-
"""答案 → 选项字母的映射。

线上真实故障：模型和题库都会只回一个字母（"B"），而 normalize_text 会把开头
的字母当成选项前缀剥掉，字母答案被归一成空串，于是判定「没匹配上」，重试拿回
同一个字母，最后走了「随机选答案」——卷面上就是必错的答案。
"""
import unittest

from api.base import match_option_exact

SINGLE = ["A 人民群众", "B 人民军队", "C 中国共产党", "D 民兵"]
MULTI = ["A. 甲", "B. 乙", "C. 丙", "D. 丁"]


class BareLetterTest(unittest.TestCase):
    def test_bare_letter_maps_to_option(self):
        self.assertEqual(match_option_exact("B", SINGLE), "B")
        self.assertEqual(match_option_exact("b", SINGLE), "B")
        self.assertEqual(match_option_exact("B.", SINGLE), "B")
        self.assertEqual(match_option_exact("  B ", SINGLE), "B")
        self.assertEqual(match_option_exact("答案：B", SINGLE), "B")

    def test_letter_out_of_range_is_rejected(self):
        self.assertEqual(match_option_exact("E", SINGLE), "")
        self.assertEqual(match_option_exact("Z", SINGLE), "")

    def test_multiple_letters(self):
        self.assertEqual(match_option_exact("AC", MULTI), "AC")

    def test_english_word_is_not_treated_as_letters(self):
        self.assertEqual(match_option_exact("yes", SINGLE), "")
        self.assertEqual(match_option_exact("defense", SINGLE), "")


class TextMatchTest(unittest.TestCase):
    def test_exact_option_text(self):
        self.assertEqual(match_option_exact("人民军队", SINGLE), "B")

    def test_wrapped_text(self):
        self.assertEqual(match_option_exact("答案：民兵", SINGLE), "D")

    def test_no_match_returns_empty(self):
        self.assertEqual(match_option_exact("完全不相干的内容", SINGLE), "")

    def test_empty_input(self):
        self.assertEqual(match_option_exact("", SINGLE), "")
        self.assertEqual(match_option_exact("B", []), "")


if __name__ == "__main__":
    unittest.main()