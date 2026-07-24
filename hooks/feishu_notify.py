#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feishu notification hook for Claude Code.

Fires on PreToolUse(AskUserQuestion/ExitPlanMode) and PermissionRequest —
i.e. whenever Claude needs a human decision — and pings a Feishu bot so the
user can step away and get called back. Each message is prefixed with the
current session name (looked up from ~/.claude/sessions/<pid>.json by
session_id) so concurrent sessions are distinguishable.

Design notes:
- Pure Python + urllib: avoids the Windows argv / codepage garbling that
  `curl -d '中文'` hits. Payload is encoded to UTF-8 bytes and sent directly.
- Never blocks the tool call: any error is swallowed and the process exits 0.

Deploy: copy this file to ~/.claude/hooks/feishu_notify.py and wire it up in
~/.claude/settings.json (see SKILL.md in this folder).
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
    """Look up the human-readable session name by session_id.

    Claude Code stores one JSON per PID under ~/.claude/sessions/, each
    carrying {sessionId, name}. We match on sessionId. Returns "" if the
    name is missing, unset, or anything goes wrong.
    """
    if not session_id:
        return ""
    sessions_dir = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
    try:
        for path in glob.glob(os.path.join(sessions_dir, "*.json")):
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


def build_body(data):
    """Build the descriptive body (without the session-name prefix)."""
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}

    # Permission approval (Bash command / file write-edit / etc.)
    if event == "PermissionRequest":
        if tool == "Bash":
            cmd = (ti.get("command") or "").strip()
            detail = "\n命令：" + cmd if cmd else ""
        elif tool in ("Write", "Edit", "NotebookEdit"):
            fp = (ti.get("file_path") or "").strip()
            detail = "\n文件：" + fp if fp else ""
        else:
            detail = ""
        return "🔔 Claude Code 需要你审批（" + tool + "）。" + detail

    if tool == "ExitPlanMode":
        return "🔔 Claude Code 需要你确认计划（plan），请回到终端。"

    if tool == "AskUserQuestion":
        qs = ti.get("questions") or []
        if qs and isinstance(qs[0], dict):
            head = (qs[0].get("question") or "").strip()
            if head:
                return "🔔 Claude Code 在等你做选择：\n" + head
        return "🔔 Claude Code 需要你做选择（正在提问），请回到终端。"

    return "🔔 Claude Code 正在等你操作，请回到终端。"


def main():
    if not URL:
        return  # webhook未配置,静默跳过
    # Read stdin as raw bytes and decode UTF-8 explicitly. On Windows, Python
    # defaults sys.stdin to the locale codepage (gbk/cp936), which garbles the
    # UTF-8 JSON the harness sends (Chinese tool_input arrives mojibake).
    # sys.stdin.buffer bypasses that entirely.
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        data = {}

    body = build_body(data)
    name = get_session_name(data.get("session_id", ""))
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
