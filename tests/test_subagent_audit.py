#!/usr/bin/env python3
"""
Unit tests for Antigravity Multi-Agent Token Efficiency Analyzer (subagent_audit.py)
Path: ~/.gemini/config/skills/token-audit/scripts/test_subagent_audit.py
"""

import unittest
import sys
import os

# Ensure scripts directory is in python module search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))
from subagent_audit import (
    get_display_width,
    fit_text_width,
    pad_cjk,
    format_tokens,
    format_duration,
    calculate_efficiency_metrics,
    aggregate_by_agent_type,
    rank_efficiency_highlights,
)


class TestFormattingAndCJK(unittest.TestCase):
    def test_cjk_display_width(self):
        self.assertEqual(get_display_width("abc"), 3)
        self.assertEqual(get_display_width("中文"), 4)
        self.assertEqual(get_display_width("代理: agent"), 11)

    def test_fit_text_width(self):
        text = "這是一個非常長的代理任務描述"
        fitted = fit_text_width(text, 10)
        self.assertTrue(get_display_width(fitted) <= 10)
        self.assertTrue(fitted.endswith("…"))

    def test_pad_cjk(self):
        padded = pad_cjk("測試", 8, "left")
        self.assertEqual(get_display_width(padded), 8)

        padded_right = pad_cjk("123", 6, "right")
        self.assertEqual(get_display_width(padded_right), 6)
        self.assertTrue(padded_right.startswith("   "))

    def test_format_tokens(self):
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(500), "500")
        self.assertEqual(format_tokens(1500), "1.5k")
        self.assertEqual(format_tokens(65558), "65.6k")
        self.assertEqual(format_tokens(1250000), "1.25M")

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "-")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(190), "3m 10s")
        self.assertEqual(format_duration(3665), "1h 1m")


class TestEfficiencyCalculations(unittest.TestCase):
    def setUp(self):
        self.subagents = [
            {
                "id": "sub1",
                "short_id": "sub1",
                "name": "translator",
                "label": "translator: 初翻",
                "in_tokens": 10000,
                "out_tokens": 5000,
                "cache_tokens": 30000,
                "total_tokens": 15000,
                "turns": 5,
                "tool_calls_count": 2,
                "duration_seconds": 60.0,
            },
            {
                "id": "sub2",
                "short_id": "sub2",
                "name": "polisher",
                "label": "polisher: 潤飾",
                "in_tokens": 20000,
                "out_tokens": 15000,
                "cache_tokens": 80000,
                "total_tokens": 35000,
                "turns": 7,
                "tool_calls_count": 10,
                "duration_seconds": 120.0,
            },
        ]
        self.main_tokens = 50000
        self.total_sub_tokens = 50000

    def test_calculate_efficiency_metrics(self):
        calculate_efficiency_metrics(self.subagents, self.main_tokens, self.total_sub_tokens)

        sub1 = self.subagents[0]
        self.assertAlmostEqual(sub1["out_in_ratio"], 50.0)
        self.assertAlmostEqual(sub1["tokens_per_turn"], 3000.0)
        self.assertAlmostEqual(sub1["tools_per_turn"], 0.4)
        self.assertAlmostEqual(sub1["share_of_subagents"], 30.0)
        self.assertAlmostEqual(sub1["share_of_session"], 15.0)
        # sub1 cache_rate: 30000 / (10000 + 30000) = 75.0%
        self.assertAlmostEqual(sub1["cache_rate"], 75.0)

        sub2 = self.subagents[1]
        self.assertAlmostEqual(sub2["out_in_ratio"], 75.0)
        self.assertAlmostEqual(sub2["tokens_per_turn"], 5000.0)
        self.assertAlmostEqual(sub2["tools_per_turn"], 10 / 7)
        self.assertAlmostEqual(sub2["share_of_subagents"], 70.0)
        self.assertAlmostEqual(sub2["share_of_session"], 35.0)
        # sub2 cache_rate: 80000 / (20000 + 80000) = 80.0%
        self.assertAlmostEqual(sub2["cache_rate"], 80.0)

    def test_aggregate_by_agent_type(self):
        groups = aggregate_by_agent_type(self.subagents)
        self.assertIn("translator", groups)
        self.assertIn("polisher", groups)
        self.assertEqual(groups["translator"]["count"], 1)
        self.assertEqual(groups["translator"]["total_tokens"], 15000)
        self.assertAlmostEqual(groups["translator"]["avg_cache_rate"], 75.0)
        self.assertEqual(groups["polisher"]["tool_calls_count"], 10)
        self.assertAlmostEqual(groups["polisher"]["avg_cache_rate"], 80.0)

    def test_rank_efficiency_highlights(self):
        calculate_efficiency_metrics(self.subagents, self.main_tokens, self.total_sub_tokens)
        hl = rank_efficiency_highlights(self.subagents)
        self.assertEqual(hl["heavyweight"]["id"], "sub2")
        self.assertEqual(hl["high_synthesis"]["id"], "sub2")
        self.assertEqual(hl["tool_intensive"]["id"], "sub2")
        self.assertEqual(hl["fastest"]["id"], "sub1")
        self.assertEqual(hl["highest_cache"]["id"], "sub2")


if __name__ == "__main__":
    unittest.main()
