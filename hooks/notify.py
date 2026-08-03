#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unified notification hook for Claude Code.

Merges the former done_notify (Stop) + feishu_notify (PreToolUse /
PermissionRequest) into one dispatcher keyed on hook_event_name:

  Stop                                         -> "✅ 回答完成"
  PreToolUse(AskUserQuestion|ExitPlanMode) /   -> "🔔 ..." (needs a human
  PermissionRequest                               decision)

Each notification fans out to TWO channels:
  1. Feishu bot  — webhook from env FEISHU_WEBHOOK_URL (skip silently if unset)
  2. Windows toast — win11toast (preferred); PowerShell WinRT fallback;
     silent skip if both unavailable. (An earlier PowerShell NotifyIcon toast
     was unreliable here — Focus Assist / no message pump — so we use the
     WinRT notification-center API via win11toast instead.)

Never blocks the tool call: every error is swallowed, exit 0, no stdout.
Pure Python + urllib avoids the Windows argv/codepage garbling that
`curl -d '中文'` hits. Heavy-ish imports (win11toast) are deferred into the
function body so a missing lib can't break the Feishu send.
"""
import sys
import os
import glob
import json
import subprocess
import urllib.request

def _load_feishu_url():
    """Feishu webhook from env FEISHU_WEBHOOK_URL, else ~/.claude/.feishu_webhook
    (a local-only file kept out of the repo and settings.json). '' if unset."""
    url = os.environ.get('FEISHU_WEBHOOK_URL', '').strip()
    if url:
        return url
    try:
        with open(os.path.join(os.path.expanduser('~'), '.claude', '.feishu_webhook'),
                  encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


FEISHU_URL = _load_feishu_url()


# ---------------- session name ----------------
def get_session_name(session_id):
    """sessionId -> name via ~/.claude/sessions/*.json. '' if unknown."""
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


# ---------------- Feishu ----------------
def feishu_send(text):
    if not FEISHU_URL:
        return
    payload = json.dumps(
        {"msg_type": "text", "content": {"text": text}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        FEISHU_URL, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass  # network blip must never break the tool call


# ---------------- Windows toast ----------------
def toast_send(title, body):
    """Windows 11 system notification. Fires win11toast in a DETACHED
    subprocess so the blocking WinRT message loop never stalls this hook
    (win11toast.toast() blocks until the toast is dismissed). PowerShell
    WinRT fallback; silent skip on failure. Never raises."""
    # 1. win11toast in a detached child (returns immediately; the toast's
    #    WinRT message loop runs in the child until dismissal/TIMEOUT).
    try:
        env = dict(os.environ, _CC_TOAST_TITLE=title, _CC_TOAST_BODY=body)
        flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, '-c',
             'import os;from win11toast import toast;'
             'toast(os.environ["_CC_TOAST_TITLE"],os.environ["_CC_TOAST_BODY"])'],
            env=env, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return
    except Exception:
        pass
    # 2. PowerShell WinRT toast fallback (borrow WindowsTerminal's AppId so
    # the toast registers under a known app even without our own AUMID).
    try:
        def esc(s):
            return (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace("'", "&apos;"))
        script = (
            "[void][Windows.UI.Notifications.ToastNotificationManager,"
            "Windows.UI.Notifications,ContentType=WindowsRuntime];"
            "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$x.LoadXml('<toast><visual><binding template=\"ToastText02\">"
            f"<text id=\"1\">{esc(title)}</text>"
            f"<text id=\"2\">{esc(body)}</text>"
            "</binding></visual></toast>');"
            "$tn=New-Object Windows.UI.Notifications.ToastNotification $x;"
            "[Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('Microsoft.WindowsTerminal_8wekyb3d8bbwe!App').Show($tn)"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            timeout=10, capture_output=True,
        )
    except Exception:
        pass


# ---------------- Stop-event filtering (ex done_notify) ----------------
def last_conversational_type(transcript_path):
    """'user'/'assistant' for the last conversational transcript entry,
    scanning back past interleaved metadata. Reads only the last 256KB."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            if size > 262144:
                f.seek(size - 262144)
                f.readline()  # drop a partial first line
                data = f.read().decode("utf-8", errors="replace")
            else:
                data = f.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    for ln in reversed(data.splitlines()):
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


def _has_result(lines, tool_use_id):
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


def find_pending_tool(transcript_path):
    """Scan transcript tail for an Agent/Task/Workflow tool_use whose
    tool_result hasn't landed yet. Returns a detail string or None."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            if size > 262144:
                f.seek(size - 262144)
                f.readline()
                data = f.read().decode("utf-8", errors="replace")
            else:
                data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
    except Exception:
        return None
    pending_ids = set()
    tool_names = {"Agent", "Task", "Workflow"}
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
                        if not _has_result(lines, tid):
                            pending_ids.add(tid)
        elif o.get("type") == "user" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    pending_ids.discard(c.get("tool_use_id"))
    if pending_ids:
        return f"等待 {len(pending_ids)} 个 agent/tool 返回"
    return None


def should_notify(data):
    """Only notify on a genuine completion (skip hook-recursion / compact /
    clear / resume stops). Default to notify on uncertainty."""
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
    """Distinguish a phase stop (background/pending work) from a final stop."""
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
    crons = data.get("session_crons") or []
    if isinstance(crons, list) and crons:
        return True, f"{len(crons)}个定时任务待唤醒"
    tp = data.get("transcript_path")
    if tp:
        detail = find_pending_tool(tp)
        if detail:
            return True, detail
    return False, None


# ---------------- decision-event body (ex feishu_notify) ----------------
def build_decision_body(data):
    """Build the descriptive body (without session-name prefix)."""
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}

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


# ---------------- dispatch ----------------
def main():
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}
    except Exception:
        data = {}

    event = data.get("hook_event_name", "")

    if event == "Stop":
        if not should_notify(data):
            return
        if detect_phase(data)[0]:
            return  # phase stop (background/pending work) — don't notify
        body = "✅ 回答完成，等待你的下一个问题"
    else:
        # PreToolUse(AskUserQuestion|ExitPlanMode) / PermissionRequest
        body = build_decision_body(data)

    name = get_session_name(data.get("session_id", ""))
    feishu_send(("【" + name + "】" + body) if name else body)
    toast_title = ("Claude Code · " + name) if name else "Claude Code"
    toast_send(toast_title, body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
