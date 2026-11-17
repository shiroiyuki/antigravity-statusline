#!/usr/bin/env python3
"""
Antigravity Statusline Render Engine
Supports both legacy Antigravity payloads and AGY 1.5.9+ StatusLineData fields.
"""

import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple


UUID_CID_RE = re.compile(r'["\']conversationId["\']\s*:\s*["\']([a-f0-9\-]{36})["\']')
ROLE_RE = re.compile(r'["\']role["\']\s*:\s*["\']([^"\']+)["\']', re.IGNORECASE)
TYPE_RE = re.compile(r'["\']typeName["\']\s*:\s*["\']([^"\']+)["\']', re.IGNORECASE)
TASK_START_RE = re.compile(r"task id:\s*([^\s\\]+/task-\d+)")
TASK_DESC_RE = re.compile(r"Task Description:\s*([^\n\r]+)")
TASK_END_RE = re.compile(r"Task id [\"']([^\s\"']+/task-\d+)[\"']")
TASK_END2_RE = re.compile(r"Task [\"']([^\s\"']+/task-\d+)[\"'] (?:cancelled|completed|finished)")


def format_tokens(n: Any) -> str:
    """Formats raw integer token counts into compact human-readable form."""
    if n is None:
        return "0"
    try:
        val = int(n)
        if val >= 1_000_000:
            return f"{val / 1_000_000:.2f}M"
        if val >= 1_000:
            return f"{val / 1_000:.1f}k"
        return str(val)
    except Exception:
        return str(n)


def format_reset_time(seconds: Any) -> str:
    """Formats countdown seconds into compact d/h/m text."""
    try:
        s = int(seconds)
        if s <= 0:
            return ""
        if s >= 86400:
            d = s // 86400
            h = (s % 86400) // 3600
            return f"{d}d{h}h"
        if s >= 3600:
            h = s // 3600
            m = (s % 3600) // 60
            return f"{h}h{m}m"
        return f"{s // 60}m"
    except Exception:
        return ""


def resolve_app_data_dir(custom_dir: Optional[str] = None) -> str:
    """Resolves Antigravity CLI app data directory dynamically."""
    return (
        custom_dir
        or os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
        or os.path.expanduser("~/.gemini/antigravity-cli")
    )


def format_workspace(data: Dict[str, Any]) -> str:
    """Formats workspace path concisely with optional git branch/dirty badge."""
    raw_ws = None
    ws_dict = data.get("workspace")
    if isinstance(ws_dict, dict):
        raw_ws = ws_dict.get("current_dir") or ws_dict.get("project_dir") or ws_dict.get("path")
    elif isinstance(ws_dict, str):
        raw_ws = ws_dict

    if not raw_ws:
        raw_ws = data.get("cwd") or os.environ.get("WORKSPACE") or os.getcwd()

    raw_ws = str(raw_ws or "").strip()
    if raw_ws.startswith("file://"):
        raw_ws = raw_ws[7:]

    home = os.path.expanduser("~")
    if home and raw_ws.startswith(home):
        rel = os.path.relpath(raw_ws, home)
        parts = [p for p in rel.split(os.sep) if p and p != "."]
        if not parts:
            ws_label = "~"
        elif len(parts) > 3:
            ws_label = ".../" + "/".join(parts[-3:])
        else:
            ws_label = "~/" + "/".join(parts)
    else:
        parts = [p for p in raw_ws.strip("/").split("/") if p]
        if len(parts) > 3:
            ws_label = ".../" + "/".join(parts[-3:])
        else:
            ws_label = "/" + "/".join(parts) if parts else "/"

    vcs = data.get("vcs") if isinstance(data.get("vcs"), dict) else {}
    branch = data.get("branch") or vcs.get("branch") or (ws_dict.get("branch") if isinstance(ws_dict, dict) else "")
    dirty = data.get("dirty") if "dirty" in data else vcs.get("dirty")
    if branch:
        dirty_mark = "*" if dirty else ""
        return f"{ws_label} ({branch}{dirty_mark})"
    return ws_label


def resolve_transcript_path(data: Dict[str, Any], conv_id: Optional[str], brain_dir: str) -> Optional[str]:
    """Resolves parent transcript path, preferring AGY 1.5.9+ transcript_path field."""
    explicit_path = data.get("transcript_path")
    if explicit_path and os.path.isfile(str(explicit_path)):
        return str(explicit_path)
    if conv_id and os.path.isdir(brain_dir):
        candidate = os.path.join(brain_dir, conv_id, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(candidate):
            return candidate
    return None


def parse_main_transcript(transcript_path: Optional[str]) -> Dict[str, Any]:
    """Parses parent transcript for tokens, subagent invocations, and background tasks."""
    res: Dict[str, Any] = {
        "in_tokens": 0,
        "out_tokens": 0,
        "cache_tokens": 0,
        "last_model": "",
        "agent_invocations": {},
        "id_to_invocation": {},
        "tasks_started": {},
        "tasks_ended": set(),
    }
    if not transcript_path or not os.path.isfile(transcript_path):
        return res

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                res["in_tokens"] += row.get("input_tokens", 0) or 0
                res["out_tokens"] += row.get("output_tokens", 0) or 0
                res["cache_tokens"] += row.get("cache_read_tokens", 0) or 0
                if row.get("model"):
                    res["last_model"] = str(row["model"])

                if "invoke_subagent" in line:
                    for tc in row.get("tool_calls", []):
                        if tc.get("name") == "invoke_subagent":
                            args = tc.get("args") or {}
                            t_name = str(args.get("TypeName", "")).strip("\"'")
                            role = str(args.get("Role", "")).strip("\"'")
                            prompt = str(args.get("Prompt", "")).strip("\"'")
                            model = str(args.get("Model", "")).strip("\"'")
                            res["agent_invocations"][t_name] = {
                                "role": role,
                                "prompt": prompt,
                                "model": model,
                            }

                content = str(row.get("content") or "")
                if res["agent_invocations"] and ("conversationId" in content or "conversation_id" in content):
                    for m_cid in UUID_CID_RE.finditer(content):
                        cid = m_cid.group(1)
                        m_role = ROLE_RE.search(content)
                        m_type = TYPE_RE.search(content)
                        res["id_to_invocation"][cid] = {
                            "role": m_role.group(1) if m_role else "",
                            "type_name": m_type.group(1) if m_type else "",
                        }

                if "task-" in content:
                    m_start = TASK_START_RE.search(content)
                    if m_start:
                        tid = m_start.group(1)
                        m_desc = TASK_DESC_RE.search(content)
                        res["tasks_started"][tid] = m_desc.group(1).strip() if m_desc else ""
                    m_end = TASK_END_RE.search(content)
                    if m_end:
                        res["tasks_ended"].add(m_end.group(1))
                    m_end2 = TASK_END2_RE.search(content)
                    if m_end2:
                        res["tasks_ended"].add(m_end2.group(1))
    except Exception:
        pass

    return res


def _read_subagent_tokens(sub_transcript_path: str) -> Tuple[int, int, int]:
    """Reads token totals from a subagent transcript.jsonl file."""
    c_tin, c_tout, c_tcache = 0, 0, 0
    if not os.path.isfile(sub_transcript_path):
        return c_tin, c_tout, c_tcache
    try:
        with open(sub_transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    c_tin += row.get("input_tokens", 0) or 0
                    c_tout += row.get("output_tokens", 0) or 0
                    c_tcache += row.get("cache_read_tokens", 0) or 0
                except Exception:
                    pass
    except Exception:
        pass
    return c_tin, c_tout, c_tcache


def collect_subagents(
    conv_id: Optional[str],
    brain_dir: str,
    db_path: str,
    main_meta: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """Collects subagent stats from SQLite DB with transcript invocation fallback."""
    agent_stats: List[Dict[str, Any]] = []
    seen_ids = {conv_id} if conv_id else set()
    sub_in, sub_out, sub_cache = 0, 0, 0
    agent_invocations = main_meta.get("agent_invocations") or {}
    id_to_invocation = main_meta.get("id_to_invocation") or {}

    if conv_id and os.path.isfile(db_path):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute(
                "SELECT conversation_id, agent_name, status, not_fully_idle, title, preview "
                "FROM conversation_summaries WHERE parent_conversation_id = ?",
                (conv_id,),
            )
            rows = c.fetchall()
            conn.close()

            for sub_id, a_name, status, not_idle, title, preview in rows:
                seen_ids.add(sub_id)
                is_running = status == "CASCADE_RUN_STATUS_RUNNING" or not_idle == 1
                sub_transcript = os.path.join(brain_dir, sub_id, ".system_generated", "logs", "transcript.jsonl")
                c_tin, c_tout, c_tcache = _read_subagent_tokens(sub_transcript)
                sub_in += c_tin
                sub_out += c_tout
                sub_cache += c_tcache

                inv = id_to_invocation.get(sub_id) or agent_invocations.get(a_name, {})
                c_in_tot = c_tin + c_tcache
                c_rate = (c_tcache / c_in_tot * 100.0) if c_in_tot > 0 else 0.0

                agent_stats.append({
                    "id": sub_id,
                    "name": a_name or inv.get("type_name") or "subagent",
                    "role": inv.get("role", ""),
                    "prompt": inv.get("prompt", ""),
                    "title": title or preview or "",
                    "tokens": c_tin + c_tout,
                    "tin": c_tin,
                    "tout": c_tout,
                    "tcache": c_tcache,
                    "cache_rate": c_rate,
                    "is_running": is_running,
                })
        except Exception:
            pass

    # Fallback: discover subagents recorded in parent transcript if DB is absent/unflushed
    if os.path.isdir(brain_dir):
        for sub_id, inv in id_to_invocation.items():
            if sub_id in seen_ids:
                continue
            sub_transcript = os.path.join(brain_dir, sub_id, ".system_generated", "logs", "transcript.jsonl")
            if not os.path.isfile(sub_transcript):
                continue
            seen_ids.add(sub_id)
            c_tin, c_tout, c_tcache = _read_subagent_tokens(sub_transcript)
            sub_in += c_tin
            sub_out += c_tout
            sub_cache += c_tcache

            a_name = inv.get("type_name") or "subagent"
            role = inv.get("role") or agent_invocations.get(a_name, {}).get("role", "")
            c_in_tot = c_tin + c_tcache
            c_rate = (c_tcache / c_in_tot * 100.0) if c_in_tot > 0 else 0.0

            agent_stats.append({
                "id": sub_id,
                "name": a_name,
                "role": role,
                "prompt": agent_invocations.get(a_name, {}).get("prompt", ""),
                "title": "",
                "tokens": c_tin + c_tout,
                "tin": c_tin,
                "tout": c_tout,
                "tcache": c_tcache,
                "cache_rate": c_rate,
                "is_running": False,
            })

    return agent_stats, sub_in, sub_out, sub_cache


def _normalize_quota_map(quota_raw: Any) -> Dict[str, Dict[str, Any]]:
    """Normalizes dict or list quota payloads into {bucket_id: bucket_dict}."""
    if isinstance(quota_raw, dict):
        return {str(k): v for k, v in quota_raw.items() if isinstance(v, dict)}
    if isinstance(quota_raw, list):
        mapped: Dict[str, Dict[str, Any]] = {}
        for idx, item in enumerate(quota_raw):
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("quota_bucket")
                or item.get("quota_bucket_name")
                or item.get("quota_id")
                or item.get("id")
                or item.get("name")
                or f"bucket_{idx}"
            )
            mapped[key] = item
        return mapped
    return {}


def _extract_bucket_pct(bucket: Dict[str, Any]) -> Optional[int]:
    """Extracts integer remaining percentage from remaining_percentage or remaining_fraction."""
    if bucket.get("remaining_percentage") is not None:
        try:
            return max(0, min(100, int(round(float(bucket["remaining_percentage"])))))
        except Exception:
            pass
    if bucket.get("remaining_fraction") is not None:
        try:
            return max(0, min(100, int(float(bucket["remaining_fraction"]) * 100)))
        except Exception:
            pass
    return None


POOL_LABELS = {
    "gemini": "Gemini",
    "argon": "Argon",
    "claude": "Claude",
}
POOL_ORDER = ("gemini", "argon", "claude")
PRO_WORD_RE = re.compile(r"(?:^|[-_.\s/])pro(?:$|[-_.\s/])", re.IGNORECASE)
CLAUDE_3P_KEYWORDS = ("3p", "claude", "sonnet", "opus", "haiku", "anthropic", "openai", "gpt")


def _detect_active_pool(model_id: str) -> str:
    """Determines which quota pool corresponds to the active model."""
    ml = model_id.lower()
    if any(k in ml for k in CLAUDE_3P_KEYWORDS):
        return "claude"
    if "argon" in ml or "carbon" in ml or PRO_WORD_RE.search(ml):
        return "argon"
    return "gemini"


def _classify_quota_pool(bucket_key: str, bucket: Dict[str, Any]) -> str:
    """Classifies a quota bucket into gemini, argon, claude, or custom pool name."""
    probe = " ".join(
        str(bucket.get(field) or "")
        for field in ("quota_bucket", "quota_bucket_name", "display_name", "name", "label")
    )
    text = f"{bucket_key} {probe}".lower()

    if any(k in text for k in CLAUDE_3P_KEYWORDS):
        return "claude"
    if "argon" in text or "carbon" in text or PRO_WORD_RE.search(text):
        return "argon"
    if any(k in text for k in ("gemini", "flash", "hydrogen", "1p")):
        return "gemini"

    base = re.sub(r"[-_.\s]*(?:5h|5-h|5_h|weekly|week|7d|7-d)[-_.\s]*", "", bucket_key.lower()).strip("-_ ")
    return base or "quota"


def _pct_color(pct: int) -> str:
    """Returns ANSI color code for remaining quota percentage."""
    if pct < 20:
        return "\033[31m"
    if pct < 50:
        return "\033[33m"
    return "\033[32m"


def _format_pool_entry(
    pool_id: str,
    buckets: Dict[str, Dict[str, Any]],
    is_active: bool,
    multi_pool: bool,
) -> str:
    """Formats one quota pool cleanly for human readability."""
    h_bucket = buckets.get("5h")
    w_bucket = buckets.get("weekly")

    parts = []
    h_has_reset = False
    if h_bucket:
        pct = _extract_bucket_pct(h_bucket)
        if pct is not None:
            t = format_reset_time(h_bucket.get("reset_in_seconds")) or str(h_bucket.get("reset_time") or "")
            h_has_reset = bool(t)
            color = _pct_color(pct)
            parts.append(f"5h {color}{pct}%\033[0m" + (f" [{t}]" if t else ""))

    if w_bucket:
        pct = _extract_bucket_pct(w_bucket)
        if pct is not None:
            t = format_reset_time(w_bucket.get("reset_in_seconds")) or str(w_bucket.get("reset_time") or "")
            color = _pct_color(pct)
            show_w_reset = (not multi_pool) or (not h_has_reset)
            parts.append(f"Wk {color}{pct}%\033[0m" + (f" [{t}]" if (t and show_w_reset) else ""))

    if not parts:
        return ""

    label = POOL_LABELS.get(pool_id, pool_id.capitalize())
    if multi_pool and is_active:
        label_str = f"\033[1;36m*{label}\033[0m"
    elif multi_pool:
        label_str = f"\033[1m{label}\033[0m"
    else:
        label_str = label

    return f"{label_str} {' · '.join(parts)}"


def format_quota_segment(data: Dict[str, Any], active_pool: str) -> Tuple[str, bool]:
    """Formats single or multi-pool (Gemini / Argon / Claude) quota display.

    Returns (formatted_quota_string, is_multi_pool).
    """
    quota_map = _normalize_quota_map(data.get("quota"))
    if not quota_map:
        return "", False

    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for key, bucket in quota_map.items():
        pool_id = _classify_quota_pool(key, bucket)
        kl = key.lower()
        probe = str(bucket.get("window") or bucket.get("period") or "").lower()
        win_text = f"{kl} {probe}"

        if any(w in win_text for w in ("weekly", "week", "7d", "7-d")):
            win = "weekly"
        else:
            win = "5h"

        grouped.setdefault(pool_id, {})
        if win not in grouped[pool_id]:
            grouped[pool_id][win] = bucket

    ordered_pools = [p for p in POOL_ORDER if p in grouped] + [p for p in grouped if p not in POOL_ORDER]
    multi_pool = len(ordered_pools) > 1

    entries = []
    for pool_id in ordered_pools:
        entry = _format_pool_entry(
            pool_id,
            grouped[pool_id],
            is_active=(pool_id == active_pool),
            multi_pool=multi_pool,
        )
        if entry:
            entries.append(entry)

    if not entries:
        return "", False

    sep = "  |  " if multi_pool else " | "
    return "Quota: " + sep.join(entries), multi_pool


def format_model_label(data: Dict[str, Any], last_model: str) -> Tuple[str, str]:
    """Resolves display model label (with effort if not redundant) and active quota pool."""
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    model_id = str(model.get("id") or model.get("display_name") or last_model or "").lower()
    model_name = str(model.get("display_name") or model.get("id") or last_model or "Agent")

    effort = str(data.get("effort") or model.get("effort") or "").strip()
    if effort and effort.lower() not in model_name.lower():
        model_name = f"{model_name} ({effort})"

    return model_name, _detect_active_pool(model_id)


def format_cost_segment(data: Dict[str, Any]) -> str:
    """Formats optional session/subagent USD cost exposed in AGY 1.5.9+ payloads."""
    cost_obj = data.get("cost")
    total_usd = None
    if isinstance(cost_obj, dict):
        for k in ("total_cost_usd", "total", "estimated", "usd"):
            if cost_obj.get(k) is not None:
                try:
                    total_usd = float(cost_obj[k])
                    break
                except Exception:
                    pass
    elif isinstance(cost_obj, (int, float)):
        total_usd = float(cost_obj)

    sub_usd = None
    if data.get("subagent_usd") is not None:
        try:
            sub_usd = float(data["subagent_usd"])
        except Exception:
            pass

    if total_usd is not None and total_usd > 0:
        return f"Cost: ${total_usd:.2f}"
    if sub_usd is not None and sub_usd > 0:
        return f"Sub: ${sub_usd:.2f}"
    return ""


def render_statusline(raw_input: Any, app_data_dir: Optional[str] = None) -> str:
    """Renders multi-line Antigravity terminal statusline from stdin JSON and local logs."""
    try:
        if isinstance(raw_input, (str, bytes)):
            data = json.loads(raw_input)
        elif hasattr(raw_input, "read"):
            data = json.load(raw_input)
        elif isinstance(raw_input, dict):
            data = raw_input
        else:
            data = {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    conv_id = (
        data.get("conversation_id")
        or data.get("session_id")
        or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    )
    base_dir = resolve_app_data_dir(app_data_dir)
    brain_dir = os.path.join(base_dir, "brain")
    db_path = os.path.join(base_dir, "conversation_summaries.db")

    transcript_path = resolve_transcript_path(data, conv_id, brain_dir)
    main_meta = parse_main_transcript(transcript_path)

    main_in = main_meta["in_tokens"]
    main_out = main_meta["out_tokens"]
    main_cache = main_meta["cache_tokens"]

    agent_stats, sub_in, sub_out, sub_cache = collect_subagents(conv_id, brain_dir, db_path, main_meta)
    sub_count = len(agent_stats)

    main_tokens = main_in + main_out
    sub_tokens = sub_in + sub_out
    total_session_tokens = main_tokens + sub_tokens

    m_in_tot = main_in + main_cache
    main_cache_rate = (main_cache / m_in_tot * 100.0) if m_in_tot > 0 else 0.0

    all_in_tot = (main_in + sub_in) + (main_cache + sub_cache)
    total_cache_rate = ((main_cache + sub_cache) / all_in_tot * 100.0) if all_in_tot > 0 else 0.0

    tasks_started = main_meta["tasks_started"]
    tasks_ended = main_meta["tasks_ended"]
    running_tasks = {tid: desc for tid, desc in tasks_started.items() if tid not in tasks_ended}
    total_tasks = len(tasks_started)
    if total_tasks == 0 and conv_id:
        t_dir = os.path.join(brain_dir, conv_id, ".system_generated", "tasks")
        if os.path.isdir(t_dir):
            try:
                total_tasks = len([f for f in os.listdir(t_dir) if f.endswith(".log")])
            except Exception:
                pass

    model_name, active_pool = format_model_label(data, main_meta.get("last_model", ""))
    quota_str, multi_quota = format_quota_segment(data, active_pool)

    # Context Window
    ctx = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    raw_pct = ctx.get("used_percentage")
    if raw_pct is None and ctx.get("remaining_percentage") is not None:
        try:
            raw_pct = 100.0 - float(ctx["remaining_percentage"])
        except Exception:
            raw_pct = None
    try:
        ctx_pct = max(0.0, min(100.0, float(raw_pct))) if raw_pct is not None else 0.0
    except Exception:
        ctx_pct = 0.0

    inp_tokens = ctx.get("total_input_tokens")
    if inp_tokens is None:
        curr_usage = ctx.get("current_usage") if isinstance(ctx.get("current_usage"), dict) else {}
        inp_tokens = curr_usage.get("input_tokens")

    ctx_size = ctx.get("context_window_size")
    filled = min(10, max(0, int(ctx_pct / 10)))
    bar = "=" * filled + "-" * (10 - filled)
    color = "\033[31m" if ctx_pct >= 85 else ("\033[33m" if ctx_pct >= 60 else "\033[32m")
    reset = "\033[0m"

    tokens_detail = ""
    cache_tag = f" | Cache {total_cache_rate:.1f}%"
    if inp_tokens is not None and ctx_size:
        tokens_detail = f" ({format_tokens(inp_tokens)}/{format_tokens(ctx_size)}{cache_tag})"
    elif total_cache_rate > 0:
        tokens_detail = f" (Cache {total_cache_rate:.1f}%)"

    exceeds_tag = " \033[33m!200k\033[0m" if ctx.get("exceeds_200k_tokens") else ""

    ws_str = format_workspace(data)
    shortcuts_hint = "\033[90m? for shortcuts\033[0m"
    line1 = (
        f"[{ws_str}] [{model_name}] "
        f"Context: {color}[{bar}] {ctx_pct:.1f}%{tokens_detail}{reset}{exceeds_tag} | {shortcuts_hint}"
    )

    tot_saved = main_cache + sub_cache
    if sub_tokens > 0:
        saved_str = f" | saved: {format_tokens(tot_saved)} [{total_cache_rate:.1f}%]" if tot_saved > 0 else ""
        token_str = (
            f"Session: {format_tokens(total_session_tokens)} "
            f"(主 {format_tokens(main_tokens)} / 子 {format_tokens(sub_tokens)}{saved_str})"
        )
    elif main_tokens > 0:
        saved_str = f" | saved: {format_tokens(main_cache)} [{main_cache_rate:.1f}%]" if main_cache > 0 else ""
        token_str = (
            f"Session: {format_tokens(total_session_tokens)} "
            f"(in: {format_tokens(main_in)} / out: {format_tokens(main_out)}{saved_str})"
        )
    else:
        token_str = "Session: 0"

    r_t = len(running_tasks)
    r_a = sum(1 for a in agent_stats if a.get("is_running"))
    t_running_display = f"\033[33m{r_t}\033[0m" if r_t > 0 else str(r_t)
    a_running_display = f"\033[33m{r_a}\033[0m" if r_a > 0 else str(r_a)

    tasks_stat = f"Tasks: {t_running_display}/{total_tasks} | Agents: {a_running_display}/{sub_count}"

    output_lines = [line1]
    line2_items = []
    if quota_str and multi_quota:
        output_lines.append(quota_str)
    elif quota_str:
        line2_items.append(quota_str)
    line2_items.append(token_str)
    cost_str = format_cost_segment(data)
    if cost_str:
        line2_items.append(cost_str)
    line2_items.append(tasks_stat)

    line2 = " | ".join(line2_items)
    output_lines.append(line2)

    if agent_stats:
        m_saved_str = f" | saved: {format_tokens(main_cache)} [{main_cache_rate:.1f}%]" if main_cache > 0 else ""
        output_lines.append(
            f"  ├─ \033[35m[Main]\033[0m {format_tokens(main_tokens)} "
            f"(in: {format_tokens(main_in)} / out: {format_tokens(main_out)}{m_saved_str})"
        )

        sorted_agents = sorted(agent_stats, key=lambda a: a.get("tokens", 0), reverse=True)
        top3 = sorted_agents[:3]
        total_a_count = len(sorted_agents)

        for i, a in enumerate(top3):
            name = a.get("name", "agent")
            role = a.get("role", "")
            label = f"{name}: {role}" if role else name
            tok = format_tokens(a["tokens"])
            tin = format_tokens(a["tin"])
            tout = format_tokens(a["tout"])
            status_tag = " \033[33m(RUNNING)\033[0m" if a.get("is_running") else ""

            more_suffix = ""
            if i == 2 and total_a_count > 3:
                more_count = total_a_count - 3
                more_toks = sum(x.get("tokens", 0) for x in sorted_agents[3:])
                more_suffix = f" \033[90m(+{more_count} 隻累計 {format_tokens(more_toks)})\033[0m"

            is_last = i == len(top3) - 1
            tree_char = "  └─" if is_last else "  ├─"

            crate = a.get("cache_rate", 0.0)
            c_saved = a.get("tcache", 0)
            c_rate_str = f" | saved: {format_tokens(c_saved)} [{crate:.1f}%]" if c_saved > 0 else ""
            output_lines.append(
                f"{tree_char} \033[36m[{label}]\033[0m {tok} "
                f"(in: {tin} / out: {tout}{c_rate_str}){status_tag}{more_suffix}"
            )

    return "\n".join(output_lines)


def main() -> None:
    try:
        content = sys.stdin.read()
        print(render_statusline(content))
    except Exception:
        print("[Workspace] [Agent] Context: [----------] 0.0%\nSession: 0 | Tasks: 0/0 | Agents: 0/0")


if __name__ == "__main__":
    main()
