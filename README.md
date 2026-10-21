# Antigravity Statusline & Multi-Agent Telemetry Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-brightgreen.svg)](#)
[![Shell: Bash](https://img.shields.io/badge/Shell-Bash-orange.svg)](#)
[![Zero-Hardcode](https://img.shields.io/badge/Engineering-Zero--Hardcode-success.svg)](#)
[![CJK: Aligned](https://img.shields.io/badge/Terminal-CJK--Aligned-blueviolet.svg)](#)

A high-performance, real-time terminal statusline and multi-agent token telemetry engine tailored for [Google Antigravity (AGY)](https://github.com/google/antigravity).

Features dynamic provider quota switching (Gemini vs. Claude 3P), subagent process tracking, prompt cache savings calculation, and hierarchical multi-agent token breakdown.

---

## 📸 Preview

### Multi-Agent Mode (Subagents Active)

```text
[~/arch_test] [Gemini 3.8 Flash (High)] Context: [=---------] 11.0% (115.2k/1.05M | Cache 95.0%) | ? for shortcuts
Quota: 5h 94% [4h35m] | Wk 28% [3d23h] | Session: 1.55M (主 1.48M / 子 65.6k | saved: 25.92M [95.0%]) | Tasks: 0/4 | Agents: 0/1
  ├─ [Main] 1.48M (in: 1.30M / out: 177.9k | saved: 25.85M [95.2%])
  └─ [architect: JPEG Codec Architect] 65.6k (in: 50.9k / out: 14.7k | saved: 65.6k [56.3%])
```

### Single Agent Mode (Clean 2-Line Layout)

```text
[~/arch_test] [Gemini 3.8 Flash (High)] Context: [=---------] 11.0% (115.2k/1.05M | Cache 95.0%) | ? for shortcuts
Quota: 5h 94% [4h35m] | Wk 28% [3d23h] | Session: 1.48M (in: 1.30M / out: 177.9k | saved: 25.85M [95.2%]) | Tasks: 0/0 | Agents: 0/0
```

---

## ✨ Key Features

1. **Dynamic Model & Quota Tracking**:
   - Automatically switches between **Google Gemini** (`gemini-5h`, `gemini-weekly`) and **Anthropic Claude / 3P** (`3p-5h`, `3p-weekly`) quota buckets depending on the active model.
   - Streamlined display with time-to-reset countdowns (e.g. `[4h35m]`, `[3d23h]`).

2. **Hierarchical Multi-Agent Token Tree**:
   - Live process indicators: `Tasks: running/total | Agents: running/total`.
   - Displays Main session coordinator and **Top 3 Token-Consuming Subagents** with granular `(in / out)` counts.
   - If more than 3 subagents exist, summarizes remaining agents without cluttering terminal space (`(+N 隻累計 ...)`).

3. **Prompt Cache Savings & Hit Rate (快取節省量與命中率)**:
   - Tracks actual tokens saved via prompt caching and computes exact hit rates (`saved: 25.92M [95.0%]`).
   - Granular breakdown per entity: Whole Session, Main Coordinator, and each Subagent.

4. **CJK-Aware Terminal Layout**:
   - Fully compliant with `unicodedata.east_asian_width` to correctly calculate double-width Chinese/Japanese characters and emojis, preventing line wraps.

5. **`subagent-audit` CLI Command**:
   - Accompanying CLI tool for deep multi-agent token efficiency analysis:
     - `Out/In Synthesis Ratio` (measures delivery density vs. exploratory search overhead).
     - `Tokens/Turn` and `Tools/Turn` density.
     - Agent Type grouping matrix (e.g., `translator` vs. `polisher`).
     - Structured JSON export (`--json`) for automation and benchmarking.

---

## 🚀 Quick Start

### 1-Step Automatic Installation

Clone the repository and run the installer:

```bash
git clone https://github.com/<your-username>/antigravity-statusline.git
cd antigravity-statusline
./install.sh
```

The installer will:
1. Symlink `bin/statusline.sh` to `~/.gemini/statusline.sh`.
2. Symlink `bin/subagent-audit` into `~/.local/bin/subagent-audit` (and alias `agent-audit`).
3. Automatically configure `~/.gemini/antigravity-cli/settings.json` to activate the custom statusline.

---

### Manual Configuration

If you prefer to configure manually, update `~/.gemini/antigravity-cli/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/statusline.sh"
  },
  "stack_with_default": false
}
```

---

## 🛠️ `subagent-audit` CLI Usage

Inspect multi-agent token consumption and cache efficiency at any time:

```bash
# 1. Audit all subagents in the currently active session
subagent-audit

# 2. Audit a specific session (supports prefix matching)
subagent-audit -s 6d5cdcf9

# 3. Sort subagents by cache hit rate or synthesis ratio
subagent-audit --sort cache
subagent-audit --sort ratio

# 4. View detailed task prompts and tool invocation frequencies
subagent-audit -v

# 5. List all historical parent sessions with subagents
subagent-audit --list

# 6. Export structured JSON for downstream analysis
subagent-audit --json
```

### Dashboard Output Sample

```text
══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  🤖 Antigravity 多代理 Token 與快取效率審計儀表板 (Multi-Agent Token & Cache Analyzer)
══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

📌 目標 Session : 6d5cdcf9-438b-4d12-aad2-bf0e167c3125 (Fix Statusline Command Error)
📊 總量分配     : 主 Session 1.48M (in: 1.30M / out: 177.9k | 省下: 25.85M [95.2%]) ＋ 子代理 65.6k (in: 50.9k / out: 14.7k | 省下: 65.6k [56.3%]) ＝ 1.55M (全場省下: 25.92M [95.0%])
👥 子代理統計   : 共 1 隻代理 | 工具總調用: 5 次 | 子/主 Token 佔比: 4.4%

ID       | 代理身份 (Agent : Role)        | 狀態    |  輸入 (In) | 輸出 (Out) | 總計 (Total) | 快取省下 (Saved) | 產出率 (Out/In) |   輪次 |   工具調用 |   子佔比 |     耗時
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
58087a75 | architect: JPEG Codec Archite… | DONE    |      50.9k |      14.7k |        65.6k |    65.6k [56.3%] |           28.9% |      6 |          5 |   100.0% |   3m 10s
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

📑 代理類型效率矩陣 (Type Group Efficiency Breakdown):
代理類型 (Type)        | 衍生隻數 |   累計 Token |  平均每隻 Token |   平均省下 (Saved) | 平均產出率 (Out/In) |  平均每輪 Token |   累計工具
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
architect              |        1 |        65.6k |           65.6k |      65.6k [56.3%] |              28.9% |           10.9k |          5
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

🏆 多代理效率觀測結論 (Efficiency Insights):
  • 最高 Token 消耗   : [58087a75 architect: JPEG Codec Architect] 消耗 65.6k (佔子代理 100.0%)
  • 最高產出密度 (Out/In): [58087a75 architect: JPEG Codec Architect] 達 28.9% (產出 14.7k / 輸入 50.9k)
  • 最高快取節省 (Saved): [58087a75 architect: JPEG Codec Architect] 省下 65.6k Tokens (快取率 56.3%)
  • 最高工具密集度     : [58087a75 architect: JPEG Codec Architect] 調用 5 次工具 (65.6k Tokens, 6 輪)
```

---

## 🧪 Testing

Run unit tests covering formatting, calculations, and CJK width handling:

```bash
python3 -m unittest tests/test_subagent_audit.py
```

---

## 📄 License

[MIT License](LICENSE) © 2026 Jack Wang
