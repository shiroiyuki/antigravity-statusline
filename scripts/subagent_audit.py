#!/usr/bin/env python3
"""
Antigravity Multi-Agent Token Efficiency Analyzer & Audit Engine
Path: ~/.gemini/config/skills/token-audit/scripts/subagent_audit.py

Provides comprehensive inspection, granular token breakdown, prompt cache metrics,
and multi-agent efficiency analytics for Antigravity subagents.
"""

import os
import sys
import glob
import json
import sqlite3
import datetime
import argparse
import unicodedata
from typing import Dict, Any, List, Optional, Tuple


def get_display_width(text: str) -> int:
    """Calculates visible terminal character width considering East Asian full-width characters."""
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in text)


def fit_text_width(text: str, max_width: int) -> str:
    """Truncates text to fit within max_width display cells, appending '…' if truncated."""
    if get_display_width(text) <= max_width:
        return text

    cur_width = 0
    res = []
    for c in text:
        w = 2 if unicodedata.east_asian_width(c) in ("F", "W") else 1
        if cur_width + w > max_width - 1:
            res.append("…")
            break
        res.append(c)
        cur_width += w
    return "".join(res)


def pad_cjk(text: str, target_width: int, align: str = "left") -> str:
    """Pads text to exact terminal display width, correctly handling CJK wide characters."""
    fitted = fit_text_width(text, target_width)
    curr_w = get_display_width(fitted)
    pad = " " * max(0, target_width - curr_w)
    return (fitted + pad) if align == "left" else (pad + fitted)


def format_tokens(n: Any) -> str:
    """Formats raw token integers into human-readable compact notation."""
    if n is None:
        return "0"
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n/1_000_000:.2f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}k"
        return str(n)
    except Exception:
        return str(n)


def format_duration(seconds: float) -> str:
    """Formats elapsed seconds into compact duration string (e.g. 2m 15s)."""
    if seconds is None or seconds <= 0:
        return "-"
    s = int(seconds)
    if s >= 3600:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"
    if s >= 60:
        m = s // 60
        sec = s % 60
        return f"{m}m {sec:02d}s"
    return f"{s}s"


def get_antigravity_paths(custom_data_dir: Optional[str] = None) -> Tuple[str, str, str]:
    """Resolves Antigravity core paths dynamically."""
    base_dir = (
        custom_data_dir
        or os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
        or os.path.expanduser("~/.gemini/antigravity-cli")
    )
    brain_dir = os.path.join(base_dir, "brain")
    log_dir = os.path.join(base_dir, "log")
    db_path = os.path.join(base_dir, "conversation_summaries.db")
    return brain_dir, log_dir, db_path


def get_parent_sessions(db_path: str) -> List[Dict[str, Any]]:
    """Retrieves all parent sessions that have spawned subagents."""
    if not os.path.isfile(db_path):
        return []
    parents = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("""
            SELECT 
                parent_conversation_id, 
                count(*) as sub_count, 
                max(last_modified_time) as last_active
            FROM conversation_summaries
            WHERE parent_conversation_id IS NOT NULL AND parent_conversation_id != ""
            GROUP BY parent_conversation_id
            ORDER BY max(last_modified_time) DESC
        """)
        rows = c.fetchall()
        for pid, cnt, last_act in rows:
            c.execute("SELECT title, preview FROM conversation_summaries WHERE conversation_id = ?", (pid,))
            p_row = c.fetchone()
            p_title = ""
            if p_row:
                p_title = p_row[0] or p_row[1] or ""
            parents.append({
                "parent_id": pid,
                "short_id": pid[:8],
                "subagent_count": cnt,
                "last_active": str(last_act)[:19],
                "title": p_title or "Untitled Session"
            })
        conn.close()
    except Exception:
        pass
    return parents


def resolve_target_session(db_path: str, session_arg: Optional[str]) -> Optional[str]:
    """Resolves target parent session ID via flag, env, or most recent parent session."""
    parents = get_parent_sessions(db_path)
    if not parents:
        return None

    if session_arg:
        arg_low = session_arg.lower().strip()
        for p in parents:
            if p["parent_id"].lower().startswith(arg_low):
                return p["parent_id"]
        return session_arg

    env_id = os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or os.environ.get("CONVERSATION_ID")
    if env_id:
        for p in parents:
            if p["parent_id"] == env_id:
                return env_id

    # Fallback to the most recent parent session with subagents
    return parents[0]["parent_id"]


def parse_main_transcript(parent_transcript_path: str) -> Dict[str, Any]:
    """Parses parent transcript to extract main session tokens, cache reads, and invocation parameters."""
    res = {
        "in_tokens": 0,
        "out_tokens": 0,
        "cache_tokens": 0,
        "cache_rate": 0.0,
        "total_tokens": 0,
        "steps": 0,
        "agent_invocations": {},
        "id_to_invocation": {},
    }
    if not os.path.isfile(parent_transcript_path):
        return res

    import re
    try:
        with open(parent_transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                res["steps"] += 1
                res["in_tokens"] += row.get("input_tokens", 0)
                res["out_tokens"] += row.get("output_tokens", 0)
                res["cache_tokens"] += row.get("cache_read_tokens", 0)

                for tc in row.get("tool_calls", []):
                    if tc.get("name") == "invoke_subagent":
                        args = tc.get("args") or {}
                        t_name = str(args.get("TypeName", "")).strip("\"'")
                        role = str(args.get("Role", "")).strip("\"'")
                        prompt = str(args.get("Prompt", "")).strip("\"'")
                        model = str(args.get("Model", "")).strip("\"'")
                        info = {"type_name": t_name, "role": role, "prompt": prompt, "model": model}
                        res["agent_invocations"][t_name] = info

                content = str(row.get("content") or "")
                if "conversationId" in content or "conversation_id" in content:
                    m_cid = re.search(r'["\']conversationId["\']\s*:\s*["\']([a-f0-9\-]{36})["\']', content)
                    if m_cid:
                        cid = m_cid.group(1)
                        m_role = re.search(r'["\']role["\']\s*:\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                        m_type = re.search(r'["\']typeName["\']\s*:\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                        res["id_to_invocation"][cid] = {
                            "role": m_role.group(1) if m_role else "",
                            "type_name": m_type.group(1) if m_type else "",
                        }
    except Exception:
        pass

    res["total_tokens"] = res["in_tokens"] + res["out_tokens"]
    m_in_tot = res["in_tokens"] + res["cache_tokens"]
    res["cache_rate"] = (res["cache_tokens"] / m_in_tot * 100.0) if m_in_tot > 0 else 0.0
    return res


def parse_subagent_transcript(sub_transcript_path: str) -> Dict[str, Any]:
    """Parses subagent transcript for granular token counts, cache tokens, turns, tools, and timestamps."""
    stats = {
        "in_tokens": 0,
        "out_tokens": 0,
        "cache_tokens": 0,
        "cache_rate": 0.0,
        "total_tokens": 0,
        "steps": 0,
        "turns": 0,
        "tools": {},
        "total_tool_calls": 0,
        "start_time": None,
        "end_time": None,
        "duration_seconds": 0.0,
        "final_summary": "",
    }
    if not os.path.isfile(sub_transcript_path):
        return stats

    first_dt = None
    last_dt = None

    try:
        with open(sub_transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                stats["steps"] += 1
                stats["in_tokens"] += row.get("input_tokens", 0)
                stats["out_tokens"] += row.get("output_tokens", 0)
                stats["cache_tokens"] += row.get("cache_read_tokens", 0)

                stype = row.get("type")
                if stype == "PLANNER_RESPONSE":
                    stats["turns"] += 1

                for tc in row.get("tool_calls", []):
                    tname = tc.get("name") if isinstance(tc, dict) else str(tc)
                    if tname:
                        stats["tools"][tname] = stats["tools"].get(tname, 0) + 1
                        stats["total_tool_calls"] += 1

                created_at = row.get("created_at")
                if created_at:
                    try:
                        dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if first_dt is None or dt < first_dt:
                            first_dt = dt
                        if last_dt is None or dt > last_dt:
                            last_dt = dt
                    except Exception:
                        pass

                if stype == "PLANNER_RESPONSE" and row.get("content"):
                    stats["final_summary"] = str(row.get("content"))
    except Exception:
        pass

    stats["total_tokens"] = stats["in_tokens"] + stats["out_tokens"]
    s_in_tot = stats["in_tokens"] + stats["cache_tokens"]
    stats["cache_rate"] = (stats["cache_tokens"] / s_in_tot * 100.0) if s_in_tot > 0 else 0.0
    stats["start_time"] = first_dt
    stats["end_time"] = last_dt
    if first_dt and last_dt:
        stats["duration_seconds"] = max(0.0, (last_dt - first_dt).total_seconds())

    return stats


def calculate_efficiency_metrics(
    subagents: List[Dict[str, Any]],
    main_total_tokens: int,
    total_sub_tokens: int
) -> None:
    """Computes relative shares, cache rates, and token efficiency indicators in-place."""
    overall_tokens = main_total_tokens + total_sub_tokens
    for sa in subagents:
        tin = sa["in_tokens"]
        tout = sa["out_tokens"]
        tot = sa["total_tokens"]
        tcache = sa.get("cache_tokens", 0)
        turns = sa["turns"]
        tool_cnt = sa["tool_calls_count"]
        dur = sa["duration_seconds"]

        in_total = tin + tcache
        sa["cache_rate"] = (tcache / in_total * 100.0) if in_total > 0 else 0.0
        sa["out_in_ratio"] = (tout / tin * 100.0) if tin > 0 else 0.0
        sa["tokens_per_turn"] = (tot / turns) if turns > 0 else tot
        sa["tools_per_turn"] = (tool_cnt / turns) if turns > 0 else 0.0
        sa["tokens_per_second"] = (tout / dur) if dur > 0 else 0.0
        sa["share_of_subagents"] = (tot / total_sub_tokens * 100.0) if total_sub_tokens > 0 else 0.0
        sa["share_of_session"] = (tot / overall_tokens * 100.0) if overall_tokens > 0 else 0.0


def aggregate_by_agent_type(subagents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregates multi-agent token metrics and cache statistics by subagent type."""
    groups: Dict[str, Dict[str, Any]] = {}
    for sa in subagents:
        tname = sa["name"]
        if tname not in groups:
            groups[tname] = {
                "type_name": tname,
                "count": 0,
                "in_tokens": 0,
                "out_tokens": 0,
                "cache_tokens": 0,
                "total_tokens": 0,
                "turns": 0,
                "tool_calls_count": 0,
                "duration_seconds": 0.0,
            }
        g = groups[tname]
        g["count"] += 1
        g["in_tokens"] += sa["in_tokens"]
        g["out_tokens"] += sa["out_tokens"]
        g["cache_tokens"] += sa.get("cache_tokens", 0)
        g["total_tokens"] += sa["total_tokens"]
        g["turns"] += sa["turns"]
        g["tool_calls_count"] += sa["tool_calls_count"]
        g["duration_seconds"] += sa["duration_seconds"]

    for g in groups.values():
        tin = g["in_tokens"]
        tout = g["out_tokens"]
        tcache = g["cache_tokens"]
        tot = g["total_tokens"]
        turns = g["turns"]
        cnt = g["count"]

        in_tot = tin + tcache
        g["avg_cache_rate"] = (tcache / in_tot * 100.0) if in_tot > 0 else 0.0
        g["avg_out_in_ratio"] = (tout / tin * 100.0) if tin > 0 else 0.0
        g["avg_tokens_per_agent"] = (tot / cnt) if cnt > 0 else tot
        g["avg_turns_per_agent"] = (turns / cnt) if cnt > 0 else turns
        g["avg_tokens_per_turn"] = (tot / turns) if turns > 0 else tot

    return groups


def rank_efficiency_highlights(subagents: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Identifies notable efficiency and cache leaders across multiple dimensions."""
    if not subagents:
        return {"heavyweight": None, "high_synthesis": None, "tool_intensive": None, "fastest": None, "highest_cache": None}

    heavyweight = max(subagents, key=lambda a: a["total_tokens"])

    synthesis_candidates = [a for a in subagents if a["out_tokens"] >= 500] or subagents
    high_synthesis = max(synthesis_candidates, key=lambda a: a["out_in_ratio"])

    tool_intensive = max(subagents, key=lambda a: a["tool_calls_count"])

    timed_subs = [a for a in subagents if a["duration_seconds"] > 0]
    fastest = min(timed_subs, key=lambda a: a["duration_seconds"]) if timed_subs else None

    highest_cache = max(subagents, key=lambda a: a.get("cache_rate", 0.0))

    return {
        "heavyweight": heavyweight,
        "high_synthesis": high_synthesis,
        "tool_intensive": tool_intensive,
        "fastest": fastest,
        "highest_cache": highest_cache,
    }


def audit_session_subagents(
    db_path: str,
    brain_dir: str,
    log_dir: str,
    target_parent_id: str
) -> Dict[str, Any]:
    """Gathers and computes complete subagent efficiency statistics for a given parent session."""
    main_transcript_path = os.path.join(brain_dir, target_parent_id, ".system_generated", "logs", "transcript.jsonl")
    main_meta = parse_main_transcript(main_transcript_path)

    parent_title = "Active Conversation"
    sub_rows = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("SELECT title, preview FROM conversation_summaries WHERE conversation_id = ?", (target_parent_id,))
        p_row = c.fetchone()
        if p_row and (p_row[0] or p_row[1]):
            parent_title = p_row[0] or p_row[1]

        c.execute("""
            SELECT 
                conversation_id, agent_name, status, not_fully_idle, killed, 
                step_count, last_modified_time, title, preview
            FROM conversation_summaries
            WHERE parent_conversation_id = ?
            ORDER BY last_modified_time ASC
        """, (target_parent_id,))
        sub_rows = c.fetchall()
        conn.close()
    except Exception:
        pass

    subagents = []
    total_sub_tokens = 0
    total_sub_in = 0
    total_sub_out = 0
    total_sub_cache = 0
    total_sub_tools = 0

    for s_id, a_name, status, not_idle, killed, step_count, mtime, title, preview in sub_rows:
        is_running = (status == "CASCADE_RUN_STATUS_RUNNING" or not_idle == 1)
        is_killed = (killed == 1)
        state_str = "RUNNING" if is_running else ("KILLED" if is_killed else "DONE")

        sub_t_path = os.path.join(brain_dir, s_id, ".system_generated", "logs", "transcript.jsonl")
        t_stats = parse_subagent_transcript(sub_t_path)

        inv_info = main_meta["id_to_invocation"].get(s_id) or main_meta["agent_invocations"].get(a_name, {})
        role = inv_info.get("role") or ""
        prompt = inv_info.get("prompt") or ""
        req_model = inv_info.get("model") or "inherit"

        task_desc = title or preview or prompt
        if task_desc:
            task_desc = task_desc.replace("\n", " ").strip()

        tin = t_stats["in_tokens"]
        tout = t_stats["out_tokens"]
        tcache = t_stats["cache_tokens"]
        tot = t_stats["total_tokens"]
        tool_cnt = t_stats["total_tool_calls"]

        total_sub_in += tin
        total_sub_out += tout
        total_sub_cache += tcache
        total_sub_tokens += tot
        total_sub_tools += tool_cnt

        subagents.append({
            "id": s_id,
            "short_id": s_id[:8],
            "name": a_name or "subagent",
            "role": role,
            "label": f"{a_name}: {role}" if role else (a_name or "subagent"),
            "status": state_str,
            "is_running": is_running,
            "task_desc": task_desc,
            "model": req_model,
            "in_tokens": tin,
            "out_tokens": tout,
            "cache_tokens": tcache,
            "cache_rate": t_stats.get("cache_rate", 0.0),
            "total_tokens": tot,
            "steps": t_stats["steps"],
            "turns": t_stats["turns"],
            "tools": t_stats["tools"],
            "tool_calls_count": tool_cnt,
            "duration_seconds": t_stats["duration_seconds"],
            "start_time": t_stats["start_time"].isoformat() if t_stats["start_time"] else None,
            "end_time": t_stats["end_time"].isoformat() if t_stats["end_time"] else None,
        })

    calculate_efficiency_metrics(subagents, main_meta["total_tokens"], total_sub_tokens)
    type_aggregates = aggregate_by_agent_type(subagents)
    highlights = rank_efficiency_highlights(subagents)

    overall_session_tokens = main_meta["total_tokens"] + total_sub_tokens
    sub_vs_main = (total_sub_tokens / main_meta["total_tokens"] * 100.0) if main_meta["total_tokens"] > 0 else 0.0

    s_in_all = total_sub_in + total_sub_cache
    sub_cache_rate = (total_sub_cache / s_in_all * 100.0) if s_in_all > 0 else 0.0

    overall_cache = main_meta["cache_tokens"] + total_sub_cache
    overall_in_all = (main_meta["in_tokens"] + total_sub_in) + overall_cache
    overall_cache_rate = (overall_cache / overall_in_all * 100.0) if overall_in_all > 0 else 0.0

    return {
        "parent_session_id": target_parent_id,
        "parent_title": parent_title,
        "main_session": main_meta,
        "subagents": subagents,
        "type_aggregates": type_aggregates,
        "highlights": highlights,
        "total_subagents_count": len(subagents),
        "total_subagents_in": total_sub_in,
        "total_subagents_out": total_sub_out,
        "total_subagents_cache": total_sub_cache,
        "subagent_cache_rate": sub_cache_rate,
        "total_subagents_tokens": total_sub_tokens,
        "total_subagents_tools": total_sub_tools,
        "overall_session_tokens": overall_session_tokens,
        "overall_cache_tokens": overall_cache,
        "overall_cache_rate": overall_cache_rate,
        "subagent_vs_main_ratio": sub_vs_main,
    }


def render_terminal_dashboard(data: Dict[str, Any], show_details: bool = False, sort_key: str = "tokens"):
    """Renders pixel-perfect, CJK-aligned terminal dashboard for multi-agent efficiency analysis."""
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    RESET = "\033[0m"
    DIM = "\033[2m"

    p_id = data["parent_session_id"]
    p_title = data["parent_title"]
    m_info = data["main_session"]
    subs = list(data["subagents"])
    tot_subs = data["total_subagents_count"]

    # Sorting
    if sort_key == "in":
        subs.sort(key=lambda a: a["in_tokens"], reverse=True)
    elif sort_key == "out":
        subs.sort(key=lambda a: a["out_tokens"], reverse=True)
    elif sort_key == "cache":
        subs.sort(key=lambda a: a.get("cache_rate", 0.0), reverse=True)
    elif sort_key == "ratio":
        subs.sort(key=lambda a: a["out_in_ratio"], reverse=True)
    elif sort_key == "turns":
        subs.sort(key=lambda a: a["turns"], reverse=True)
    elif sort_key == "tools":
        subs.sort(key=lambda a: a["tool_calls_count"], reverse=True)
    elif sort_key == "duration":
        subs.sort(key=lambda a: a["duration_seconds"], reverse=True)
    elif sort_key == "name":
        subs.sort(key=lambda a: a["name"])
    else:  # default tokens
        subs.sort(key=lambda a: a["total_tokens"], reverse=True)

    print(f"\n{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}  🤖 Antigravity 多代理 Token 與快取效率審計儀表板 (Multi-Agent Token & Cache Analyzer){RESET}")
    print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════{RESET}\n")

    # Header summary
    m_tot = format_tokens(m_info["total_tokens"])
    m_in = format_tokens(m_info["in_tokens"])
    m_out = format_tokens(m_info["out_tokens"])
    m_cache = format_tokens(m_info.get("cache_tokens", 0))
    m_crate = m_info.get("cache_rate", 0.0)

    s_tot = format_tokens(data["total_subagents_tokens"])
    s_in = format_tokens(data["total_subagents_in"])
    s_out = format_tokens(data["total_subagents_out"])
    s_cache = format_tokens(data["total_subagents_cache"])
    s_crate = data.get("subagent_cache_rate", 0.0)

    all_tot = format_tokens(data["overall_session_tokens"])
    all_crate = data.get("overall_cache_rate", 0.0)

    print(f"📌 {BOLD}目標 Session{RESET} : {CYAN}{p_id}{RESET} ({p_title})")
    tot_all_saved = format_tokens(data.get("overall_cache_tokens", 0))
    print(f"📊 {BOLD}總量分配{RESET}     : 主 Session {MAGENTA}{m_tot}{RESET} (in: {m_in} / out: {m_out} | 省下: {m_cache} [{m_crate:.1f}%]) ＋ 子代理 {CYAN}{s_tot}{RESET} (in: {s_in} / out: {s_out} | 省下: {s_cache} [{s_crate:.1f}%]) ＝ {BOLD}{all_tot}{RESET} (全場省下: {tot_all_saved} [{all_crate:.1f}%])")
    print(f"👥 {BOLD}子代理統計{RESET}   : 共 {BOLD}{tot_subs}{RESET} 隻代理 | 工具總調用: {BOLD}{data['total_subagents_tools']}{RESET} 次 | 子/主 Token 佔比: {BOLD}{data['subagent_vs_main_ratio']:.1f}%{RESET}\n")

    if not subs:
        print(f"{YELLOW}⚠️ 本 Session 尚未衍生任何 Subagent 調用。{RESET}\n")
        return

    # Table columns definition
    cols = [
        ("ID", 8, "left"),
        ("代理身份 (Agent : Role)", 30, "left"),
        ("狀態", 7, "left"),
        ("輸入 (In)", 10, "right"),
        ("輸出 (Out)", 10, "right"),
        ("總計 (Total)", 11, "right"),
        ("快取省下 (Saved)", 16, "right"),
        ("產出率 (Out/In)", 15, "right"),
        ("輪次", 6, "right"),
        ("工具調用", 10, "right"),
        ("子佔比", 8, "right"),
        ("耗時", 8, "right"),
    ]

    header_parts = [pad_cjk(name, w, align) for name, w, align in cols]
    print(f"{BOLD}{' | '.join(header_parts)}{RESET}")
    print("─" * 155)

    for a in subs:
        c_sid = f"{DIM}{pad_cjk(a['short_id'], 8, 'left')}{RESET}"
        c_label = f"{CYAN}{pad_cjk(a['label'], 30, 'left')}{RESET}"

        if a["status"] == "RUNNING":
            c_status = f"{YELLOW}{pad_cjk('RUNNING', 7, 'left')}{RESET}"
        elif a["status"] == "KILLED":
            c_status = f"{RED}{pad_cjk('KILLED', 7, 'left')}{RESET}"
        else:
            c_status = f"{GREEN}{pad_cjk('DONE', 7, 'left')}{RESET}"

        c_in = pad_cjk(format_tokens(a["in_tokens"]), 10, "right")
        c_out = pad_cjk(format_tokens(a["out_tokens"]), 10, "right")
        c_tot = f"{BOLD}{pad_cjk(format_tokens(a['total_tokens']), 11, 'right')}{RESET}"

        c_tok = format_tokens(a.get("cache_tokens", 0))
        c_rate_str = f"{c_tok} [{a['cache_rate']:.1f}%]"
        c_cache = pad_cjk(c_rate_str, 16, "right")

        ratio_val = f"{a['out_in_ratio']:.1f}%"
        if a["out_in_ratio"] >= 40.0:
            c_ratio = f"{GREEN}{pad_cjk(ratio_val, 15, 'right')}{RESET}"
        elif a["out_in_ratio"] >= 20.0:
            c_ratio = f"{CYAN}{pad_cjk(ratio_val, 15, 'right')}{RESET}"
        else:
            c_ratio = pad_cjk(ratio_val, 15, "right")

        c_turns = pad_cjk(str(a["turns"]), 6, "right")
        c_tools = pad_cjk(str(a["tool_calls_count"]), 10, "right")
        c_share = pad_cjk(f"{a['share_of_subagents']:.1f}%", 8, "right")
        c_dur = pad_cjk(format_duration(a["duration_seconds"]), 8, "right")

        print(f"{c_sid} | {c_label} | {c_status} | {c_in} | {c_out} | {c_tot} | {c_cache} | {c_ratio} | {c_turns} | {c_tools} | {c_share} | {c_dur}")

    print("─" * 155)

    # Type aggregation table
    type_aggs = data.get("type_aggregates", {})
    if len(type_aggs) >= 1:
        print(f"\n{BOLD}📑 代理類型效率矩陣 (Type Group Efficiency Breakdown):{RESET}")
        t_cols = [
            ("代理類型 (Type)", 22, "left"),
            ("衍生隻數", 8, "right"),
            ("累計 Token", 12, "right"),
            ("平均每隻 Token", 15, "right"),
            ("平均省下 (Saved)", 18, "right"),
            ("平均產出率 (Out/In)", 18, "right"),
            ("平均每輪 Token", 15, "right"),
            ("累計工具", 10, "right"),
        ]
        t_headers = [pad_cjk(n, w, a) for n, w, a in t_cols]
        print(f"{BOLD}{' | '.join(t_headers)}{RESET}")
        print("─" * 130)

        for tname, g in sorted(type_aggs.items(), key=lambda x: x[1]["total_tokens"], reverse=True):
            r_type = pad_cjk(tname, 22, "left")
            r_cnt = pad_cjk(str(g["count"]), 8, "right")
            r_tot = pad_cjk(format_tokens(g["total_tokens"]), 12, "right")
            r_avg_a = pad_cjk(format_tokens(int(g["avg_tokens_per_agent"])), 15, "right")
            avg_c_tok = format_tokens(int(g.get('cache_tokens', 0) / max(1, g['count'])))
            r_avg_c = pad_cjk(f"{avg_c_tok} [{g.get('avg_cache_rate', 0.0):.1f}%]", 18, "right")
            r_avg_r = pad_cjk(f"{g['avg_out_in_ratio']:.1f}%", 18, "right")
            r_avg_t = pad_cjk(format_tokens(int(g["avg_tokens_per_turn"])), 15, "right")
            r_tools = pad_cjk(str(g["tool_calls_count"]), 10, "right")
            print(f"{r_type} | {r_cnt} | {r_tot} | {r_avg_a} | {r_avg_c} | {r_avg_r} | {r_avg_t} | {r_tools}")
        print("─" * 130)

    # Efficiency Highlights
    hl = data.get("highlights", {})
    hw = hl.get("heavyweight")
    hs = hl.get("high_synthesis")
    ti = hl.get("tool_intensive")
    hc = hl.get("highest_cache")

    print(f"\n{BOLD}🏆 多代理效率觀測結論 (Efficiency Insights):{RESET}")
    if hw:
        print(f"  • {BOLD}最高 Token 消耗{RESET}   : {CYAN}[{hw['short_id']} {hw['label']}]{RESET} 消耗 {format_tokens(hw['total_tokens'])} (佔子代理 {hw['share_of_subagents']:.1f}%)")
    if hs:
        print(f"  • {BOLD}最高產出密度 (Out/In){RESET}: {GREEN}[{hs['short_id']} {hs['label']}]{RESET} 達 {hs['out_in_ratio']:.1f}% (產出 {format_tokens(hs['out_tokens'])} / 輸入 {format_tokens(hs['in_tokens'])})")
    if hc:
        print(f"  • {BOLD}最高快取節省 (Saved){RESET}: {CYAN}[{hc['short_id']} {hc['label']}]{RESET} 省下 {format_tokens(hc.get('cache_tokens', 0))} Tokens (快取率 {hc['cache_rate']:.1f}%)")
    if ti:
        print(f"  • {BOLD}最高工具密集度{RESET}     : {YELLOW}[{ti['short_id']} {ti['label']}]{RESET} 調用 {ti['tool_calls_count']} 次工具 ({format_tokens(ti['total_tokens'])} Tokens, {ti['turns']} 輪)")

    if show_details:
        print(f"\n{BOLD}🔍 工具與任務明細分佈 (Detailed Tool Usage & Task Descriptions):{RESET}")
        for sa in subs:
            print(f"\n  [{sa['short_id']}] {BOLD}{sa['label']}{RESET} ({sa['status']})")
            if sa["task_desc"]:
                print(f"    📝 任務說明: {sa['task_desc'][:90]}")
            c_tok = format_tokens(sa.get("cache_tokens", 0))
            print(f"    💾 快取命中: {c_tok} Tokens (快取命中率: {sa['cache_rate']:.1f}%)")
            if sa["tools"]:
                tool_items = [f"{t}: {c}次" for t, c in sorted(sa["tools"].items(), key=lambda x: x[1], reverse=True)]
                print(f"    🛠️ 工具調用: {', '.join(tool_items)}")
            else:
                print(f"    🛠️ 工具調用: 0 (純思考)")
            if sa["duration_seconds"] > 0:
                print(f"    ⏱️ 執行週期: {format_duration(sa['duration_seconds'])} (輸出速度: {sa['tokens_per_second']:.1f} tokens/s)")
    print()


def list_all_parent_sessions(db_path: str):
    """Lists all historical parent sessions that have spawned subagents."""
    parents = get_parent_sessions(db_path)
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    print(f"\n{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}  📋 具備子代理 (Subagents) 的歷史對話 Session 清單{RESET}")
    print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════════{RESET}\n")

    cols = [
        ("Session ID", 36, "left"),
        ("衍生代理數", 10, "right"),
        ("最後活躍時間", 19, "left"),
        ("主題 / 任務意圖", 35, "left"),
    ]
    headers = [pad_cjk(n, w, a) for n, w, a in cols]
    print(f"{BOLD}{' | '.join(headers)}{RESET}")
    print("─" * 110)

    for p in parents:
        c_id = pad_cjk(p["parent_id"], 36, "left")
        c_cnt = pad_cjk(f"{p['subagent_count']} 隻", 10, "right")
        c_time = pad_cjk(p["last_active"], 19, "left")
        c_title = pad_cjk(p["title"], 35, "left")
        print(f"{c_id} | {c_cnt} | {c_time} | {c_title}")

    print("─" * 110)
    print(f"💡 可使用指令：{CYAN}subagent-audit --session <Session ID>{RESET} 深入分析特定對話的多代理表現。\n")


def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Multi-Agent Token Efficiency Analyzer & Audit Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  subagent-audit                     # 審計當前活躍 Session 的所有子代理與快取率
  subagent-audit -s 6d5cdcf9         # 審計指定 Session ID 的子代理
  subagent-audit -v                  # 展開工具調用分佈與詳細任務說明
  subagent-audit --sort cache        # 按快取命中率 (Cache Rate) 排序
  subagent-audit --sort ratio        # 按產出率 (Out/In) 排序
  subagent-audit --list              # 列出所有包含子代理的 Session
  subagent-audit --json              # 輸出結構化 JSON 供後續分析管線使用
        """
    )
    parser.add_argument("-s", "--session", type=str, default=None, help="Target parent session ID (full UUID or prefix)")
    parser.add_argument("-l", "--list", action="store_true", help="List all parent sessions that have spawned subagents")
    parser.add_argument("-v", "--detail", action="store_true", help="Show granular tool breakdown and task prompts")
    parser.add_argument("--sort", type=str, default="tokens", choices=["tokens", "in", "out", "cache", "ratio", "turns", "tools", "duration", "name"], help="Sort metric (default: tokens)")
    parser.add_argument("--json", action="store_true", help="Output raw structured JSON for analysis pipelines")
    parser.add_argument("--app-data-dir", type=str, default=None, help="Custom Antigravity app data directory")

    args = parser.parse_args()
    brain_dir, log_dir, db_path = get_antigravity_paths(args.app_data_dir)

    if args.list:
        list_all_parent_sessions(db_path)
        return

    target_id = resolve_target_session(db_path, args.session)
    if not target_id:
        print("未找到包含子代理的對話 Session。")
        sys.exit(0)

    data = audit_session_subagents(db_path, brain_dir, log_dir, target_id)

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return

    render_terminal_dashboard(data, show_details=args.detail, sort_key=args.sort)


if __name__ == "__main__":
    main()
