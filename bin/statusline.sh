#!/usr/bin/env bash
PYTHON_BIN="${HOME}/gemini_py3_env/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(which python3 2>/dev/null || which python 2>/dev/null)"
fi

exec "$PYTHON_BIN" -c '
import sys, os, json, sqlite3, re

def format_tokens(n):
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

def format_reset_time(seconds):
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

def format_workspace(data):
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
            return "~"
        if len(parts) > 3:
            return ".../" + "/".join(parts[-3:])
        return "~/" + "/".join(parts)
    
    parts = [p for p in raw_ws.strip("/").split("/") if p]
    if len(parts) > 3:
        return ".../" + "/".join(parts[-3:])
    return "/" + "/".join(parts) if parts else "/"

def render_statusline(raw_input):
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

    conv_id = data.get("conversation_id") or data.get("session_id") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
    db_path = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")

    # 1. Main & Subagent Tokens + Subagents States
    main_in, main_out, main_cache = 0, 0, 0
    sub_in, sub_out, sub_cache = 0, 0, 0
    sub_count = 0
    agent_invocations = {}
    agent_stats = []

    tasks_started = {}
    tasks_ended = set()

    if conv_id and os.path.isdir(brain_dir):
        main_transcript = os.path.join(brain_dir, conv_id, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(main_transcript):
            try:
                with open(main_transcript, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                            main_in += row.get("input_tokens", 0)
                            main_out += row.get("output_tokens", 0)
                            main_cache += row.get("cache_read_tokens", 0)
                        except Exception:
                            continue

                        # Extract subagent metadata
                        if "invoke_subagent" in line:
                            for tc in row.get("tool_calls", []):
                                if tc.get("name") == "invoke_subagent":
                                    args = tc.get("args") or {}
                                    t_name = str(args.get("TypeName", "")).strip("\"'\''")
                                    role = str(args.get("Role", "")).strip("\"'\''")
                                    prompt = str(args.get("Prompt", "")).strip("\"'\''")
                                    agent_invocations[t_name] = {"role": role, "prompt": prompt}

                        # Extract background tasks
                        if "task-" in line:
                            content = row.get("content") or ""
                            m_start = re.search(r"task id:\s*([^\s\\]+/task-\d+)", content)
                            if m_start:
                                tid = m_start.group(1)
                                m_desc = re.search(r"Task Description:\s*([^\n\r]+)", content)
                                tasks_started[tid] = m_desc.group(1).strip() if m_desc else ""
                            m_end = re.search(r"Task id [\"\x27]([^\s\"\x27]+/task-\d+)[\"\x27]", content)
                            if m_end:
                                tasks_ended.add(m_end.group(1))
                            m_end2 = re.search(r"Task [\"\x27]([^\s\"\x27]+/task-\d+)[\"\x27] (?:cancelled|completed|finished)", content)
                            if m_end2:
                                tasks_ended.add(m_end2.group(1))
            except Exception:
                pass

        if os.path.isfile(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                c = conn.cursor()
                c.execute("SELECT conversation_id, agent_name, status, not_fully_idle, title, preview FROM conversation_summaries WHERE parent_conversation_id = ?", (conv_id,))
                rows = c.fetchall()
                sub_count = len(rows)
                for sub_id, a_name, status, not_idle, title, preview in rows:
                    is_running = (status == "CASCADE_RUN_STATUS_RUNNING" or not_idle == 1)
                    c_tin, c_tout, c_tcache = 0, 0, 0
                    c_transcript = os.path.join(brain_dir, sub_id, ".system_generated", "logs", "transcript.jsonl")
                    if os.path.isfile(c_transcript):
                        try:
                            with open(c_transcript, "r", encoding="utf-8", errors="ignore") as f:
                                for line in f:
                                    try:
                                        row = json.loads(line)
                                        c_tin += row.get("input_tokens", 0)
                                        c_tout += row.get("output_tokens", 0)
                                        c_tcache += row.get("cache_read_tokens", 0)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    sub_in += c_tin
                    sub_out += c_tout
                    sub_cache += c_tcache

                    inv = agent_invocations.get(a_name, {})
                    role = inv.get("role", "")
                    prompt = inv.get("prompt", "")

                    c_in_tot = c_tin + c_tcache
                    c_rate = (c_tcache / c_in_tot * 100.0) if c_in_tot > 0 else 0.0

                    agent_stats.append({
                        "id": sub_id,
                        "name": a_name or "subagent",
                        "role": role,
                        "prompt": prompt,
                        "title": title or preview or "",
                        "tokens": c_tin + c_tout,
                        "tin": c_tin,
                        "tout": c_tout,
                        "tcache": c_tcache,
                        "cache_rate": c_rate,
                        "is_running": is_running
                    })
                conn.close()
            except Exception:
                pass

    main_tokens = main_in + main_out
    sub_tokens = sub_in + sub_out
    total_session_tokens = main_tokens + sub_tokens

    m_in_tot = main_in + main_cache
    main_cache_rate = (main_cache / m_in_tot * 100.0) if m_in_tot > 0 else 0.0

    all_in_tot = (main_in + sub_in) + (main_cache + sub_cache)
    total_cache_rate = ((main_cache + sub_cache) / all_in_tot * 100.0) if all_in_tot > 0 else 0.0

    running_tasks = {tid: desc for tid, desc in tasks_started.items() if tid not in tasks_ended}
    total_tasks = len(tasks_started)
    if total_tasks == 0 and conv_id:
        t_dir = os.path.join(brain_dir, conv_id, ".system_generated", "tasks")
        if os.path.isdir(t_dir):
            try:
                total_tasks = len([f for f in os.listdir(t_dir) if f.endswith(".log")])
            except Exception:
                pass

    # 2. Model & Streamlined Quota (5h + Weekly)
    model = data.get("model") or {}
    model_id = (model.get("id") or model.get("display_name") or "").lower()
    model_name = model.get("display_name") or model.get("id") or "Agent"

    is_3p = any(k in model_id for k in ("claude", "gpt", "openai", "anthropic", "sonnet", "opus", "haiku"))

    quota = data.get("quota") or {}
    if is_3p:
        h_bucket = quota.get("3p-5h")
        w_bucket = quota.get("3p-weekly")
    else:
        h_bucket = quota.get("gemini-5h")
        w_bucket = quota.get("gemini-weekly")

    q_parts = []
    if h_bucket:
        try:
            pct = int(float(h_bucket.get("remaining_fraction", 1.0)) * 100)
            t = format_reset_time(h_bucket.get("reset_in_seconds"))
            q_parts.append(f"5h {pct}%" + (f" [{t}]" if t else ""))
        except Exception:
            pass

    if w_bucket:
        try:
            pct = int(float(w_bucket.get("remaining_fraction", 1.0)) * 100)
            t = format_reset_time(w_bucket.get("reset_in_seconds"))
            q_parts.append(f"Wk {pct}%" + (f" [{t}]" if t else ""))
        except Exception:
            pass

    quota_str = ""
    if q_parts:
        quota_str = "Quota: " + " | ".join(q_parts)

    # 3. Context Window
    ctx = data.get("context_window") or {}
    raw_pct = ctx.get("used_percentage")
    try:
        ctx_pct = float(raw_pct) if raw_pct is not None else 0.0
    except Exception:
        ctx_pct = 0.0

    inp_tokens = ctx.get("total_input_tokens")
    if inp_tokens is None:
        curr_usage = ctx.get("current_usage") or {}
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

    ws_str = format_workspace(data)

    # Line 1: Workspace, Model, Current Context Window, Shortcuts Hint
    shortcuts_hint = "\033[90m? for shortcuts\033[0m"
    line1 = f"[{ws_str}] [{model_name}] Context: {color}[{bar}] {ctx_pct:.1f}%{tokens_detail}{reset} | {shortcuts_hint}"

    # Line 2: Streamlined Quota, Session Cumulative Tokens, Process Counts
    tot_saved = main_cache + sub_cache
    if sub_tokens > 0:
        saved_str = f" | saved: {format_tokens(tot_saved)} [{total_cache_rate:.1f}%]" if tot_saved > 0 else ""
        token_str = f"Session: {format_tokens(total_session_tokens)} (主 {format_tokens(main_tokens)} / 子 {format_tokens(sub_tokens)}{saved_str})"
    elif main_tokens > 0:
        saved_str = f" | saved: {format_tokens(main_cache)} [{main_cache_rate:.1f}%]" if main_cache > 0 else ""
        token_str = f"Session: {format_tokens(total_session_tokens)} (in: {format_tokens(main_in)} / out: {format_tokens(main_out)}{saved_str})"
    else:
        token_str = f"Session: 0"

    r_t = len(running_tasks)
    r_a = sum(1 for a in agent_stats if a.get("is_running"))
    t_running_display = f"\033[33m{r_t}\033[0m" if r_t > 0 else str(r_t)
    a_running_display = f"\033[33m{r_a}\033[0m" if r_a > 0 else str(r_a)

    tasks_stat = f"Tasks: {t_running_display}/{total_tasks} | Agents: {a_running_display}/{sub_count}"

    line2_items = []
    if quota_str:
        line2_items.append(quota_str)
    line2_items.append(token_str)
    line2_items.append(tasks_stat)

    line2 = " | ".join(line2_items)

    output_lines = [line1, line2]

    # Line 3+: Breakdown of Main & Top 3 Subagents
    if agent_stats:
        # Main session is the primary coordinator
        m_saved_str = f" | saved: {format_tokens(main_cache)} [{main_cache_rate:.1f}%]" if main_cache > 0 else ""
        output_lines.append(f"  ├─ \033[35m[Main]\033[0m {format_tokens(main_tokens)} (in: {format_tokens(main_in)} / out: {format_tokens(main_out)}{m_saved_str})")

        # Sort descending by total tokens consumed
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

            # If 3rd item and there are more agents, append summary of the rest
            more_suffix = ""
            if i == 2 and total_a_count > 3:
                more_count = total_a_count - 3
                more_toks = sum(x.get("tokens", 0) for x in sorted_agents[3:])
                more_suffix = f" \033[90m(+{more_count} 隻累計 {format_tokens(more_toks)})\033[0m"

            is_last = (i == len(top3) - 1)
            tree_char = "  └─" if is_last else "  ├─"

            crate = a.get("cache_rate", 0.0)
            c_saved = a.get("tcache", 0)
            c_rate_str = f" | saved: {format_tokens(c_saved)} [{crate:.1f}%]" if c_saved > 0 else ""
            output_lines.append(f"{tree_char} \033[36m[{label}]\033[0m {tok} (in: {tin} / out: {tout}{c_rate_str}){status_tag}{more_suffix}")

    return "\n".join(output_lines)

try:
    content = sys.stdin.read()
    print(render_statusline(content))
except Exception:
    print("[Workspace] [Agent] Context: [----------] 0.0%\nSession: 0 | Tasks: 0/0 | Agents: 0/0")
'
