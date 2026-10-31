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
import fcntl
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


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


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


def try_acquire_flock(lock_path: Path) -> int | None:
    """Non-blocking file lock to prevent concurrent process pile-ups on rapid events."""
    if "unittest" in sys.modules:
        return 0
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (BlockingIOError, OSError):
        return None


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

    # Extract inner user prompt from BotMux envelope if present
    if "<user_message>" in text:
        um_match = re.search(r"<user_message>(.*?)</user_message>", text, re.DOTALL)
        if not um_match:
            return None
        text = um_match.group(1).strip()
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
        or text.startswith("<role")
        or text.startswith("<whiteboard")
        or text.startswith("<sender")
        or text.startswith("<cross-session-message")
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
    # 1. Authoritative check: projects/*/{session_id}/custom-title.json under the config directory
    if session_id:
        ct_pattern = str(claude_config_dir() / "projects" / "*" / session_id / "custom-title.json")
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
        pattern = str(claude_config_dir() / "projects" / "*" / f"{session_id}.jsonl")
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
    matches = glob.glob(str(claude_config_dir() / "projects" / "*" / f"{session_id}.jsonl"))
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
    attached to, recorded in jobs/<jobId>/state.json under the config directory, whose `resumeSessionId`
    is what `claude --resume` can actually reopen.
    """
    if not job_id or not isinstance(job_id, str):
        return None, None
    if not re.match(r"^[0-9a-zA-Z_-]{1,64}$", job_id):
        return None, None
    state_file = claude_config_dir() / "jobs" / job_id / "state.json"
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
    for path in glob.glob(str(claude_config_dir() / "sessions" / "*.json")):
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


def _read_interactive_session(pid: int) -> dict:
    if not pid or not isinstance(pid, int) or pid <= 0:
        return {}
    session_file = claude_config_dir() / "sessions" / f"{pid}.json"
    if not os.path.isfile(session_file):
        return {}
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("kind") == "interactive":
            return data
    except Exception:
        pass
    return {}


def resolve_session_from_pid(pid: int, pane_name: str = "") -> tuple[str | None, str | None]:
    """Reads sessions/{pid}.json to extract authoritative Root Session ID and title."""
    return _resolve_interactive_session(_read_interactive_session(pid), pane_name)


def _resolve_interactive_session(data: dict, pane_name: str) -> tuple[str | None, str | None]:
    try:
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

        session_id = data.get("sessionId")
        if not isinstance(session_id, str) or not re.match(r"^[0-9a-fA-F-]{6,36}$", session_id.strip()):
            if not parked_job_id:
                return None, None
            session_id = None
        else:
            session_id = session_id.strip()
        title = extract_cc_session_name(session_id) if session_id else None
        if not title:
            name_source = data.get("nameSource")
            raw_name = data.get("name")
            # Only adopt data.get("name") if it is a real user/peer name, NOT an auto-derived directory slug (e.g. bytedance-5d)
            if raw_name and isinstance(raw_name, str) and name_source != "derived":
                title = sanitize_title(raw_name)[:32]
        return (None if parked_job_id else session_id), title
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


def resolve_title_from_foreground_process(pane_id: str, cwd: str = "", pane_name: str = "",
                                         screen_title: str = "") -> tuple[str | None, str | None]:
    """Inspects pane.process_info to resolve session_id and title directly from PID or CLI args (attach/--resume/--name).

    `screen_title` must come from the live screen only (never the pane label, which can be an
    echo of an earlier binding); it is used to correct a stale `attach` id in argv.
    """
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

            # 1. Authoritative check via process PID -> sessions/{pid}.json under the config directory
            proc_pid = proc.get("pid")
            if isinstance(proc_pid, int):
                data = _read_interactive_session(proc_pid)
                sid, title = _resolve_interactive_session(data, pane_name)
                if sid or data.get("parkedJobId"):
                    return sid, title

            for i, arg in enumerate(argv):
                flag, sep, inline_val = arg.partition("=")
                val = inline_val if sep else (argv[i + 1].strip() if i + 1 < len(argv) else "")
                if not val or val.startswith("-"):
                    continue

                if flag in {"attach", "--resume", "-r"}:
                    if re.match(r"^[0-9a-fA-F-]{6,36}$", val):
                        pattern = f"{val}.jsonl" if len(val) == 36 else f"{val}*.jsonl"
                        matches = glob.glob(str(claude_config_dir() / "projects" / "*" / pattern))
                        candidates = [
                            m for m in matches
                            if os.path.isfile(m) and ".orphaned-" not in os.path.basename(m)
                        ]
                        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        for m in candidates:
                            sid = os.path.basename(m)[:-6]
                            if is_valid_cc_session(sid):
                                title = extract_cc_session_name(sid, cwd)
                                # A `claude attach` viewer can switch to another session from
                                # its agents list without changing argv, so this id may be stale.
                                # If the screen shows a different title that exactly one live
                                # background session claims, that is what the pane displays.
                                if flag == "attach" and screen_title and title != screen_title:
                                    shown_sid, shown_title = resolve_live_bg_session_by_name(screen_title)
                                    if shown_sid and shown_sid != sid:
                                        return shown_sid, shown_title
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


# Whitespace is optional between words: lines are joined without separators, and Claude drops the
# space where it wraps a word onto the next line.
BG_REFUSAL_RE = re.compile(r"That\s*session\s*is\s*running\s*in\s*the\s*background\s*\(([0-9A-Za-z]{4,32})\)")


def find_bg_resume_refusal(text: str, session_id: str) -> str | None:
    """Returns the job id Claude refused to resume in this pane, if that is the pane's latest output.

    After a Herdr restart the pane reruns `claude --resume <session_id>`. When that
    session is still running in the background, Claude refuses with
    "That session is running in the background (<job>). Run `claude attach <job>` ..."
    and drops back to the shell -- the job id there is Claude's own answer to "what does
    this pane display". Accept it only when the echoed command right above is the
    `--resume` of this pane's session and at most the shell prompt follows the message;
    anything older is history, and attaching on history would type into whatever runs
    there now. Claude hard-wraps the message to the terminal width, so it ends at the first
    line closing with a period within eight lines. The id is typed into a shell, so
    it must be a plain alphanumeric token.
    """
    if not text or not session_id:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i in range(len(lines) - 1, -1, -1):
        end = next((j for j in range(i, min(i + 8, len(lines))) if lines[j].rstrip().endswith(".")), None)
        if end is None:
            continue
        m = BG_REFUSAL_RE.search("".join(lines[i:end + 1]))
        if not m:
            continue
        if len(lines) - 1 - end > 1 or i == 0:
            return None
        if "--resume" not in lines[i - 1] or session_id not in lines[i - 1]:
            return None
        job_id = m.group(1)
        return job_id if session_id.startswith(job_id) else None
    return None


def _pane_foreground_state(pane_id: str) -> str | None:
    """Returns claude, shell, or busy; None when process information is incomplete."""
    pinfo = herdr_rpc("pane.process_info", {"pane_id": pane_id})
    info = (pinfo.get("result") or {}).get("process_info") if pinfo else None
    if not isinstance(info, dict):
        return None
    foreground = info.get("foreground_processes")
    if not isinstance(foreground, list) or not foreground or not all(isinstance(proc, dict) for proc in foreground):
        return None
    if any(_is_claude_proc(proc) for proc in foreground):
        return "claude"
    shell_pid = info.get("shell_pid")
    if not isinstance(shell_pid, int) or shell_pid <= 0:
        return None
    return "shell" if all(proc.get("pid") == shell_pid for proc in foreground) else "busy"


def reattach_viewers_after_restore(timeout: float = 120.0, interval: float = 2.0, settle: float = 20.0,
                                   sleep=time.sleep, clock=time.monotonic) -> list[tuple[str, str]]:
    """Startup hook: run `claude attach <job>` where the restart's resume was refused.

    Herdr resumes a Claude pane with `claude --resume <id>` from its stored reference, and
    there is no way to change that command without a custom reporter holding the pane --
    which makes Herdr stop reading the screen and freezes the pane's status. So viewer panes
    keep an ordinary reference to the background session they display (see sync_pane), the
    resume is refused while that session still runs, and this hook follows Claude's own
    advice afterwards. A pane is attached at most once per startup, and only from a shell.
    Herdr drops a pane's reference shortly after its resume falls back to the shell, so the
    reference is remembered from the first listing that showed it. Panes running Claude
    continuously for 15 seconds are done; the loop ends once nothing is waiting after
    `settle` seconds, or at `timeout`. Failed attach commands are retried.
    """
    import subprocess

    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    attached: list[tuple[str, str]] = []
    handled: set[str] = set()
    known: dict[str, str] = {}
    claude_since: dict[str, float] = {}
    start = clock()
    while True:
        res = herdr_rpc("pane.list")
        panes = res.get("result", {}).get("panes", []) if res else []
        for pane in panes:
            ref = pane.get("agent_session") or {}
            if pane.get("pane_id") and ref.get("agent") == "claude" and ref.get("kind") == "id" \
                    and isinstance(ref.get("value"), str):
                known.setdefault(pane["pane_id"], ref["value"])
        live = {p.get("pane_id") for p in panes}
        for pane_id in set(claude_since) - live:
            claude_since.pop(pane_id)
        pending = 0
        for pane_id, sid in known.items():
            if pane_id in handled or pane_id not in live:
                continue
            foreground = _pane_foreground_state(pane_id)
            if foreground == "claude":
                now = clock()
                first_seen = claude_since.setdefault(pane_id, now)
                if now - first_seen >= 15:
                    handled.add(pane_id)
                else:
                    pending += 1
                continue
            claude_since.pop(pane_id, None)
            if foreground != "shell":
                pending += 1
                continue
            read = herdr_rpc("pane.read", {"pane_id": pane_id, "source": "recent_unwrapped", "lines": 12})
            text = read.get("result", {}).get("read", {}).get("text", "") if read else ""
            job_id = find_bg_resume_refusal(text, sid)
            if not job_id:
                pending += 1
                continue
            try:
                result = subprocess.run([herdr, "pane", "run", pane_id, f"claude attach {job_id}"],
                                        capture_output=True, timeout=5)
                if result.returncode == 0:
                    handled.add(pane_id)
                    attached.append((pane_id, job_id))
                    continue
            except Exception:
                pass
            pending += 1
        elapsed = clock() - start
        if elapsed >= timeout or (pending == 0 and elapsed >= settle):
            return attached
        sleep(interval)


HOOK_GUARD_ANCHOR = '[ -n "${HERDR_PANE_ID:-}" ] || exit 0\n'
HOOK_GUARD = (
    "# Added by herdr-session-sync (herdrdev/herdr#5261): a background session (`claude --bg`,\n"
    "# `/background`) inherits HERDR_PANE_ID from whatever terminal started Claude's daemon and\n"
    "# owns no pane, so reporting it binds an unrelated pane. Herdr rewrites this file on\n"
    "# integration install; the plugin's startup hook lays this line over it again.\n"
    '[ -z "${CLAUDE_JOB_DIR:-}" ] || exit 0\n'
)


def default_claude_hook_path() -> Path:
    return claude_config_dir() / "hooks" / "herdr-agent-state.sh"


def moshi_claude_sessions_dir() -> Path:
    raw = os.environ.get("MOSHI_CLAUDE_SESSIONS_DIR")
    if raw:
        return Path(raw)
    return Path.home() / "Library" / "Application Support" / "Moshi" / "claude-sessions"


def _write_json_atomic(path: Path, data: dict) -> bool:
    # Moshi keeps these files owner-only; keep the original mode instead of the umask default.
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.chmod(mode)
        tmp.replace(path)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        return False


def is_pane_explicit_attach(target_pane_id: str, target_sid: str) -> bool:
    """Checks if the target pane foreground process is explicitly `claude attach <sid>`."""
    if not target_pane_id or not target_sid:
        return False
    pinfo = herdr_rpc("pane.process_info", {"pane_id": target_pane_id}) or {}
    fg = (pinfo.get("result") or {}).get("process_info", {}).get("foreground_processes", [])
    prefix = target_sid[:8]
    for proc in fg:
        cmd = proc.get("cmdline") or ""
        argv = proc.get("argv") or []
        if f"attach {prefix}" in cmd or ("attach" in argv and any(a.startswith(prefix) for a in argv)):
            return True
    return False


def sync_moshi_pane_session(pane: dict, session_id: str | None) -> bool:
    """Ensure Moshi's claude-sessions/<session_id>.json records this Herdr pane and tab.

    Moshi's resolveHerdrAgentSession scores claude-sessions/*.json against (pane_id, tab_id):
    an exact (herdrPane, herdrTabId) match scores 200, while another pane in the same tab
    scores 120. Viewer panes (`claude attach`) run their conversation under cc-daemon and
    never write herdrPane through moshi-hook's shell-PID lookup, leaving them at score 0 and
    vulnerable to same-tab hijacking (or None-wiping when a parked viewer session is picked).
    """
    if "unittest" in sys.modules and not os.environ.get("MOSHI_CLAUDE_SESSIONS_DIR"):
        return False
    if not session_id or not isinstance(session_id, str):
        return False
    session_id = session_id.strip()
    if not re.match(r"^[0-9a-fA-F-]{6,36}$", session_id):
        return False
    pane_id = (pane.get("pane_id") or "").strip()
    if not pane_id:
        return False
    moshi_dir = moshi_claude_sessions_dir()
    if not moshi_dir.is_dir():
        return False

    tab_id = (pane.get("tab_id") or "").strip()
    workspace_id = (pane.get("workspace_id") or (pane_id.split(":")[0] if ":" in pane_id else "")).strip()
    cwd = (pane.get("foreground_cwd") or pane.get("cwd") or "").strip()

    state_file = moshi_dir / f"{session_id}.json"
    data: dict = {}
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            return False
    elif not is_valid_cc_session(session_id):
        return False

    # Moshi keeps one herdrPane per session. While the recorded pane still holds this
    # session, leave it: two panes showing the same session would otherwise take turns
    # overwriting it on every event. If Herdr cannot be asked, don't write.
    existing_pane = data.get("herdrPane")
    if existing_pane and existing_pane != pane_id:
        res = herdr_rpc("pane.get", {"pane_id": existing_pane})
        if not res:
            return False
        held = ((res.get("result") or {}).get("pane") or {}).get("agent_session") or {}
        if held.get("value") == session_id:
            return False

    if (
        data.get("terminalKind") == "herdr"
        and data.get("herdrPane") == pane_id
        and (not tab_id or data.get("herdrTabId") == tab_id)
        and (not workspace_id or data.get("herdrWorkspaceId") == workspace_id)
    ):
        return False

    data["terminalKind"] = "herdr"
    data["herdrPane"] = pane_id
    if workspace_id:
        data["herdrWorkspaceId"] = workspace_id
    if tab_id:
        data["herdrTabId"] = tab_id
    if cwd and not data.get("cwd"):
        data["cwd"] = cwd
    return _write_json_atomic(state_file, data)


def reconcile_moshi_claude_sessions(panes: list[dict]) -> int:
    """Stamp active Herdr pane bindings into Moshi state and strip stale herdr* fields."""
    if "unittest" in sys.modules and not os.environ.get("MOSHI_CLAUDE_SESSIONS_DIR"):
        return 0
    moshi_dir = moshi_claude_sessions_dir()
    if not moshi_dir.is_dir():
        return 0

    # Every session a pane holds keeps its binding; only those with a transcript get stamped.
    # A just-started session has none until its first prompt, but moshi-hook already recorded
    # its pane -- stripping that would hand the pane to a same-tab session.
    held_sids: set[str] = set()
    holders_by_sid: dict[str, list[dict]] = {}
    for p in panes:
        pane_id = (p.get("pane_id") or "").strip()
        ref = p.get("agent_session") or {}
        sid = (ref.get("value") or "").strip() if isinstance(ref.get("value"), str) else ""
        if not (pane_id and sid):
            continue
        held_sids.add(sid)
        if is_valid_cc_session(sid):
            holders_by_sid.setdefault(sid, []).append(p)

    # Several panes can hold one session; Moshi records only one. Keep the recorded pane
    # while it still holds the session, else prefer an explicit `claude attach <sid>`.
    active_pane_by_sid: dict[str, dict] = {}
    for sid, holders in holders_by_sid.items():
        chosen = holders[0]
        if len(holders) > 1:
            recorded_pane = None
            try:
                recorded_pane = json.loads((moshi_dir / f"{sid}.json").read_text(encoding="utf-8")).get("herdrPane")
            except Exception:
                pass
            recorded = [p for p in holders if p.get("pane_id") == recorded_pane]
            preferred = recorded or [p for p in holders if is_pane_explicit_attach(p.get("pane_id"), sid)]
            if preferred:
                chosen = preferred[0]
        active_pane_by_sid[sid] = chosen

    changed = 0
    for sid, p in active_pane_by_sid.items():
        if sync_moshi_pane_session(p, sid):
            changed += 1

    herdr_keys = ("herdrPane", "herdrTabId", "herdrTab", "herdrWorkspaceId", "herdrWorkspace")
    for path in moshi_dir.glob("*.json"):
        sid = path.stem
        if sid in held_sids:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not any(k in data for k in herdr_keys) and data.get("terminalKind") != "herdr":
            continue
        for k in herdr_keys:
            data.pop(k, None)
        if data.get("terminalKind") == "herdr":
            data.pop("terminalKind", None)
        if _write_json_atomic(path, data):
            changed += 1

    return changed


def ensure_hook_guard(hook_path: Path) -> str:
    """Startup hook: keep Herdr's own Claude hook, plus a guard against background sessions.

    The file stays Herdr's (so `herdr integration install` keeps it current); only one guard
    line is laid over it, right after the shipped HERDR_PANE_ID check. A file that already
    checks CLAUDE_JOB_DIR is left alone, and so is one whose anchor line changed -- patching
    an unknown shape could break the hook, a missing guard only brings back herdr#5261.
    """
    try:
        content = hook_path.read_text(encoding="utf-8")
    except OSError:
        return "no-hook"
    if "HERDR_INTEGRATION_ID=claude" not in content:
        return "not-claude-hook"
    if "CLAUDE_JOB_DIR" in content:
        return "already-guarded"
    if content.count(HOOK_GUARD_ANCHOR) != 1:
        return "anchor-missing"
    patched = content.replace(HOOK_GUARD_ANCHOR, HOOK_GUARD_ANCHOR + HOOK_GUARD)
    mode = hook_path.stat().st_mode & 0o777
    tmp = hook_path.with_name(f".{hook_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(patched, encoding="utf-8")
        tmp.chmod(mode)
        tmp.replace(hook_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        return "write-failed"
    return "patched"


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
    fg_sid, fg_title = resolve_title_from_foreground_process(pane_id, cwd, screen_title or current_title or current_label,
                                                             screen_title=screen_title or "")
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
            pane["agent_session"] = {
                "source": "herdr:claude",
                "agent": "claude",
                "kind": "id",
                "value": fg_sid,
            }

        if fg_title and not target_title:
            target_title = fg_title
            state.set_assigned_title(session_id, target_title)
    elif fg_title and not target_title:
        target_title = fg_title

    if session_id:
        sync_moshi_pane_session(pane, session_id)

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


def sync_all(state: StateManager, force_moshi_sweep: bool = False):
    now = time.time()
    state.data["last_full_sync"] = now
    res = herdr_rpc("pane.list")
    if not res or "result" not in res:
        return
    panes = res.get("result", {}).get("panes", [])
    count = 0
    for p in panes:
        if sync_pane(p, state):
            count += 1
    # Moshi directory-wide sweep reads all session JSONs on disk (~700+ files).
    # Throttle full sweeps to at most once per 5 minutes unless forced.
    if force_moshi_sweep or (now - state.data.get("last_moshi_sweep", 0) >= 300.0):
        reconcile_moshi_claude_sessions(panes)
        state.data["last_moshi_sweep"] = now
    else:
        # Fast path (<5ms): only stamp active panes, avoiding directory crawl
        for p in panes:
            ref = p.get("agent_session") or {}
            sid = (ref.get("value") or "").strip() if isinstance(ref.get("value"), str) else ""
            if sid and is_valid_cc_session(sid):
                sync_moshi_pane_session(p, sid)
    state.save()
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
    parser.add_argument("--ensure-hook-guard", action="store_true",
                        help="Startup: lay the background-session guard over Herdr's Claude hook")

    args = parser.parse_args()
    state = StateManager(get_state_dir())

    if args.ensure_hook_guard:
        hook = default_claude_hook_path()
        print(f"Session Sync: {hook} -> {ensure_hook_guard(hook)}")
    elif args.reattach:
        for pane_id, job_id in reattach_viewers_after_restore():
            print(f"Session Sync: {pane_id} -> claude attach {job_id}")
    elif args.event:
        lock_fd = try_acquire_flock(get_state_dir() / "event.lock")
        if lock_fd is None:
            # Another event handler is currently syncing; skip to prevent process pile-up
            return
        try:
            # A manual `herdr integration install claude` drops the guard mid-session; every
            # event re-checks it (one small file read), so it does not wait for a server restart.
            ensure_hook_guard(default_claude_hook_path())
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
                                # If all panes have been refreshed recently, return immediately (<5ms).
                                # Otherwise, fall through to fast-path sync_all to keep idle panes fresh.
                                if time.time() - state.data.get("last_full_sync", 0) < 60.0:
                                    return
                except Exception:
                    pass
            # Run fast-path sync_all if at least 60s has passed
            if time.time() - state.data.get("last_full_sync", 0) >= 60.0:
                sync_all(state)
        finally:
            if lock_fd:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                except OSError:
                    pass
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
        sync_all(state, force_moshi_sweep=True)


if __name__ == "__main__":
    main()
