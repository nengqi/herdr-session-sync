#!/usr/bin/env python3
"""herdr-session-sync

Automatically syncs Claude Code / Agent session names to:
1. Herdr Pane Labels (Mac 4-column border display)
2. Terminal Title (PTY xterm title for Heeler iOS companion app card second-line)
3. Herdr Tab Labels (Context-aware tab titles)

Zero dependencies (Python standard library only).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


def get_socket_path() -> str:
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def get_state_dir() -> Path:
    raw = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if raw:
        path = Path(raw)
    else:
        path = Path.home() / ".config" / "herdr" / "plugins" / "config" / "session-sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def herdr_rpc(method: str, params: dict | None = None, timeout: float = 1.5) -> dict | None:
    socket_path = get_socket_path()
    if not os.path.exists(socket_path):
        return None
    req = {
        "id": f"sync:{int(time.time() * 1000)}",
        "method": method,
        "params": params or {},
    }
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(req) + "\n").encode())
        res = b""
        while b"\n" not in res:
            chunk = s.recv(16384)
            if not chunk:
                break
            res += chunk
        s.close()
        if res:
            return json.loads(res.decode().strip())
    except Exception:
        pass
    return None


def get_tty_for_pid(pid: int | str | None) -> str | None:
    if not pid:
        return None
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "tty="], text=True).strip()
        if out and out != "?" and not out.startswith("??"):
            return f"/dev/{out}"
    except Exception:
        pass
    return None


class StateManager:
    def __init__(self, state_dir: Path):
        self.file_path = state_dir / "sync_state.json"
        self.data = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"assigned_sessions": {}}

    def save(self):
        try:
            tmp = self.file_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            tmp.rename(self.file_path)
        except Exception:
            pass

    def get_assigned_title(self, session_id: str) -> str | None:
        return self.data.get("assigned_sessions", {}).get(session_id)

    def set_assigned_title(self, session_id: str, title: str):
        if "assigned_sessions" not in self.data:
            self.data["assigned_sessions"] = {}
        self.data["assigned_sessions"][session_id] = title
        self.save()


def clean_task_title(text: str) -> str | None:
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None

    # Filter out internal system instructions / skill headers / reminders
    if (
        "Base directory for this skill:" in text
        or "Caveat: The messages below" in text
        or text.startswith("<system-reminder>")
        or text.startswith("Ran ")
        or text.startswith("Task #")
        or text.startswith("UserPromptSubmit")
        or text.startswith("Session renamed to:")
    ):
        return None

    # XML command extraction: e.g. <command-message>chat-catchup</command-message><command-name>/chat-catchup</command-name><command-args>...</command-args>
    if "<command-name>" in text or "<command-message>" in text:
        cmd_name_match = re.search(r"<command-name>(.*?)</command-name>", text, re.DOTALL)
        cmd_args_match = re.search(r"<command-args>(.*?)</command-args>", text, re.DOTALL)
        cmd = cmd_name_match.group(1).strip().lstrip("/") if cmd_name_match else ""
        args = cmd_args_match.group(1).strip() if cmd_args_match else ""

        if cmd in {"clear", "compact", "cost", "help", "fast", "exit", "quit", "rename"}:
            if cmd == "rename" and args:
                return args[:32]
            return None

        if args and len(args) > 1:
            first_arg_line = args.split("\n")[0].strip()
            return f"{cmd} {first_arg_line}"[:32].strip()
        elif cmd:
            return cmd[:32]
        return None

    if text.startswith("<"):
        return None

    # Grok/Claude handoff extraction: e.g. 【从 Grok 接管任务：OOS-词性缓存】
    grok_match = re.search(r"接管任务[：:]\s*([^\s】\n]+)", text)
    if grok_match:
        return grok_match.group(1).strip()

    # Filter out generic short acknowledgements
    lower = text.lower()
    if lower in {"ok", "yes", "no", "好的", "继续", "收到", "确认", "对", "行", "差不多", "测试", "align"}:
        return None

    # If slash command, extract parameter
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and len(parts[1].strip()) > 2:
            text = parts[1].strip()
        else:
            cmd = parts[0].lstrip("/")
            if cmd in {"clear", "compact", "cost", "help", "fast", "exit", "quit"}:
                return None
            text = cmd

    # Take first line
    first_line = text.split("\n")[0].strip()
    # Strip markdown headers, bullet lists, numbers, quotes
    first_line = re.sub(r"^[\s\d\.\-\*\#\>\[\]\(\)\|\:\：]+", "", first_line).strip()
    # Strip trailing punctuation
    first_line = re.sub(r"[\s\.\,\!\?\,\。\!\？]+$", "", first_line).strip()

    if not first_line or len(first_line) < 2:
        return None

    return first_line[:32].strip()


def extract_cc_session_name(session_id: str, cwd: str = "", terminal_title: str = "") -> str | None:
    # 1. Authoritative check: ~/.claude/projects/*/{session_id}/custom-title.json
    if session_id:
        ct_pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}/custom-title.json")
        ct_matches = glob.glob(ct_pattern)
        if ct_matches:
            try:
                with open(ct_matches[0], "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if d.get("customTitle"):
                        return str(d["customTitle"]).strip()[:32]
            except Exception:
                pass

        # 2. Check transcript JSONL for customTitle / agentName / first prompt
        pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl")
        matches = glob.glob(pattern)
        if matches:
            transcript_path = matches[0]
            last_custom_title = None
            first_prompt_title = None

            try:
                with open(transcript_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            # Exact customTitle or agentName set in Claude Code
                            if d.get("customTitle"):
                                last_custom_title = str(d["customTitle"]).strip()
                            elif d.get("agentName"):
                                last_custom_title = str(d["agentName"]).strip()

                            # First real user prompt as fallback
                            if (
                                not first_prompt_title
                                and d.get("type") == "user"
                                and not d.get("isMeta")
                                and not d.get("turnCompanion")
                                and d.get("message", {}).get("role") == "user"
                            ):
                                content = d.get("message", {}).get("content")
                                raw_text = ""
                                if isinstance(content, str):
                                    raw_text = content
                                elif isinstance(content, list) and content:
                                    for item in content:
                                        if isinstance(item, dict) and item.get("type") == "text":
                                            t = item.get("text", "")
                                            if not t.startswith("<system-reminder>") and "Base directory for this skill:" not in t:
                                                raw_text = t
                                                break
                                title = clean_task_title(raw_text)
                                if title:
                                    first_prompt_title = title
                        except Exception:
                            pass
            except Exception:
                pass

            if last_custom_title:
                return last_custom_title[:32]
            if first_prompt_title:
                return first_prompt_title[:32]

    # 3. If session_id is missing or stale, try resolving from terminal_title
    if terminal_title and terminal_title not in {"claude", "zsh", "bash", "sh", "None"}:
        for p in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        if terminal_title in line:
                            d = json.loads(line)
                            if d.get("agentName") == terminal_title or d.get("customTitle") == terminal_title:
                                sid = d.get("sessionId")
                                if sid:
                                    ct_file = os.path.expanduser(f"~/.claude/projects/*/{sid}/custom-title.json")
                                    ct_matches = glob.glob(ct_file)
                                    if ct_matches:
                                        with open(ct_matches[0], "r", encoding="utf-8") as h:
                                            ct_data = json.load(h)
                                            if ct_data.get("customTitle"):
                                                return str(ct_data["customTitle"]).strip()[:32]
                                return (d.get("customTitle") or d.get("agentName"))[:32]
            except Exception:
                pass

    # 4. Fallback to Git repository / Directory basename
    if cwd:
        base = os.path.basename(os.path.abspath(cwd))
        if base and base not in {"bytedance", "staff", "Desktop", "root", "~", "now"}:
            return base[:32]

    return None


def sync_pane(pane: dict, state: StateManager) -> bool:
    pane_id = pane.get("pane_id")
    if not pane_id:
        return False

    current_label = pane.get("label")
    cwd = pane.get("foreground_cwd") or pane.get("cwd", "")
    agent_session = pane.get("agent_session") or {}
    session_id = agent_session.get("value")
    terminal_title = pane.get("terminal_title") or pane.get("title") or ""

    # Check live active mapping reported by Claude Code statusline
    statusline_mapping = Path(f"/tmp/.herdr-pane-sessions/{pane_id}.json")
    if statusline_mapping.exists():
        try:
            with open(statusline_mapping, "r", encoding="utf-8") as smf:
                sdata = json.load(smf)
                live_sid = sdata.get("session_id")
                if live_sid:
                    session_id = live_sid
        except Exception:
            pass

    target_title = extract_cc_session_name(session_id, cwd, terminal_title)
    if target_title and session_id:
        state.set_assigned_title(session_id, target_title)

    if not target_title and cwd:
        base = os.path.basename(os.path.abspath(cwd))
        if base and base not in {"bytedance", "staff", "Desktop", "root", "~", "now"}:
            target_title = base[:32]

    if not target_title:
        return False

    updated = False

    # 1. Sync Pane Label (for Mac 4-column border)
    if current_label != target_title:
        res = herdr_rpc("pane.rename", {"pane_id": pane_id, "label": target_title})
        if res and "result" in res:
            updated = True

    # 2. Sync Herdr Metadata (report title, keep display_agent clean)
    herdr_rpc("pane.report_metadata", {
        "pane_id": pane_id,
        "source": "herdr:claude",
        "title": target_title,
        "display_agent": target_title
    })

    # 3. Directly update PTY xterm terminal_title via pane slave TTY (for Heeler card line 2)
    pinfo = herdr_rpc("pane.process_info", {"pane_id": pane_id})
    shell_pid = pinfo.get("result", {}).get("process_info", {}).get("shell_pid") if pinfo else None
    tty_path = get_tty_for_pid(shell_pid)
    if tty_path and os.path.exists(tty_path):
        try:
            with open(tty_path, "w") as tty_file:
                tty_file.write(f"\033]0;{target_title}\007")
                tty_file.flush()
            updated = True
        except Exception:
            pass

    return updated


def sync_all(state: StateManager):
    res = herdr_rpc("pane.list")
    if not res or "result" not in res:
        return
    panes = res.get("result", {}).get("panes", [])
    count = 0
    for p in panes:
        if sync_pane(p, state):
            count += 1
    if count > 0:
        print(f"Session Sync: synced {count} panes.")


def main():
    parser = argparse.ArgumentParser(description="herdr-session-sync")
    parser.add_argument("--all", action="store_true", help="Sync all panes")
    parser.add_argument("--event", action="store_true", help="Handle herdr event")
    parser.add_argument("--pane-current", action="store_true", help="Sync current pane")
    parser.add_argument("--status", action="store_true", help="Show sync status")

    args = parser.parse_args()
    state = StateManager(get_state_dir())

    if args.event:
        event_raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON")
        if event_raw:
            try:
                event_data = json.loads(event_raw)
                pane_id = event_data.get("data", {}).get("pane_id")
                if pane_id:
                    res = herdr_rpc("pane.get", {"pane_id": pane_id})
                    if res and "result" in res:
                        pane = res["result"].get("pane")
                        if pane:
                            sync_pane(pane, state)
                            return
            except Exception:
                pass
        sync_all(state)
    elif args.pane_current:
        pane_id = os.environ.get("HERDR_PANE_ID")
        if pane_id:
            res = herdr_rpc("pane.get", {"pane_id": pane_id})
            if res and "result" in res:
                pane = res["result"].get("pane")
                if pane:
                    sync_pane(pane, state)
    elif args.status:
        res = herdr_rpc("pane.list")
        panes = res.get("result", {}).get("panes", []) if res else []
        print(f"=== Herdr Session Sync Status ({len(panes)} panes) ===")
        for p in panes:
            pid = p.get("pane_id")
            lbl = p.get("label")
            sess = p.get("agent_session", {}).get("value")
            cached = state.get_assigned_title(sess) if sess else None
            print(f"[{pid}] Label: {lbl} | Session: {sess[:8] if sess else 'none'} | Cached Title: {cached}")
    else:
        sync_all(state)


if __name__ == "__main__":
    main()
