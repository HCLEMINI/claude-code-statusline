#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feishu "response done" notification hook for Claude Code.

Fires on the Stop event — Claude finished responding and is ready for the
next prompt — and pings the Feishu bot so the user (who may have stepped
away) knows the turn is done. (Originally tried a Windows toast via
PowerShell NotifyIcon, but it was unreliable on this machine — Focus
Assist / no message pump — so we send Feishu instead, which is proven.)

Filtered: skips compact / /clear / resume stops that aren't a real
completion (see should_notify). Never blocks; exits 0; no stdout.

Deploy: copy this file to ~/.claude/hooks/done_notify.py and wire the Stop
event in ~/.claude/settings.json (see claude-skills-hooks/SKILL.md).
"""
import sys
import os
import glob
import json
import urllib.request

# Read the Feishu webhook from env (set FEISHU_WEBHOOK_URL in settings.json env).
# NEVER hardcode your webhook — anyone with the URL can post to your group.
URL = os.environ.get("FEISHU_WEBHOOK_URL", "")


def get_session_name(session_id):
    """Same lookup as feishu_notify.py: sessionId -> name in ~/.claude/sessions/."""
    if not session_id:
        return ""
    d = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
    try:
        for path in glob.glob(os.path.join(d, "*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                if rec.get("sessionId") == session_id:
                    return (rec.get("name") or "").strip()
            except Exception:
                continue
    except Exception:
        pass
    return ""


def last_conversational_type(transcript_path):
    """Return 'user'/'assistant' for the last conversational entry in the
    transcript, scanning backward past interleaved metadata (ai-title, mode,
    permission-mode, ...). None if unreadable / no conv entry."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("type") in ("user", "assistant"):
            return o.get("type")
    return None


def should_notify(data):
    """Only notify on a genuine response completion.

    Skip when:
    - stop_hook_active: this Stop is a hook-driven continuation/recursion.
    - last conversational transcript entry is NOT assistant: happens on
      compact (summary injected as user/system), /clear, resume — none of
      which are 'Claude just finished answering'.
    On any uncertainty (no transcript path, unreadable), default to notify
    so the core feature never silently breaks.
    """
    if data.get("stop_hook_active"):
        return False
    tp = data.get("transcript_path")
    if not tp:
        return True
    last = last_conversational_type(tp)
    if last is None:
        return True
    return last == "assistant"


def detect_phase(data):
    """Distinguish a phase stop (background/pending work) from a final stop.

    Returns (is_phase, detail_str). Uses two signals:
    1. background_tasks field (added to Stop input in CC ~2.1.x): non-empty
       means background agents/shells are still running.
    2. Transcript fallback: last assistant message has a pending Agent/Task/
       Workflow tool_use with no matching tool_result yet.
    """
    # Signal 1: background tasks (authoritative — per CC schema, an empty
    # array means "session done"; non-empty means "paused waiting for bg work")
    bg = data.get("background_tasks") or []
    if isinstance(bg, list) and bg:
        from collections import Counter
        cn = {
            "subagent": "子agent", "workflow": "工作流", "shell": "后台命令",
            "monitor": "监控", "MCP task": "MCP任务", "teammate": "队友agent",
            "cloud session": "云端会话", "dream": "dream",
        }
        types = Counter(
            cn.get(t.get("type", "task"), t.get("type", "task"))
            for t in bg if isinstance(t, dict)
        )
        summary = "、".join(f"{n}个{lbl}" for lbl, n in types.items())
        return True, f"后台 {summary} 执行中"
    # Signal 1b: scheduled crons mean the session will wake later
    crons = data.get("session_crons") or []
    if isinstance(crons, list) and crons:
        return True, f"{len(crons)}个定时任务待唤醒"
    # Signal 2: pending foreground tool call (defensive fallback)
    tp = data.get("transcript_path")
    if tp:
        detail = find_pending_tool(tp)
        if detail:
            return True, detail
    return False, None


def find_pending_tool(transcript_path):
    """Scan transcript tail for an Agent/Task/Workflow tool_use whose
    tool_result hasn't landed yet. Returns a detail string or None."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    # Walk from the end: collect the last assistant tool_use ids of interest
    pending_ids = set()
    tool_names = {"Agent", "Task", "Workflow"}
    # Only inspect the last ~40 entries (a turn's worth) for cheapness
    for ln in reversed(lines[-40:]):
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        msg = o.get("message", {})
        content = msg.get("content", [])
        if o.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use" \
                        and c.get("name") in tool_names:
                    tid = c.get("id")
                    if tid and tid not in pending_ids:
                        # Re-check: is there a tool_result for this id later?
                        if not _has_result(lines, tid):
                            pending_ids.add(tid)
        elif o.get("type") == "user" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    pending_ids.discard(c.get("tool_use_id"))
    if pending_ids:
        return f"等待 {len(pending_ids)} 个 agent/tool 返回"
    return None


def _has_result(lines, tool_use_id):
    """True if a tool_result for tool_use_id exists anywhere in the transcript."""
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        content = o.get("message", {}).get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result" \
                        and c.get("tool_use_id") == tool_use_id:
                    return True
    return False


def main():
    # Read stdin as raw bytes, decode UTF-8 explicitly (Windows defaults to gbk).
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}
    except Exception:
        data = {}

    # Debug-log the full Stop input (cheap, helps verify field structure /
    # diagnose mis-classification). Rotate-safe: append only.
    try:
        dbg = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "stop_input_debug.log")
        with open(dbg, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if not URL:
        return  # webhook未配置,静默跳过
    if not should_notify(data):
        return

    name = get_session_name(data.get("session_id", ""))
    is_phase, detail = detect_phase(data)
    if is_phase:
        body = f"⏳ 阶段性停止（{detail}，对话未结束）"
    else:
        body = "✅ 回答完成，等待你的下一个问题"
    text = ("【" + name + "】" + body) if name else body

    payload = json.dumps(
        {"msg_type": "text", "content": {"text": text}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass  # network blip must never break the tool call


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
