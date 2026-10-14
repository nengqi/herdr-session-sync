#!/usr/bin/env python3
"""herdr-session-sync

Automatically syncs Claude Code / Agent session names to:
1. Herdr Pane Labels (Mac 4-column border display)
2. Herdr Pane Metadata & Window Titles (for Heeler iOS companion app cards)
3. Herdr Tab Labels (Context-aware tab titles)

Zero dependencies (Python standard library only).
"""

from __future__ import annotations

import argparse
import calendar
import glob
import json
import os
import re
import socket
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
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def sanitize_title(title: str | None) -> str:
    if not title or not isinstance(title, str):
        return ""
    # Strip ANSI escape sequences (CSI / OSC / etc.)
    title = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", title)
    # Strip non-printable and control characters (0x00-0x1F, 0x7F-0x9F, including BEL \x07)
    title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title)
    # Strip trailing punctuation, separators, and dashes (e.g. "相关性 MCP - " -> "相关性 MCP")
    title = re.sub(r"[\s\.\,\!\?\,\。\!\？\:\：\|\-\—\–\_]+$", "", title)
    return title.strip()


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
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            s.sendall((json.dumps(req) + "\n").encode())
            res = b""
            while b"\n" not in res:
                chunk = s.recv(16384)
                if not chunk:
                    break
                res += chunk
            if res:
                return json.loads(res.decode().strip())
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
            tmp = self.file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.file_path)
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
        or text.startswith("<task-notification>")
        or text.startswith("<local-command-")
        or text.startswith("<user-prompt-submit")
        or text.startswith("Ran ")
        or text.startswith("Task #")
        or text.startswith("UserPromptSubmit")
        or text.startswith("Session renamed to:")
    ):
        return None

    # XML command extraction: e.g. <command-message>chat-catchup</command-message><command-name>/chat-catchup</command-name><command-args>...</command-args>
    if "<command-name>" in text or "<command-message>" in text:
        cmd_name_match = re.search(r"<command-name>(.*?)</command-name>", text, re.DOTALL)
        cmd_msg_match = re.search(r"<command-message>(.*?)</command-message>", text, re.DOTALL)
        cmd_args_match = re.search(r"<command-args>(.*?)</command-args>", text, re.DOTALL)

        cmd = ""
        if cmd_name_match:
            cmd = cmd_name_match.group(1).strip().lstrip("/")
        elif cmd_msg_match:
            cmd = cmd_msg_match.group(1).strip().lstrip("/")

        args = cmd_args_match.group(1).strip() if cmd_args_match else ""

        if cmd in {"clear", "compact", "cost", "help", "fast", "exit", "quit", "rename"}:
            if cmd == "rename" and args:
                return sanitize_title(args)[:32]
            return None

        if args and len(args) > 1:
            first_arg_line = args.split("\n")[0].strip()
            return sanitize_title(f"{cmd} {first_arg_line}")[:32].strip()
        elif cmd:
            return sanitize_title(cmd)[:32]
        return None

    # Grok/Claude handoff extraction: e.g. 【从 Grok 接管任务：OOS-词性缓存】
    grok_match = re.search(r"接管任务[：:]\s*([^】\n]+)", text)
    if grok_match:
        val = sanitize_title(grok_match.group(1).strip())[:32]
        if val:
            return val

    # Filter out generic short acknowledgements
    lower = text.lower()
    if lower in {"ok", "yes", "no", "好的", "继续", "收到", "确认", "对", "行", "差不多", "测试", "align"}:
        return None

    # If slash command, extract parameter
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/")
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"clear", "compact", "cost", "help", "fast", "exit", "quit", "rename"}:
            if cmd == "rename" and args:
                return sanitize_title(args)[:32]
            return None

        if args and len(args) > 2:
            text = args
        else:
            text = cmd

    # Take first line
    first_line = text.split("\n")[0].strip()
    # Strip markdown headers, blockquotes, bullets, quotes, backticks, and task checkboxes
    first_line = re.sub(r"^[\s\#\>\*\-\|\`\'\"•·]+", "", first_line).strip()
    first_line = re.sub(r"^\[[ xX]\]\s*", "", first_line).strip()
    # Strip leading list prefixes:
    # 1) Numeric: 1. / 1) / (1) / [1] / 1: / 1：
    # 2) Alphabetic: a. / b. / A. / B) / (a) / [b] / a: / a：
    # 3) Roman: i. / ii. / I. / II.
    first_line = re.sub(r"^(?:(?:[0-9]+|[a-zA-Z]|[ivxIVX]+)[\.\:\：\)\/]|[\(\[](?:[0-9]+|[a-zA-Z]|[ivxIVX]+)[\)\]])\s*", "", first_line).strip()
    # Clean leading formatting again after list prefix removal
    first_line = re.sub(r"^[\s\#\>\*\-\|\`\'\"•·]+", "", first_line).strip()
    # Strip trailing punctuation, separators, and dashes
    first_line = sanitize_title(first_line)
    if not first_line or len(first_line) < 2:
        return None

    return first_line[:32].strip()


def extract_cc_session_name(session_id: str, cwd: str = "") -> str | None:
    # 1. Authoritative check: ~/.claude/projects/*/{session_id}/custom-title.json
    if session_id:
        ct_pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}/custom-title.json")
        ct_matches = glob.glob(ct_pattern)
        if ct_matches:
            try:
                with open(ct_matches[0], "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if d.get("customTitle"):
                        return sanitize_title(str(d["customTitle"]))[:32]
            except Exception:
                pass

        # 2. Check transcript JSONL for customTitle / agentName / first prompt
        pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl")
        matches = glob.glob(pattern)
        if matches:
            transcript_path = matches[0]
            last_custom_title = None
            first_prompt_title = None

            def parse_line_obj(line_str: str):
                nonlocal last_custom_title, first_prompt_title
                try:
                    d = json.loads(line_str)
                    if d.get("customTitle"):
                        last_custom_title = str(d["customTitle"]).strip()
                    elif d.get("agentName"):
                        last_custom_title = str(d["agentName"]).strip()

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

            try:
                fsize = os.path.getsize(transcript_path)
                with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                    if fsize > 524288:
                        # Bounded read: first 128KB for first user prompt
                        head_chunk = f.read(131072)
                        for line in head_chunk.splitlines():
                            parse_line_obj(line)
                        # Seek to tail 128KB for latest customTitle / agentName
                        f.seek(max(0, fsize - 131072))
                        tail_chunk = f.read()
                        for line in tail_chunk.splitlines():
                            parse_line_obj(line)
                    else:
                        for line in f:
                            parse_line_obj(line)
            except Exception:
                pass

            if last_custom_title:
                return sanitize_title(last_custom_title)[:32]
            if first_prompt_title:
                return sanitize_title(first_prompt_title)[:32]

    # 3. Deterministic fallback to Git repository / Directory basename (strictly per-pane CWD, never fuzzy search)
    if cwd:
        abs_cwd = Path(cwd).resolve()
        if abs_cwd != Path.home().resolve() and abs_cwd != Path("/"):
            base = abs_cwd.name
            ignored = {"staff", "Desktop", "root", "~", "now", Path.home().name}
            if base and base not in ignored:
                return sanitize_title(base)[:32]

    return None


def is_valid_cc_session(session_id: str) -> bool:
    """Checks whether a given session ID exists on disk as a non-empty transcript."""
    if not session_id or not isinstance(session_id, str):
        return False
    session_id = session_id.strip()
    if not re.match(r"^[0-9a-fA-F-]{6,36}$", session_id):
        return False
    matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"))
    for m in matches:
        try:
            if os.path.isfile(m) and os.path.getsize(m) > 0:
                return True
        except OSError:
            pass
    return False


def _proc_start_matches(lstart: str, proc_start: str) -> bool:
    """Whether `ps -o lstart=` and a session's `procStart` describe the same instant.

    Both use ctime formatting ("Wed Sep 16 13:28:43 2026") but not the same clock: `ps`
    prints local time while session JSON records UTC, so the strings differ by the UTC
    offset on the same process. Compare parsed instants under both readings and allow a
    second of slack, rather than comparing text.
    """
    fmt = "%a %b %d %H:%M:%S %Y"
    try:
        a = time.strptime(" ".join(lstart.split()), fmt)
        b = time.strptime(" ".join(proc_start.split()), fmt)
    except (ValueError, AttributeError):
        return False
    try:
        local = time.mktime(a)
        return abs(local - time.mktime(b)) <= 1 or abs(local - calendar.timegm(b)) <= 1
    except (OverflowError, ValueError):
        return False


def _is_live_claude_process(pid: int, proc_start: str | None = None) -> bool:
    """Whether `pid` is still the Claude process the session metadata describes.

    A signal-0 probe alone only proves *something* holds that pid. Session JSON outlives
    the process that wrote it, so after a pid rollover an unrelated process can make a
    long-dead session look alive. Confirm the command is Claude and, when the metadata
    recorded a `procStart`, that the process started at that instant; also reject a
    zombie, which answers signal 0 but runs nothing.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    try:
        import subprocess

        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "state=,lstart=,command="],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return False
        # "SNs+ Wed Sep 16 13:28:43 2026 claude bg-spare ..." -- state, then lstart's
        # fixed five fields, then the command.
        fields = out.stdout.strip().split(None, 6)
        if len(fields) < 7:
            return False
        state, lstart, command = fields[0], " ".join(fields[1:6]), fields[6]
        if state.startswith("Z"):
            return False
        if "claude" not in command.lower():
            return False
        if proc_start and not _proc_start_matches(lstart, proc_start):
            return False
        return True
    except Exception:
        return False


def resolve_parked_job_session(job_id: str) -> tuple[str | None, str | None]:
    """Resolves the durable session behind a parked background job.

    An interactive process that attached to a background job is a thin viewer: it owns
    the pane's TTY but never writes a transcript. The conversation lives in the job it
    attached to, recorded in ~/.claude/jobs/<jobId>/state.json, whose `resumeSessionId`
    is what `claude --resume` can actually reopen.
    """
    if not job_id or not isinstance(job_id, str):
        return None, None
    if not re.match(r"^[0-9a-zA-Z_-]{1,64}$", job_id):
        return None, None
    state_file = os.path.expanduser(f"~/.claude/jobs/{job_id}/state.json")
    if not os.path.isfile(state_file):
        return None, None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None

    for key in ("resumeSessionId", "sessionId"):
        sid = data.get(key)
        if not isinstance(sid, str):
            continue
        sid = sid.strip()
        if not re.match(r"^[0-9a-fA-F-]{6,36}$", sid):
            continue
        if not is_valid_cc_session(sid):
            continue
        title = extract_cc_session_name(sid)
        if not title:
            raw_name = data.get("name")
            if isinstance(raw_name, str) and raw_name and data.get("nameSource") != "derived":
                title = sanitize_title(raw_name)[:32]
        return sid, title
    return None, None


def resolve_live_bg_session_by_name(name: str) -> tuple[str | None, str | None]:
    """Finds the live background session whose reported name matches `name`.

    Last-resort fallback for a pane whose foreground interactive process has no
    transcript of its own: it is viewing a background session, and nothing on disk
    links the two once `parkedJobId` goes stale (the job directory it names is often
    already deleted). Every stronger key was checked and ruled out: the viewer's argv
    carries no `attach <id>`, the two processes sit on different TTYs with no socket
    between them, and the viewer's `claude daemon run --spawned-by` shares exactly one
    socket with *every* live background session, so it cannot tell them apart.

    That leaves the name, which is weak on two counts, and callers should treat a match
    as a guess rather than an identity:
      - It is mutable. `/rename`, an auto-generated summary, or a manual
        `herdr pane rename` all rewrite it, and the link breaks or moves with it.
      - It is partly self-referential: sync_pane() writes pane labels via
        `pane.rename`, so a label can be an echo of an earlier sync rather than
        independent evidence.

    Narrowed so a wrong guess stays unlikely: the name must be reported by the session
    itself or set by the user (never derived from a directory), the process must still
    be alive, its transcript must exist, and exactly one live session may claim the
    name. Anything ambiguous returns nothing and leaves the pane unbound -- a failed
    resume the user can fix by hand beats silently attaching a pane to someone else's
    conversation.
    """
    name = (name or "").strip()
    if not name:
        return None, None

    matches = []
    for path in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("kind") != "bg":
            continue
        # A name Claude derived from the directory is not an identity; only trust a
        # name the session itself reported (peer) or the user set.
        if data.get("nameSource") == "derived":
            continue
        if (data.get("name") or "").strip() != name:
            continue
        if not _is_live_claude_process(data.get("pid"), data.get("procStart")):
            continue
        sid = data.get("sessionId")
        if not isinstance(sid, str):
            continue
        sid = sid.strip()
        if not re.match(r"^[0-9a-fA-F-]{6,36}$", sid) or not is_valid_cc_session(sid):
            continue
        matches.append(sid)

    if len(matches) != 1:
        return None, None
    sid = matches[0]
    return sid, extract_cc_session_name(sid) or sanitize_title(name)[:32]


def resolve_session_from_pid(pid: int, pane_name: str = "") -> tuple[str | None, str | None]:
    """Reads ~/.claude/sessions/{pid}.json to extract authoritative Root Session ID and title."""
    if not pid or not isinstance(pid, int) or pid <= 0:
        return None, None
    session_file = os.path.expanduser(f"~/.claude/sessions/{pid}.json")
    if not os.path.isfile(session_file):
        return None, None
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("kind") != "interactive":
            return None, None
        session_id = data.get("sessionId")
        if not session_id or not isinstance(session_id, str):
            return None, None
        session_id = session_id.strip()
        if not re.match(r"^[0-9a-fA-F-]{6,36}$", session_id):
            return None, None

        # `parkedJobId` marks a viewer displaying a background session; a session nobody
        # has typed into yet looks the same from a missing transcript alone, so only the
        # marker may redirect. Without it, report nothing -- a fresh session becomes durable
        # on its first user message, and guessing by name here would bind the pane to an
        # unrelated conversation that merely shares its title. The marker wins even when
        # the viewer has a transcript of its own: a viewer started as `claude --resume X`
        # keeps X on disk, but X is the stale pre-park conversation, not what it displays.
        parked_job_id = data.get("parkedJobId")
        if parked_job_id:
            # jobs/<id>/state.json is the trustworthy link -- it joins on a job id, not
            # on a mutable name.
            job_sid, job_title = resolve_parked_job_session(parked_job_id)
            if job_sid:
                return job_sid, job_title
            # Weak fallback once parkedJobId names a job that no longer exists; see the
            # docstring for why the name is the only key left and how narrow the match is.
            bg_sid, bg_title = resolve_live_bg_session_by_name(pane_name)
            if bg_sid:
                return bg_sid, bg_title
        # Without a parkedJobId this is a plain session nobody has typed into yet, not a
        # viewer. Return its own id so the pane still gets a label; sync_pane's
        # is_valid_cc_session() gate keeps the unwritten id from being persisted, and
        # guessing by name here would bind the pane to an unrelated same-titled session.

        title = extract_cc_session_name(session_id)
        if not title:
            name_source = data.get("nameSource")
            raw_name = data.get("name")
            # Only adopt data.get("name") if it is a real user/peer name, NOT an auto-derived directory slug (e.g. bytedance-5d)
            if raw_name and isinstance(raw_name, str) and name_source != "derived":
                title = sanitize_title(raw_name)[:32]
        return session_id, title
    except Exception:
        pass
    return None, None


def _is_claude_proc(proc: dict) -> bool:
    """Whether a pane foreground process is claude, a node runner for claude, or a shell wrapper invoking claude."""
    argv = proc.get("argv") or []
    argv0 = os.path.basename(proc.get("argv0") or (argv[0] if argv else ""))
    name = proc.get("name", "")
    return (
        "claude" in argv0
        or "claude" in name
        or (argv0 in {"node", "nodejs", "bun"} and len(argv) > 1 and "claude" in os.path.basename(argv[1]))
        or (argv0 in {"sh", "bash", "zsh"} and any(os.path.basename(a) == "claude" for a in argv[1:3]))
    )


def resolve_title_from_foreground_process(pane_id: str, cwd: str = "", pane_name: str = "") -> tuple[str | None, str | None]:
    """Inspects pane.process_info to resolve session_id and title directly from PID or CLI args (attach/--resume/--name)."""
    try:
        pinfo = herdr_rpc("pane.process_info", {"pane_id": pane_id})
        if not pinfo:
            return None, None
        fg = pinfo.get("result", {}).get("process_info", {}).get("foreground_processes", [])
        for proc in fg:
            argv = proc.get("argv", [])
            if not argv:
                continue
            if not _is_claude_proc(proc):
                continue

            # 1. Authoritative check via process PID -> ~/.claude/sessions/{pid}.json
            proc_pid = proc.get("pid")
            if isinstance(proc_pid, int):
                sid, title = resolve_session_from_pid(proc_pid, pane_name)
                if sid:
                    return sid, title

            for i, arg in enumerate(argv):
                flag, sep, inline_val = arg.partition("=")
                val = inline_val if sep else (argv[i + 1].strip() if i + 1 < len(argv) else "")
                if not val or val.startswith("-"):
                    continue

                if flag in {"attach", "--resume", "-r"}:
                    if re.match(r"^[0-9a-fA-F-]{6,36}$", val):
                        pattern = f"{val}.jsonl" if len(val) == 36 else f"{val}*.jsonl"
                        matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{pattern}"))
                        candidates = [
                            m for m in matches
                            if os.path.isfile(m) and ".orphaned-" not in os.path.basename(m)
                        ]
                        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        for m in candidates:
                            sid = os.path.basename(m)[:-6]
                            if is_valid_cc_session(sid):
                                title = extract_cc_session_name(sid, cwd)
                                return sid, title
                    elif flag != "attach":
                        return None, sanitize_title(val)[:32]
                elif flag in {"--name", "-n"}:
                    return None, sanitize_title(val)[:32]
    except Exception:
        pass
    return None, None


def extract_title_from_screen(pane_id: str) -> str | None:
    """Inspects the live visible terminal buffer to extract the active Claude Code session title

    rendered in the readline bar: ──── <Title> ─
    """
    try:
        res = herdr_rpc("pane.read", {"pane_id": pane_id, "source": "recent", "lines": 25})
        if not res:
            return None
        text = res.get("result", {}).get("read", {}).get("text", "")
        if not text:
            return None
        for line in reversed(text.splitlines()):
            m = re.search(r"[─\─\—\-]{3,}\s*(.+?)\s*[─\─\—\-]+$", line)
            if m:
                candidate = m.group(1).strip()
                if candidate and set(candidate).issubset({'-', '─', '—'}):
                    continue
                ignored_keywords = {
                    "focus", "agents", "options", "recap", "permissions", "shift+tab",
                    "bypass permissions", "auto mode", "plan mode", "manual mode",
                    "accept edits", "dontask", "task",
                }
                if (
                    candidate
                    and candidate.lower() not in ignored_keywords
                    and not candidate.startswith("http")
                    and len(candidate) >= 2
                ):
                    return sanitize_title(candidate)[:32]
    except Exception:
        pass
    return None


BG_REFUSAL_RE = re.compile(r"That session is running in the background \(([0-9A-Za-z]{4,32})\)")


def find_bg_resume_refusal(text: str, session_id: str) -> str | None:
    """Returns the job id Claude refused to resume in this pane, if that is the pane's latest output.

    After a Herdr restart the pane reruns `claude --resume <session_id>`. When that
    session is still running in the background, Claude refuses with
    "That session is running in the background (<job>). Run `claude attach <job>` ..."
    and drops back to the shell -- the job id there is Claude's own answer to "what does
    this pane display". Accept it only when the echoed command right above is the
    `--resume` of this pane's session and at most the shell prompt follows; anything older
    is history, and attaching on history would type into whatever runs there now. The id
    is typed into a shell, so it must be a plain alphanumeric token.
    """
    if not text or not session_id:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i in range(len(lines) - 1, -1, -1):
        m = BG_REFUSAL_RE.search(lines[i])
        if not m:
            continue
        if len(lines) - 1 - i > 1 or i == 0:
            return None
        if "--resume" not in lines[i - 1] or session_id not in lines[i - 1]:
            return None
        job_id = m.group(1)
        return job_id if session_id.startswith(job_id) else None
    return None


def _pane_runs_claude(pane_id: str) -> bool | None:
    """Whether Claude is in the pane's foreground; None when Herdr cannot tell."""
    pinfo = herdr_rpc("pane.process_info", {"pane_id": pane_id})
    if not pinfo or "result" not in pinfo:
        return None
    fg = pinfo["result"].get("process_info", {}).get("foreground_processes", [])
    return any(_is_claude_proc(p) for p in fg)


def reattach_viewers_after_restore(timeout: float = 120.0, interval: float = 2.0, settle: float = 20.0,
                                   sleep=time.sleep, clock=time.monotonic) -> list[tuple[str, str]]:
    """Startup hook: run `claude attach <job>` where the restart's resume was refused.

    Herdr resumes a Claude pane with `claude --resume <id>` from its stored reference, and
    there is no way to change that command without a custom reporter holding the pane --
    which makes Herdr stop reading the screen and freezes the pane's status. So viewer panes
    keep an ordinary reference to the background session they display (see sync_pane), the
    resume is refused while that session still runs, and this hook follows Claude's own
    advice afterwards. A pane is attached at most once per startup, and only from a shell.
    Panes still running Claude are done; the loop ends once nothing is waiting after
    `settle` seconds, or at `timeout`.
    """
    import subprocess

    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    attached: list[tuple[str, str]] = []
    handled: set[str] = set()
    start = clock()
    while True:
        res = herdr_rpc("pane.list")
        panes = res.get("result", {}).get("panes", []) if res else []
        pending = 0
        for pane in panes:
            pane_id = pane.get("pane_id")
            ref = pane.get("agent_session") or {}
            sid = ref.get("value")
            if not pane_id or pane_id in handled or ref.get("agent") != "claude" or ref.get("kind") != "id":
                continue
            if not isinstance(sid, str) or _pane_runs_claude(pane_id) is not False:
                continue
            read = herdr_rpc("pane.read", {"pane_id": pane_id, "source": "recent-unwrapped", "lines": 12})
            text = read.get("result", {}).get("read", {}).get("text", "") if read else ""
            job_id = find_bg_resume_refusal(text, sid)
            if not job_id:
                pending += 1
                continue
            handled.add(pane_id)
            try:
                subprocess.run([herdr, "pane", "run", pane_id, f"claude attach {job_id}"],
                               capture_output=True, timeout=5)
                attached.append((pane_id, job_id))
            except Exception:
                pass
        elapsed = clock() - start
        if elapsed >= timeout or (pending == 0 and elapsed >= settle):
            return attached
        sleep(interval)


def sync_pane(pane: dict, state: StateManager) -> bool:
    pane_id = pane.get("pane_id")
    if not pane_id:
        return False

    current_label = pane.get("label") or ""
    cwd = pane.get("foreground_cwd") or pane.get("cwd", "")
    agent_session = pane.get("agent_session") or {}
    session_id = agent_session.get("value")
    current_title = pane.get("title") or ""
    terminal_title = pane.get("terminal_title") or ""

    target_title = None

    # Priority 1: Live screen readline bar title directly rendered by Claude Code (e.g. ──── 诊断-实验参数抓取 ─)
    # Reflects live in-session /rename or session title with 100% visual fidelity.
    screen_title = extract_title_from_screen(pane_id)
    if screen_title:
        target_title = screen_title

    # Priority 2: Direct foreground process inspection (PID -> sessions/{pid}.json or CLI args)
    # Physically immune to stale Herdr state or background subagent hijacking.
    # Runs even when the screen already gave a title: a viewer pane always shows one, and
    # skipping here would leave its binding stale. The screen title is also the name the
    # viewer displays, so it is the best hint for the background-session join.
    fg_sid, fg_title = resolve_title_from_foreground_process(pane_id, cwd, screen_title or current_title or current_label)
    if fg_sid:
        # Auto-heal: If Herdr's recorded agent_session doesn't match the authoritative foreground session,
        # report the authoritative session back to Herdr with monotonic sequence and resume source.
        # Durability guard: a session UUID is allocated at SessionStart but its transcript .jsonl is only
        # written once the first user message lands. Persisting an unwritten UUID makes `claude --resume`
        # fail with "No conversation found with session ID" after a Herdr restart, so only report a
        # session that is already durable on disk.
        if session_id != fg_sid and is_valid_cc_session(fg_sid):
            herdr_rpc("pane.report_agent_session", {
                "pane_id": pane_id,
                "source": "herdr:claude",
                "agent": "claude",
                "agent_session_id": fg_sid,
                "seq": time.time_ns(),
                "session_start_source": "resume",
            })
            session_id = fg_sid

        if fg_title and not target_title:
            target_title = fg_title
            state.set_assigned_title(session_id, target_title)
    elif fg_title and not target_title:
        target_title = fg_title

    # Priority 3: When Claude is not running in foreground, prioritize physical PTY terminal title
    # (e.g. btop, yazi, vim, git, etc.) over stale historical session transcript
    if not target_title and terminal_title:
        clean_tt = sanitize_title(terminal_title)
        if clean_tt and clean_tt not in {"claude", "zsh", "bash", "sh", "None", "current session"}:
            target_title = clean_tt[:32]

    # Priority 4: Historical session transcript / custom-title.json
    # Only fallback to session transcript if terminal_title is generic shell/absent
    if not target_title and session_id and is_valid_cc_session(session_id):
        target_title = extract_cc_session_name(session_id, cwd)
        if target_title:
            state.set_assigned_title(session_id, target_title)

    # Priority 5: Fallback strictly to CWD basename (portable, non-home)
    if not target_title and cwd:
        abs_cwd = Path(cwd).resolve()
        if abs_cwd != Path.home().resolve() and abs_cwd != Path("/"):
            base = abs_cwd.name
            ignored = {"staff", "Desktop", "root", "~", "now", Path.home().name}
            if base and base not in ignored:
                target_title = base[:32]

    if not target_title:
        return False

    target_title = sanitize_title(target_title)
    updated = False

    # 1. Sync Pane Label (for sidebar and border fallback)
    if current_label != target_title:
        res = herdr_rpc("pane.rename", {"pane_id": pane_id, "label": target_title})
        if res and "result" in res:
            updated = True

    # 2. Sync Herdr Metadata Title (for Mac pane border & mobile Heeler companion card)
    if current_title != target_title:
        res = herdr_rpc("pane.report_metadata", {
            "pane_id": pane_id,
            "source": "herdr:claude",
            "title": target_title
        })
        if res and "result" in res:
            updated = True

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
    parser.add_argument("--reattach", action="store_true",
                        help="Startup: attach panes whose restart resume was refused for a running background session")

    args = parser.parse_args()
    state = StateManager(get_state_dir())

    if args.reattach:
        for pane_id, job_id in reattach_viewers_after_restore():
            print(f"Session Sync: {pane_id} -> claude attach {job_id}")
    elif args.event:
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
