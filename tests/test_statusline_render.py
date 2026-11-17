#!/usr/bin/env python3
"""Unit tests for Antigravity Statusline Render Engine (AGY 1.5.9+ compatible)."""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))
from statusline_render import (
    format_cost_segment,
    format_model_label,
    format_quota_segment,
    format_workspace,
    render_statusline,
)


class TestStatuslineHelpers(unittest.TestCase):
    def test_workspace_with_git_branch_and_dirty(self):
        payload = {
            "workspace": "/tmp/my-project",
            "branch": "main",
            "dirty": True,
        }
        self.assertEqual(format_workspace(payload), "/tmp/my-project (main*)")

        payload_vcs = {
            "workspace": {"current_dir": "/tmp/a/b/c/d"},
            "vcs": {"branch": "feat/agy-1.5.9", "dirty": False},
        }
        self.assertEqual(format_workspace(payload_vcs), ".../b/c/d (feat/agy-1.5.9)")

    def test_model_label_and_effort_dedup(self):
        name, pool = format_model_label(
            {"model": {"display_name": "Gemini 4 Hydrogen"}, "effort": "High"},
            "",
        )
        self.assertEqual(name, "Gemini 4 Hydrogen (High)")
        self.assertEqual(pool, "gemini")

        name_argon, pool_argon = format_model_label(
            {"model": {"id": "gemini-4-argon-medium", "display_name": "Gemini 4 Argon (Medium)"}},
            "",
        )
        self.assertEqual(name_argon, "Gemini 4 Argon (Medium)")
        self.assertEqual(pool_argon, "argon")

        name_dup, _ = format_model_label(
            {"model": {"display_name": "Gemini 4 Hydrogen (High)"}, "effort": "High"},
            "",
        )
        self.assertEqual(name_dup, "Gemini 4 Hydrogen (High)")

        name_3p, pool_3p = format_model_label(
            {"model": {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"}},
            "",
        )
        self.assertEqual(name_3p, "Claude Sonnet 4.6")
        self.assertEqual(pool_3p, "claude")

    def test_quota_single_and_multi_pool_display(self):
        legacy = {
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.94, "reset_in_seconds": 3900},
                "gemini-weekly": {"remaining_fraction": 0.28, "reset_in_seconds": 90000},
            }
        }
        q_single, is_multi = format_quota_segment(legacy, active_pool="gemini")
        self.assertFalse(is_multi)
        self.assertEqual(strip_ansi(q_single), "Quota: Gemini 5h 94% [1h5m] · Wk 28% [1d1h]")

        multi = {
            "quota": [
                {"quota_bucket": "gemini-5h", "remaining_percentage": 95, "reset_in_seconds": 14400},
                {"quota_bucket": "gemini-weekly", "remaining_percentage": 88, "reset_in_seconds": 300000},
                {"quota_bucket": "gemini-pro-5h", "remaining_percentage": 72, "reset_in_seconds": 7800},
                {"quota_bucket": "gemini-pro-weekly", "remaining_percentage": 64, "reset_in_seconds": 250000},
                {"quota_bucket": "3p-5h", "remaining_percentage": 15, "reset_in_seconds": 5400},
                {"quota_bucket": "3p-weekly", "remaining_percentage": 45, "reset_in_seconds": 400000},
            ]
        }
        q_multi, is_multi_true = format_quota_segment(multi, active_pool="argon")
        self.assertTrue(is_multi_true)
        self.assertEqual(
            strip_ansi(q_multi),
            "Quota: Gemini 5h 95% [4h0m] · Wk 88%  |  *Argon 5h 72% [2h10m] · Wk 64%  |  Claude 5h 15% [1h30m] · Wk 45%",
        )

    def test_cost_segment(self):
        self.assertEqual(format_cost_segment({"cost": {"total_cost_usd": 0.256}}), "Cost: $0.26")
        self.assertEqual(format_cost_segment({"subagent_usd": 0.041}), "Sub: $0.04")
        self.assertEqual(format_cost_segment({}), "")


class TestStatuslineRenderIntegration(unittest.TestCase):
    def test_render_with_transcript_path_and_no_db_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_dir = os.path.join(tmpdir, "brain")
            parent_id = "11111111-1111-1111-1111-111111111111"
            sub_id = "22222222-2222-2222-2222-222222222222"

            parent_log_dir = os.path.join(brain_dir, parent_id, ".system_generated", "logs")
            sub_log_dir = os.path.join(brain_dir, sub_id, ".system_generated", "logs")
            os.makedirs(parent_log_dir, exist_ok=True)
            os.makedirs(sub_log_dir, exist_ok=True)

            parent_transcript = os.path.join(parent_log_dir, "transcript.jsonl")
            with open(parent_transcript, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 2000,
                    "output_tokens": 500,
                    "cache_read_tokens": 8000,
                    "tool_calls": [{
                        "name": "invoke_subagent",
                        "args": {"TypeName": "architect", "Role": "Reviewer", "Prompt": "check"},
                    }],
                }) + "\n")
                f.write(json.dumps({
                    "type": "TOOL_RESPONSE",
                    "content": json.dumps({"conversationId": sub_id, "role": "Reviewer", "typeName": "architect"}),
                }) + "\n")

            with open(os.path.join(sub_log_dir, "transcript.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_tokens": 3000,
                }) + "\n")

            payload = {
                "conversation_id": parent_id,
                "transcript_path": parent_transcript,
                "workspace": "/tmp/demo",
                "branch": "main",
                "dirty": True,
                "model": {"display_name": "Gemini 4 Hydrogen"},
                "effort": "High",
                "context_window": {
                    "remaining_percentage": 75.0,
                    "total_input_tokens": 250000,
                    "context_window_size": 1000000,
                    "exceeds_200k_tokens": True,
                },
                "cost": {"total_cost_usd": 0.15},
            }

            out = render_statusline(payload, app_data_dir=tmpdir)
            self.assertIn("[/tmp/demo (main*)] [Gemini 4 Hydrogen (High)]", out)
            self.assertIn("25.0% (250.0k/1.00M | Cache 78.6%)", out)
            self.assertIn("!200k", out)
            self.assertIn("Cost: $0.15", out)
            self.assertIn("Agents: 0/1", out)
            self.assertIn("[architect: Reviewer]", out)
            self.assertIn("1.2k (in: 1.0k / out: 200 | saved: 3.0k [75.0%])", out)

    def test_render_with_sqlite_db_and_top3_rollup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_dir = os.path.join(tmpdir, "brain")
            db_path = os.path.join(tmpdir, "conversation_summaries.db")
            parent_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            os.makedirs(os.path.join(brain_dir, parent_id, ".system_generated", "logs"), exist_ok=True)
            with open(os.path.join(brain_dir, parent_id, ".system_generated", "logs", "transcript.jsonl"), "w") as f:
                f.write(json.dumps({"input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 1000}) + "\n")

            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id TEXT, parent_conversation_id TEXT, agent_name TEXT, "
                "status TEXT, not_fully_idle INTEGER, title TEXT, preview TEXT)"
            )
            for idx in range(4):
                sid = f"bbbbbbbb-bbbb-bbbb-bbbb-00000000000{idx}"
                sdir = os.path.join(brain_dir, sid, ".system_generated", "logs")
                os.makedirs(sdir, exist_ok=True)
                with open(os.path.join(sdir, "transcript.jsonl"), "w") as sf:
                    sf.write(json.dumps({"input_tokens": (idx + 1) * 1000, "output_tokens": 100, "cache_read_tokens": 0}) + "\n")
                conn.execute(
                    "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, parent_id, f"worker-{idx}", "CASCADE_RUN_STATUS_IDLE", 0, "", ""),
                )
            conn.commit()
            conn.close()

            out = render_statusline({"conversation_id": parent_id, "workspace": "/tmp/ws"}, app_data_dir=tmpdir)
            self.assertIn("Agents: 0/4", out)
            self.assertIn("(+1 隻累計 1.1k)", out)

    def test_parent_conversation_id_never_counted_as_subagent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_dir = os.path.join(tmpdir, "brain")
            parent_id = "99999999-9999-9999-9999-999999999999"
            log_dir = os.path.join(brain_dir, parent_id, ".system_generated", "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "transcript.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "GENERIC",
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "content": f'ANTIGRAVITY_SOURCE_METADATA={{"tool":{{"conversationId":"{parent_id}"}}}}',
                }) + "\n")

            out = render_statusline({"conversation_id": parent_id, "workspace": "/tmp/ws"}, app_data_dir=tmpdir)
            self.assertIn("Agents: 0/0", out)
            self.assertNotIn("[Main]", out)


if __name__ == "__main__":
    unittest.main()
