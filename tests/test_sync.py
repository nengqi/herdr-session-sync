import unittest
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from sync import clean_task_title, sanitize_title, extract_cc_session_name


class TestSyncTitle(unittest.TestCase):
    def test_clean_grok_handoff(self):
        text = "【从 Grok 接管任务：OOS-词性缓存】请继续处理"
        self.assertEqual(clean_task_title(text), "OOS-词性缓存")

        text_with_spaces = "【从 Grok 接管任务：供给诊断 出词看后搜】请继续"
        self.assertEqual(clean_task_title(text_with_spaces), "供给诊断 出词看后搜")

    def test_clean_slash_command(self):
        text = "/codex:review 审查最近的代码变更"
        self.assertEqual(clean_task_title(text), "审查最近的代码变更")

        text_rename = "/rename 我的新任务"
        self.assertEqual(clean_task_title(text_rename), "我的新任务")

        # Utility commands should be filtered out even with arguments
        self.assertIsNone(clean_task_title("/compact please"))
        self.assertIsNone(clean_task_title("/help search"))

    def test_clean_markdown_and_bullets(self):
        text = "### 1. 排查并修复 Heeler 连接问题\n详细步骤如下..."
        self.assertEqual(clean_task_title(text), "排查并修复 Heeler 连接问题")

        text_alpha = "b. CSI 词供给方案与数据验证白皮书\n正文开始"
        self.assertEqual(clean_task_title(text_alpha), "CSI 词供给方案与数据验证白皮书")

        text_paren = "(a) 搜索词召回方案"
        self.assertEqual(clean_task_title(text_paren), "搜索词召回方案")

        text_dash = "相关性 MCP - "
        self.assertEqual(clean_task_title(text_dash), "相关性 MCP")

        text_emdash = "相关性 MCP —"
        self.assertEqual(clean_task_title(text_emdash), "相关性 MCP")

        # Balanced tags should not produce dangling brackets
        self.assertEqual(clean_task_title("- [x] Fix login bug"), "Fix login bug")
        self.assertEqual(clean_task_title("[Feature] Add auth system"), "[Feature] Add auth system")

        # Quotes and backticks should be stripped cleanly
        self.assertEqual(clean_task_title("`git status` failing with error"), "git status` failing with error")
        self.assertEqual(clean_task_title('"Fix build error" now'), 'Fix build error" now')
        self.assertEqual(clean_task_title("• 排查并修复网络故障"), "排查并修复网络故障")
        self.assertEqual(clean_task_title("· 整理 memory 文件"), "整理 memory 文件")

    def test_filter_system_and_greetings(self):
        self.assertIsNone(clean_task_title("好的"))
        self.assertIsNone(clean_task_title("<system-reminder>test</system-reminder>"))
        self.assertIsNone(clean_task_title("Base directory for this skill: /Users/bytedance/.claude/skills/chat-catchup"))
        self.assertIsNone(clean_task_title("ok"))

    def test_clean_xml_command(self):
        text = "<command-message>chat-catchup</command-message>\n<command-name>/chat-catchup</command-name>\n<command-args>oc_0553988837b02670ab7e68a1caea83ea</command-args>"
        self.assertEqual(clean_task_title(text), "chat-catchup oc_0553988837b02670")

        text_msg_only = "<command-message>chat-catchup</command-message>"
        self.assertEqual(clean_task_title(text_msg_only), "chat-catchup")

    def test_sanitize_title(self):
        self.assertEqual(sanitize_title("normal title"), "normal title")
        self.assertEqual(sanitize_title("title\x07\x1b[6ninjected"), "titleinjected")
        self.assertEqual(sanitize_title("\033[31mRed\033[0m"), "Red")
        self.assertEqual(sanitize_title("title - — : "), "title")

    def test_deterministic_fallback_no_cross_pollution(self):
        # When session_id is empty/none, extract_cc_session_name must NOT scan other transcripts
        self.assertIsNone(extract_cc_session_name("", cwd=""))
        self.assertEqual(extract_cc_session_name("", cwd="/Users/bytedance/go/src/github.com/nengqi/Heeler"), "Heeler")

    def test_resolve_title_from_foreground_process_filtering(self):
        from unittest.mock import patch
        from sync import resolve_title_from_foreground_process

        # Case 1: Non-claude command mentioning claude path (e.g. grep -r todo ~/.claude) must NOT hijack title
        mock_pinfo_grep = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {"argv0": "grep", "argv": ["grep", "-r", "todo", "/Users/bytedance/.claude"], "name": "grep"}
                    ]
                }
            }
        }
        with patch("sync.herdr_rpc", return_value=mock_pinfo_grep):
            sid, title = resolve_title_from_foreground_process("w1:p1")
            self.assertIsNone(sid)
            self.assertIsNone(title)

        # Case 2: Claude with bare --resume and trailing flags must NOT capture the flag as a title
        mock_pinfo_bare_flag = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {"argv0": "claude", "argv": ["claude", "--resume", "--dangerously-skip-permissions"], "name": "claude"}
                    ]
                }
            }
        }
        with patch("sync.herdr_rpc", return_value=mock_pinfo_bare_flag):
            sid, title = resolve_title_from_foreground_process("w1:p1")
            self.assertIsNone(sid)
            self.assertIsNone(title)

        # Case 3: Claude with explicit custom --name
        mock_pinfo_name = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {"argv0": "claude", "argv": ["claude", "--name", "Fix critical bug"], "name": "claude"}
                    ]
                }
            }
        }
        with patch("sync.herdr_rpc", return_value=mock_pinfo_name):
            sid, title = resolve_title_from_foreground_process("w1:p1")
            self.assertIsNone(sid)
            self.assertEqual(title, "Fix critical bug")


        # Case 4: Non-claude command with recycled PID matching stale session file must NOT be resolved
        mock_pinfo_recycled = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {"argv0": "btop", "argv": ["btop"], "name": "btop", "pid": 9999}
                    ]
                }
            }
        }
        with patch("sync.herdr_rpc", return_value=mock_pinfo_recycled), \
             patch("sync.resolve_session_from_pid") as mock_res_pid:
            sid, title = resolve_title_from_foreground_process("w1:p1")
            self.assertIsNone(sid)
            self.assertIsNone(title)
            # resolve_session_from_pid must never be called for non-Claude process
            mock_res_pid.assert_not_called()

        # Case 5: Resume argument skips orphaned cleanup transcripts and selects newest
        mock_pinfo_resume = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {"argv0": "claude", "argv": ["claude", "--resume", "12345678-1234-1234-1234-123456789abc"], "name": "claude", "pid": 0}
                    ]
                }
            }
        }
        orphaned_path = "/path/projects/p1/12345678-1234-1234-1234-123456789abc.orphaned-123.jsonl"
        valid_path = "/path/projects/p1/12345678-1234-1234-1234-123456789abc.jsonl"
        with patch("sync.herdr_rpc", return_value=mock_pinfo_resume), \
             patch("sync.glob.glob", return_value=[orphaned_path, valid_path]), \
             patch("sync.os.path.isfile", return_value=True), \
             patch("sync.os.path.getmtime", side_effect=lambda p: 200 if p == valid_path else 100), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value="Resume Task"):
            sid, title = resolve_title_from_foreground_process("w1:p1")
            self.assertEqual(sid, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(title, "Resume Task")

    def test_portable_home_dir_fallback(self):
        from pathlib import Path
        # Opening shell directly in user's home directory must return None, not the username
        home_dir = str(Path.home())
        self.assertIsNone(extract_cc_session_name("", cwd=home_dir))

    def test_is_valid_cc_session(self):
        from unittest.mock import patch
        from sync import is_valid_cc_session

        # Invalid format
        self.assertFalse(is_valid_cc_session(""))
        self.assertFalse(is_valid_cc_session("../evil/path"))
        self.assertFalse(is_valid_cc_session("not-hex-@#$"))

        # Valid format but no files
        with patch("sync.glob.glob", return_value=[]):
            self.assertFalse(is_valid_cc_session("12345678-1234-1234-1234-123456789abc"))

        # Valid format, file exists but 0 bytes (corrupted/ghost)
        with patch("sync.glob.glob", return_value=["/path/to/transcript.jsonl"]), \
             patch("sync.os.path.isfile", return_value=True), \
             patch("sync.os.path.getsize", return_value=0):
            self.assertFalse(is_valid_cc_session("12345678-1234-1234-1234-123456789abc"))

        # Valid format and non-empty file
        with patch("sync.glob.glob", return_value=["/path/to/transcript.jsonl"]), \
             patch("sync.os.path.isfile", return_value=True), \
             patch("sync.os.path.getsize", return_value=1024):
            self.assertTrue(is_valid_cc_session("12345678-1234-1234-1234-123456789abc"))

    def test_resolve_session_from_pid(self):
        import json
        from unittest.mock import patch, mock_open
        from sync import resolve_session_from_pid

        # Invalid pid
        self.assertEqual(resolve_session_from_pid(0), (None, None))
        self.assertEqual(resolve_session_from_pid(-1), (None, None))

        # Pid file does not exist
        with patch("sync.os.path.isfile", return_value=False):
            self.assertEqual(resolve_session_from_pid(1234), (None, None))

        # Valid interactive session file with custom extracted title
        valid_interactive_json = json.dumps({
            "pid": 1234,
            "kind": "interactive",
            "sessionId": "12345678-1234-1234-1234-123456789abc",
            "name": "Fallback Name"
        })
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=valid_interactive_json)), \
             patch("sync.extract_cc_session_name", return_value="Extracted Title"):
            sid, title = resolve_session_from_pid(1234)
            self.assertEqual(sid, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(title, "Extracted Title")

        # Freshly started session before transcript exists: falls back to JSON 'name'
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=valid_interactive_json)), \
             patch("sync.extract_cc_session_name", return_value=None):
            sid, title = resolve_session_from_pid(1234)
            self.assertEqual(sid, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(title, "Fallback Name")

        # Non-interactive session (e.g. kind="bg" background subagent/worker) must be rejected
        bg_json = json.dumps({
            "pid": 1234,
            "kind": "bg",
            "sessionId": "12345678-1234-1234-1234-123456789abc",
            "name": "bg-worker"
        })
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=bg_json)):
            self.assertEqual(resolve_session_from_pid(1234), (None, None))

    def test_sync_pane_auto_heals_drifted_session(self):
        from unittest.mock import patch, MagicMock
        from sync import sync_pane, StateManager

        mock_state = MagicMock(spec=StateManager)
        mock_pane = {
            "pane_id": "w1:p1",
            "label": "Old Label",
            "title": "Old Title",
            "agent_session": {"value": "ghost-subagent-uuid-000"},
            "cwd": "/Users/bytedance/project",
        }

        rpc_calls = []
        def fake_herdr_rpc(method, params=None, timeout=1.5):
            rpc_calls.append((method, params))
            return {"result": {"ok": True}}

        with patch("sync.resolve_title_from_foreground_process", return_value=("authoritative-uuid-1234", "Authoritative Title")), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.herdr_rpc", side_effect=fake_herdr_rpc):
            updated = sync_pane(mock_pane, mock_state)
            self.assertTrue(updated)

            # Assert pane.report_agent_session was called with authoritative id, seq, and resume source
            report_calls = [c for c in rpc_calls if c[0] == "pane.report_agent_session"]
            self.assertEqual(len(report_calls), 1)
            p = report_calls[0][1]
            self.assertEqual(p["pane_id"], "w1:p1")
            self.assertEqual(p["agent_session_id"], "authoritative-uuid-1234")
            self.assertIsInstance(p.get("seq"), int)
            self.assertGreater(p.get("seq"), 0)
            self.assertEqual(p.get("session_start_source"), "resume")

            # Assert pane.rename and pane.report_metadata were also called
            rename_calls = [c for c in rpc_calls if c[0] == "pane.rename"]
            self.assertEqual(len(rename_calls), 1)
            self.assertEqual(rename_calls[0][1]["label"], "Authoritative Title")

    def test_sync_pane_heals_binding_even_when_screen_title_found(self):
        """A viewer pane always shows a readline title, so the binding heal must not hide
        behind the title lookup; the screen title is also the best name for the bg join."""
        from unittest.mock import patch, MagicMock
        from sync import sync_pane, StateManager

        mock_pane = {"pane_id": "w1:p9", "label": "周会文档", "title": "周会文档",
                     "agent_session": {"value": "stale000-0000-0000-0000-000000000000"},
                     "cwd": "/Users/bytedance"}
        rpc_calls = []

        def fake_herdr_rpc(method, params=None, timeout=1.5):
            rpc_calls.append((method, params))
            return {"result": {"ok": True}}

        with patch("sync.extract_title_from_screen", return_value="周会文档"), \
             patch("sync.resolve_title_from_foreground_process",
                   return_value=("bbbb9999-9999-9999-9999-999999999999", "周会文档")) as mock_fg, \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.herdr_rpc", side_effect=fake_herdr_rpc):
            sync_pane(mock_pane, MagicMock(spec=StateManager))
            self.assertEqual(mock_fg.call_args[0][2], "周会文档")
            reports = [p for m, p in rpc_calls if m == "pane.report_agent_session"]
            self.assertEqual([p["agent_session_id"] for p in reports],
                             ["bbbb9999-9999-9999-9999-999999999999"])

    def test_sync_pane_prefers_terminal_title_when_claude_exited(self):
        from unittest.mock import patch, MagicMock
        from sync import sync_pane, StateManager

        mock_state = MagicMock(spec=StateManager)
        mock_pane = {
            "pane_id": "w1:p2",
            "label": "Old Claude Task",
            "title": "Old Claude Task",
            "terminal_title": "btop",
            # Even when agent_session points to a valid historical transcript on disk
            "agent_session": {"value": "historical-session-uuid-1234"},
            "cwd": "/Users/bytedance/project",
        }

        rpc_calls = []
        def fake_herdr_rpc(method, params=None, timeout=1.5):
            rpc_calls.append((method, params))
            return {"result": {"ok": True}}

        # When Claude is not in foreground, resolve_title_from_foreground_process returns (None, None)
        # Even if is_valid_cc_session is True (transcript exists), terminal_title 'btop' MUST be preferred
        with patch("sync.resolve_title_from_foreground_process", return_value=(None, None)), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name") as mock_extract, \
             patch("sync.herdr_rpc", side_effect=fake_herdr_rpc):
            updated = sync_pane(mock_pane, mock_state)
            self.assertTrue(updated)
            # Must NOT attempt to look up stale transcript when terminal has active tool title
            mock_extract.assert_not_called()
            # Must rename pane to terminal_title 'btop'
            rename_calls = [c for c in rpc_calls if c[0] == "pane.rename"]
            self.assertEqual(rename_calls[0][1]["label"], "btop")

    def test_get_state_dir_catches_oserror(self):
        from unittest.mock import patch
        from sync import get_state_dir

        with patch("pathlib.Path.mkdir", side_effect=OSError("Read-only file system")):
            # Must catch OSError gracefully and still return Path without crashing
            path = get_state_dir()
            self.assertIsNotNone(path)

    def test_cwd_fallback_dynamic_username_filtering(self):
        from unittest.mock import patch
        from pathlib import Path

        # Dynamic filtering of Path.home().name without hardcoding username
        mock_home = Path("/home/testuser")
        with patch("pathlib.Path.home", return_value=mock_home):
            self.assertIsNone(extract_cc_session_name("", cwd=str(mock_home)))
            self.assertEqual(extract_cc_session_name("", cwd="/home/testuser/herdr"), "herdr")


    def test_sync_pane_skips_unwritten_session(self):
        """A foreground session whose transcript .jsonl does not exist yet must NOT be
        persisted to Herdr, otherwise `claude --resume` fails after a restart with
        "No conversation found with session ID"."""
        from unittest.mock import patch, MagicMock
        from sync import sync_pane, StateManager

        mock_state = MagicMock(spec=StateManager)
        mock_pane = {
            "pane_id": "w1:p1",
            "label": "Old Label",
            "title": "Old Title",
            "agent_session": {"value": "previous-uuid-000"},
            "cwd": "/Users/bytedance/project",
        }

        rpc_calls = []

        def fake_herdr_rpc(method, params=None, timeout=1.5):
            rpc_calls.append((method, params))
            return {"result": {"ok": True}}

        with patch("sync.resolve_title_from_foreground_process", return_value=("unwritten-uuid-9999", "Some Title")), \
             patch("sync.is_valid_cc_session", return_value=False), \
             patch("sync.herdr_rpc", side_effect=fake_herdr_rpc):
            sync_pane(mock_pane, mock_state)
            report_calls = [c for c in rpc_calls if c[0] == "pane.report_agent_session"]
            self.assertEqual(len(report_calls), 0, "unwritten session must not be reported to Herdr")


    def test_viewer_pid_resolves_to_parked_job_session(self):
        """An interactive process with no transcript is a viewer; report the background
        job's durable session (state.json resumeSessionId), not its own dead UUID."""
        from unittest.mock import patch
        import io
        from sync import resolve_session_from_pid

        viewer = json.dumps({"pid": 111, "kind": "interactive",
                             "sessionId": "dead0000-0000-0000-0000-000000000000",
                             "parkedJobId": "job1234"})
        job = json.dumps({"resumeSessionId": "aaaa1111-1111-1111-1111-111111111111",
                          "name": "周会文档", "nameSource": "user"})

        def fake_open(path, *a, **k):
            return io.StringIO(job if "/jobs/" in str(path) else viewer)

        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", side_effect=fake_open), \
             patch("sync.is_valid_cc_session", side_effect=lambda s: s.startswith(("aaaa","bbbb"))), \
             patch("sync.extract_cc_session_name", return_value=None):
            sid, title = resolve_session_from_pid(111)
            self.assertEqual(sid, "aaaa1111-1111-1111-1111-111111111111")
            self.assertEqual(title, "周会文档")

    def test_viewer_with_own_transcript_still_resolves_to_bg_session(self):
        """A viewer started as `claude --resume X` has a transcript of its own, yet it
        displays the background session it was parked into. Binding the pane to X makes a
        Herdr restart reopen the stale pre-park conversation, so parkedJobId wins."""
        from unittest.mock import patch
        import io
        from sync import resolve_session_from_pid

        viewer = json.dumps({"pid": 112, "kind": "interactive",
                             "sessionId": "eeee0000-0000-0000-0000-000000000000",
                             "parkedJobId": "job5678"})
        job = json.dumps({"resumeSessionId": "aaaa5678-1111-1111-1111-111111111111",
                          "name": "install-herdr", "nameSource": "user"})

        def fake_open(path, *a, **k):
            return io.StringIO(job if "/jobs/" in str(path) else viewer)

        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", side_effect=fake_open), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value=None):
            sid, _ = resolve_session_from_pid(112)
            self.assertEqual(sid, "aaaa5678-1111-1111-1111-111111111111")

    def test_viewer_pid_falls_back_to_live_bg_session_by_name(self):
        """When parkedJobId is a dead pointer, join the pane's displayed name to the
        live background session that reported the same name."""
        from unittest.mock import patch, mock_open
        from sync import resolve_session_from_pid

        viewer = json.dumps({"pid": 222, "kind": "interactive",
                             "sessionId": "dead0000-0000-0000-0000-000000000000",
                             "parkedJobId": "gone"})
        with patch("sync.os.path.isfile", side_effect=lambda p: "jobs" not in str(p)), \
             patch("builtins.open", mock_open(read_data=viewer)), \
             patch("sync.is_valid_cc_session", side_effect=lambda s: s.startswith(("aaaa","bbbb"))), \
             patch("sync.resolve_live_bg_session_by_name",
                   return_value=("bbbb2222-2222-2222-2222-222222222222", "周会文档")):
            sid, title = resolve_session_from_pid(222, "周会文档")
            self.assertEqual(sid, "bbbb2222-2222-2222-2222-222222222222")

    def test_bg_name_match_requires_unique_live_session(self):
        """An ambiguous name must never rebind a pane; a dead pid must not match."""
        from unittest.mock import patch, mock_open
        from sync import resolve_live_bg_session_by_name

        two = json.dumps({"kind": "bg", "pid": 900, "name": "dup", "nameSource": "peer",
                          "sessionId": "aaaa1111-1111-1111-1111-111111111111"})
        with patch("sync.glob.glob", return_value=["/s/1.json", "/s/2.json"]), \
             patch("builtins.open", mock_open(read_data=two)), \
             patch("sync._is_live_claude_process", return_value=True), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value="dup"):
            self.assertEqual(resolve_live_bg_session_by_name("dup"), (None, None))

        with patch("sync.glob.glob", return_value=["/s/1.json"]), \
             patch("builtins.open", mock_open(read_data=two)), \
             patch("sync._is_live_claude_process", return_value=False), \
             patch("sync.is_valid_cc_session", return_value=True):
            self.assertEqual(resolve_live_bg_session_by_name("dup"), (None, None))

        self.assertEqual(resolve_live_bg_session_by_name(""), (None, None))

    def test_bg_name_match_rejects_derived_name(self):
        """A directory-derived name is not an identity and must not be used to join."""
        from unittest.mock import patch, mock_open
        from sync import resolve_live_bg_session_by_name

        derived = json.dumps({"kind": "bg", "pid": 901, "name": "bytedance-d1",
                              "nameSource": "derived",
                              "sessionId": "bbbb1111-1111-1111-1111-111111111111"})
        with patch("sync.glob.glob", return_value=["/s/1.json"]), \
             patch("builtins.open", mock_open(read_data=derived)), \
             patch("sync._is_live_claude_process", return_value=True), \
             patch("sync.is_valid_cc_session", return_value=True):
            self.assertEqual(resolve_live_bg_session_by_name("bytedance-d1"), (None, None))

    def test_attach_viewer_switched_in_agents_list_follows_screen_title(self):
        """`claude attach A` can switch to session B from its agents list without changing
        argv, and the `claude agents` child leaves no sessions/<pid>.json. When the screen
        title names a different live background session, bind to that one, not stale A."""
        from unittest.mock import patch
        from sync import resolve_title_from_foreground_process

        old_sid = "9c9f55f1-30b0-4ad1-8241-632561c0a677"
        shown_sid = "6db10eb2-9126-4d4f-b1ae-4be8280c31c7"
        procs = [
            {"pid": 97309, "argv0": "claude", "name": "2.1.301",
             "argv": ["/Users/u/.local/bin/claude", "agents", "--settings", "/Users/u/s.json"]},
            {"pid": 97304, "argv0": "sh", "name": "bash",
             "argv": ["/bin/sh", "/usr/bin/command", "claude", "--settings", "/Users/u/s.json",
                      "attach", old_sid[:8]]},
        ]
        pinfo = {"result": {"process_info": {"foreground_processes": procs}}}

        for screen, name_hit, expected in (
            ("loop Astergate Gemini Claude", (shown_sid, "loop Astergate Gemini Claude"), shown_sid),
            ("loop 工作上下文", (old_sid, "loop 工作上下文"), old_sid),   # screen agrees with argv
            ("", (shown_sid, "x"), old_sid),                              # no screen title: keep argv
            ("loop Astergate Gemini Claude", (None, None), old_sid),      # name not unique/live: keep argv
        ):
            with self.subTest(screen=screen, name_hit=name_hit), \
                 patch("sync.herdr_rpc", return_value=pinfo), \
                 patch("sync._read_interactive_session", return_value={}), \
                 patch("sync.glob.glob", return_value=[f"/p/x/{old_sid}.jsonl"]), \
                 patch("sync.os.path.isfile", return_value=True), \
                 patch("sync.os.path.getmtime", return_value=0), \
                 patch("sync.is_valid_cc_session", return_value=True), \
                 patch("sync.extract_cc_session_name", return_value="loop 工作上下文"), \
                 patch("sync.resolve_live_bg_session_by_name", return_value=name_hit) as by_name:
                sid, _ = resolve_title_from_foreground_process("w1:pQ5", "/Users/u", screen or "label",
                                                               screen_title=screen)
                self.assertEqual(sid, expected)
                if screen in ("", "loop 工作上下文"):
                    by_name.assert_not_called()

        # Only an attach viewer can switch sessions; a plain `--resume` keeps its argv id.
        resume = {"result": {"process_info": {"foreground_processes": [
            {"pid": 1, "argv0": "claude", "argv": ["claude", "--resume", old_sid]}]}}}
        with patch("sync.herdr_rpc", return_value=resume), \
             patch("sync._read_interactive_session", return_value={}), \
             patch("sync.glob.glob", return_value=[f"/p/x/{old_sid}.jsonl"]), \
             patch("sync.os.path.isfile", return_value=True), \
             patch("sync.os.path.getmtime", return_value=0), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value="loop 工作上下文"), \
             patch("sync.resolve_live_bg_session_by_name", return_value=(shown_sid, "x")) as by_name:
            sid, _ = resolve_title_from_foreground_process("w1:p1", "", "loop Astergate Gemini Claude",
                                                           screen_title="loop Astergate Gemini Claude")
            self.assertEqual(sid, old_sid)
            by_name.assert_not_called()


    def test_fresh_unwritten_session_is_not_rebound_by_name(self):
        """A session nobody has typed into yet has no transcript, but it is not a viewer.
        Without a parkedJobId it must keep its own id -- never adopt a background session
        that merely shares the pane title."""
        from unittest.mock import patch, mock_open
        from sync import resolve_session_from_pid

        fresh = json.dumps({"pid": 333, "kind": "interactive",
                            "sessionId": "cccc3333-3333-3333-3333-333333333333",
                            "name": "周会文档", "nameSource": "peer"})
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=fresh)), \
             patch("sync.is_valid_cc_session", return_value=False), \
             patch("sync.extract_cc_session_name", return_value=None), \
             patch("sync.resolve_live_bg_session_by_name") as mock_name, \
             patch("sync.resolve_parked_job_session") as mock_job:
            sid, _ = resolve_session_from_pid(333, "周会文档")
            self.assertEqual(sid, "cccc3333-3333-3333-3333-333333333333")
            mock_name.assert_not_called()
            mock_job.assert_not_called()

    def test_unresolved_viewer_preserves_existing_binding(self):
        from unittest.mock import patch, mock_open, MagicMock
        from sync import resolve_session_from_pid, resolve_title_from_foreground_process, sync_pane, StateManager

        viewer_sid = "dead0000-0000-0000-0000-000000000000"
        bound_sid = "aaaa1111-1111-1111-1111-111111111111"
        pane = {"pane_id": "w1:p1", "label": "Viewed Task", "title": "Viewed Task",
                "agent_session": {"value": bound_sid}}
        process = {"pid": 222, "argv0": "claude", "argv": ["claude", "--resume", viewer_sid]}
        viewer = {"kind": "interactive", "sessionId": viewer_sid, "parkedJobId": "gone"}

        def fake_rpc(method, params=None, timeout=1.5):
            if method == "pane.process_info":
                return {"result": {"process_info": {"foreground_processes": [process]}}}
            return {"result": {"ok": True}}

        for title in ("Viewer Title", None):
            with self.subTest(title=title), \
                 patch("sync.os.path.isfile", return_value=True), \
                 patch("builtins.open", mock_open(read_data=json.dumps(viewer))), \
                 patch("sync.resolve_parked_job_session", return_value=(None, None)), \
                 patch("sync.resolve_live_bg_session_by_name", return_value=(None, None)), \
                 patch("sync.extract_cc_session_name", return_value=title), \
                 patch("sync.glob.glob", return_value=[f"/projects/task/{viewer_sid}.jsonl"]), \
                 patch("sync.os.path.getmtime", return_value=1), \
                 patch("sync.is_valid_cc_session", return_value=True), \
                 patch("sync.herdr_rpc", side_effect=fake_rpc) as mock_rpc:
                self.assertEqual(resolve_session_from_pid(222, "Viewed Task"), (None, title))
                self.assertEqual(resolve_title_from_foreground_process("w1:p1"), (None, title))
                sync_pane(pane, MagicMock(spec=StateManager))
                reports = [call for call in mock_rpc.call_args_list
                           if call.args[0] == "pane.report_agent_session"]
                self.assertEqual(reports, [])

    def test_attach_argv_resolves_without_session_metadata(self):
        from unittest.mock import patch
        from sync import resolve_title_from_foreground_process

        session_id = "aaaa1111-1111-1111-1111-111111111111"
        transcript = f"/projects/task/{session_id}.jsonl"
        process = {"pid": 222, "argv0": "claude",
                   "argv": ["claude", "--dangerously-skip-permissions", "attach", session_id]}
        with patch("sync.herdr_rpc", return_value={
                "result": {"process_info": {"foreground_processes": [process]}}}), \
             patch("sync.os.path.isfile", side_effect=lambda path: str(path) == transcript), \
             patch("sync.glob.glob", return_value=[transcript]), \
             patch("sync.os.path.getmtime", return_value=1), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value="Attached Task"):
            self.assertEqual(resolve_title_from_foreground_process("w1:p1"),
                             (session_id, "Attached Task"))

    def test_find_bg_resume_refusal(self):
        """Herdr's restart reruns `claude --resume <id>`; for a session still running in
        the background Claude refuses and names the job to attach. Only that exact,
        latest refusal for the pane's own session may trigger an attach."""
        from sync import find_bg_resume_refusal

        sid = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        screen = (
            " ~/work claude --resume 3eae96f6-42f7-4349-8bc1-111e0b6f04e5      ✔  24.7.0\n"
            "That session is running in the background (3eae96f6). Run `claude attach 3eae96f6`"
            " to open it, or `claude stop 3eae96f6` first to resume it here. Add --fork-session"
            " to branch off a copy instead.\n"
            "\n"
            " ~/work                                                          ✘ 1  24.7.0\n"
        )
        self.assertEqual(find_bg_resume_refusal(screen, sid), "3eae96f6")
        # Claude hard-wraps the message to the terminal width; the tail is still the same message.
        wrapped = screen.replace(" Add --fork-session to branch off a copy instead.",
                                 " Add --fork-session to branch\noff a copy instead.")
        self.assertEqual(find_bg_resume_refusal(wrapped, sid), "3eae96f6")
        # Another session's refusal must not attach this pane.
        self.assertIsNone(find_bg_resume_refusal(screen, "deb10810-c9c2-437a-8867-3be43498737e"))
        # A refusal followed by later commands is history, not the restart's resume.
        self.assertIsNone(find_bg_resume_refusal(screen + " ~/work ls\nfoo\n ~/work\n", sid))
        # The refusal must answer a `--resume` of this very session.
        self.assertIsNone(find_bg_resume_refusal(screen.replace("--resume", "--continue"), sid))
        # The job id is typed into a shell, so anything but a plain token is rejected.
        self.assertIsNone(find_bg_resume_refusal(screen.replace("(3eae96f6)", "(3eae;rm)"), sid))
        self.assertIsNone(find_bg_resume_refusal("", sid))

    def test_find_bg_resume_refusal_narrow_terminals(self):
        from sync import find_bg_resume_refusal

        session_id = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        message = ("That session is running in the background (3eae96f6). Run `claude attach 3eae96f6`"
                   " to open it, or `claude stop 3eae96f6` first to resume it here. Add --fork-session"
                   " to branch off a copy instead.")
        for width in (60, 40):
            with self.subTest(width=width):
                lines = [message[offset:offset + width] for offset in range(0, len(message), width)]
                self.assertGreater(len(lines), 3)
                screen = f" ~/w claude --resume {session_id}\n" + "\n".join(lines) + "\n ~/w\n"
                self.assertEqual(find_bg_resume_refusal(screen, session_id), "3eae96f6")
                self.assertIsNone(find_bg_resume_refusal(screen + " ~/w ls\nfiles\n ~/w\n", session_id))
                self.assertIsNone(find_bg_resume_refusal(screen.replace("--resume", "--continue"), session_id))

        # Claude wraps on word boundaries and drops the space at the break.
        word_wrapped = (f" ~/w claude --resume {session_id}\nThat session is running in the\n"
                        "background (3eae96f6). Run `claude attach 3eae96f6` to open it.\n ~/w\n")
        self.assertEqual(find_bg_resume_refusal(word_wrapped, session_id), "3eae96f6")

        for line_count in (8, 9):
            with self.subTest(line_count=line_count):
                lines = ["That session is running in the background (3eae96f6)"]
                lines += ["continued"] * (line_count - 2) + ["ends here."]
                screen = f" ~/w claude --resume {session_id}\n" + "\n".join(lines) + "\n ~/w\n"
                self.assertEqual(find_bg_resume_refusal(screen, session_id),
                                 "3eae96f6" if line_count == 8 else None)

    def test_reattach_viewers_after_restore(self):
        """Attach once per pane whose restart resume Claude refused; leave panes running
        Claude, and panes without a Claude session reference, alone."""
        from unittest.mock import patch
        from sync import reattach_viewers_after_restore

        sid = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        refusal = (" ~/w claude --resume " + sid + "\n"
                   "That session is running in the background (3eae96f6). Run `claude attach 3eae96f6`"
                   " to open it, or `claude stop 3eae96f6` first to resume it here.\n"
                   " ~/w\n")
        ref = lambda v: {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": v}
        panes = [{"pane_id": "w1:pA", "agent_session": ref(sid)},
                 {"pane_id": "w1:pB", "agent_session": ref("bbbb0000-0000-0000-0000-000000000000")},
                 {"pane_id": "w1:pC"}]
        fg = {"w1:pA": [{"pid": 100, "argv": ["-zsh"], "argv0": "-zsh", "name": "zsh"}],
              "w1:pB": [{"argv": ["claude", "--resume", "bbbb"], "argv0": "claude", "name": "2.1"}]}

        def fake_rpc(method, params=None, timeout=1.5):
            if method == "pane.list":
                return {"result": {"panes": panes}}
            if method == "pane.process_info":
                return {"result": {"process_info": {"shell_pid": 100,
                                                    "foreground_processes": fg[params["pane_id"]]}}}
            if method == "pane.read":
                # Herdr's JSON API spells this enum with underscores (the CLI uses dashes).
                if params.get("source") not in {"visible", "recent", "recent_unwrapped", "detection"}:
                    return {"error": {"code": "invalid_request"}}
                return {"result": {"read": {"text": refusal}}}
            return None

        ticks = iter(range(0, 1000, 10))
        lists = []

        def listing():
            # Herdr drops a pane's reference once its refused resume is back at the shell,
            # so the reference may be gone by the time the refusal shows up.
            lists.append(1)
            if len(lists) > 1:
                return [{"pane_id": "w1:pA"}] + panes[1:]
            return panes

        def fake_rpc_dropping(method, params=None, timeout=1.5):
            if method == "pane.list":
                return {"result": {"panes": listing()}}
            if method == "pane.read" and len(lists) == 1:
                return {"result": {"read": {"text": " ~/w\n"}}}  # resume not run yet
            return fake_rpc(method, params, timeout)

        with patch("sync.herdr_rpc", side_effect=fake_rpc_dropping), \
             patch("subprocess.run") as mock_run, \
             patch.dict("os.environ", {"HERDR_BIN_PATH": "/x/herdr"}):
            mock_run.return_value.returncode = 0
            attached = reattach_viewers_after_restore(
                timeout=60, interval=0, settle=20, sleep=lambda s: None, clock=lambda: next(ticks))
        self.assertEqual(attached, [("w1:pA", "3eae96f6")])
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(mock_run.call_args[0][0],
                         ["/x/herdr", "pane", "run", "w1:pA", "claude attach 3eae96f6"])

    def test_reattach_only_types_into_idle_shell(self):
        from unittest.mock import patch, Mock
        from sync import reattach_viewers_after_restore

        session_id = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        pane = {"pane_id": "w1:p1", "agent_session": {"agent": "claude", "kind": "id", "value": session_id}}
        refusal = (f"claude --resume {session_id}\n"
                   "That session is running in the background (3eae96f6).\n ~/w\n")
        shell = {"pid": 100, "argv": ["-zsh"], "name": "zsh"}
        reader = {"pid": 200, "argv": ["cat"], "name": "cat"}
        cases = [
            (None, False),
            ({"error": {"code": "unavailable"}}, False),
            ({"result": {}}, False),
            ({"result": {"process_info": None}}, False),
            ({"result": {"process_info": {"shell_pid": 100}}}, False),
            ({"result": {"process_info": {"shell_pid": 100, "foreground_processes": []}}}, False),
            ({"result": {"process_info": {"foreground_processes": [shell]}}}, False),
            ({"result": {"process_info": {"shell_pid": 100, "foreground_processes": [{}]}}}, False),
        ]
        for foreground, idle in (([reader], False), ([shell, reader], False),
                                 ([dict(shell, pid=200)], False), ([shell], True), ([shell, shell], True)):
            cases.append(({"result": {"process_info": {"shell_pid": 100,
                                                      "foreground_processes": foreground}}}, idle))

        for process_info, idle in cases:
            with self.subTest(process_info=process_info):
                now = [0.0]

                def fake_sleep(seconds):
                    now[0] += seconds

                def fake_rpc(method, params=None, timeout=1.5):
                    if method == "pane.list":
                        return {"result": {"panes": [pane]}}
                    if method == "pane.process_info":
                        return process_info
                    if method == "pane.read":
                        return {"result": {"read": {"text": refusal}}}
                    return None

                with patch("sync.herdr_rpc", side_effect=fake_rpc), \
                     patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
                    attached = reattach_viewers_after_restore(
                        timeout=3, interval=1, settle=0, sleep=fake_sleep, clock=lambda: now[0])
                self.assertEqual(attached, [("w1:p1", "3eae96f6")] if idle else [])
                self.assertEqual(mock_run.call_count, int(idle))

    def test_reattach_waits_for_claude_to_stabilize(self):
        from unittest.mock import patch, Mock
        from sync import reattach_viewers_after_restore

        session_id = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        pane = {"pane_id": "w1:p1", "agent_session": {"agent": "claude", "kind": "id", "value": session_id}}
        refusal = (f"claude --resume {session_id}\n"
                   "That session is running in the background (3eae96f6).\n ~/w\n")
        for scenario, finished_at in (("late_refusal", 24), ("stable", 25), ("interrupted", 38)):
            with self.subTest(scenario=scenario):
                now = [0.0]

                def fake_sleep(seconds):
                    now[0] += seconds

                def fake_rpc(method, params=None, timeout=1.5):
                    if method == "pane.list":
                        return {"result": {"panes": [pane]}}
                    if method == "pane.process_info":
                        if scenario == "interrupted" and now[0] == 22:
                            return None
                        running = now[0] >= 10 and not (scenario == "late_refusal" and now[0] >= 24)
                        process = {"pid": 200, "argv": ["claude"]} if running else {"pid": 100, "argv": ["zsh"]}
                        return {"result": {"process_info": {"shell_pid": 100, "foreground_processes": [process]}}}
                    if method == "pane.read":
                        text = refusal if scenario == "late_refusal" and now[0] >= 24 else " ~/w\n"
                        return {"result": {"read": {"text": text}}}
                    return None

                with patch("sync.herdr_rpc", side_effect=fake_rpc), \
                     patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
                    attached = reattach_viewers_after_restore(
                        timeout=60, interval=1, settle=20, sleep=fake_sleep, clock=lambda: now[0])
                self.assertEqual(now[0], finished_at)
                self.assertEqual(attached, [("w1:p1", "3eae96f6")] if scenario == "late_refusal" else [])
                self.assertEqual(mock_run.call_count, int(scenario == "late_refusal"))

    def test_reattach_retries_failed_command(self):
        import subprocess
        from unittest.mock import patch, Mock
        from sync import reattach_viewers_after_restore

        session_id = "3eae96f6-42f7-4349-8bc1-111e0b6f04e5"
        pane = {"pane_id": "w1:p1", "agent_session": {"agent": "claude", "kind": "id", "value": session_id}}
        refusal = (f"claude --resume {session_id}\n"
                   "That session is running in the background (3eae96f6).\n ~/w\n")

        def fake_rpc(method, params=None, timeout=1.5):
            if method == "pane.list":
                return {"result": {"panes": [pane]}}
            if method == "pane.process_info":
                return {"result": {"process_info": {"shell_pid": 100, "foreground_processes": [{"pid": 100}]}}}
            if method == "pane.read":
                return {"result": {"read": {"text": refusal}}}
            return None

        for failure in (Mock(returncode=1), OSError("unavailable"), subprocess.TimeoutExpired("herdr", 5), None):
            with self.subTest(failure=failure):
                now = [0.0]

                def fake_sleep(seconds):
                    now[0] += seconds

                outcomes = [failure, Mock(returncode=0)] if failure is not None else [Mock(returncode=1)] * 4
                with patch("sync.herdr_rpc", side_effect=fake_rpc), \
                     patch("subprocess.run", side_effect=outcomes) as mock_run:
                    attached = reattach_viewers_after_restore(
                        timeout=3, interval=1, settle=0, sleep=fake_sleep, clock=lambda: now[0])
                self.assertEqual(attached, [("w1:p1", "3eae96f6")] if failure is not None else [])
                self.assertEqual(mock_run.call_count, 2 if failure is not None else 4)
                self.assertEqual(now[0], 1 if failure is not None else 3)

    def test_claude_config_dir_defaults_and_override(self):
        from unittest.mock import patch
        from sync import claude_config_dir, default_claude_hook_path

        for environment, expected in (({}, Path("/home/testuser/.claude")),
                                      ({"CLAUDE_CONFIG_DIR": ""}, Path("/home/testuser/.claude")),
                                      ({"CLAUDE_CONFIG_DIR": "/custom/config"}, Path("/custom/config"))):
            with self.subTest(environment=environment), \
                 patch.dict("os.environ", environment, clear=True), \
                 patch("sync.Path.home", return_value=Path("/home/testuser")):
                self.assertEqual(claude_config_dir(), expected)
                self.assertEqual(default_claude_hook_path(), expected / "hooks" / "herdr-agent-state.sh")

    def test_session_data_uses_claude_config_dir(self):
        import fnmatch
        import io
        from unittest.mock import patch
        from sync import (is_valid_cc_session, resolve_session_from_pid, resolve_parked_job_session,
                          resolve_live_bg_session_by_name, resolve_title_from_foreground_process)

        base = "/custom/config"
        job_sid = "aaaa1111-1111-1111-1111-111111111111"
        bg_sid = "bbbb2222-2222-2222-2222-222222222222"
        files = {
            f"{base}/projects/task/{job_sid}.jsonl": '{"customTitle": "Old Title"}\n',
            f"{base}/projects/task/{job_sid}/custom-title.json": '{"customTitle": "Job Title"}',
            f"{base}/projects/task/{bg_sid}.jsonl": '{"customTitle": "Background Title"}\n',
            f"{base}/sessions/111.json": json.dumps({"kind": "interactive", "sessionId": bg_sid,
                                                  "parkedJobId": "aaaa1111"}),
            f"{base}/sessions/222.json": json.dumps({"kind": "bg", "pid": 222, "name": "Background Title",
                                                  "nameSource": "user", "sessionId": bg_sid}),
            f"{base}/jobs/aaaa1111/state.json": json.dumps({"resumeSessionId": job_sid}),
        }
        process = {"pid": 333, "argv": ["claude", "attach", bg_sid[:8]]}
        with patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": base}), \
             patch("sync.glob.glob", side_effect=lambda pattern: [path for path in files if fnmatch.fnmatch(path, pattern)]), \
             patch("sync.os.path.isfile", side_effect=lambda path: str(path) in files), \
             patch("sync.os.path.getsize", side_effect=lambda path: len(files[str(path)])), \
             patch("sync.os.path.getmtime", return_value=1), \
             patch("builtins.open", side_effect=lambda path, *args, **kwargs: io.StringIO(files[str(path)])), \
             patch("sync._is_live_claude_process", return_value=True), \
             patch("sync.herdr_rpc", return_value={
                 "result": {"process_info": {"foreground_processes": [process]}}}):
            self.assertTrue(is_valid_cc_session(job_sid))
            self.assertEqual(extract_cc_session_name(job_sid), "Job Title")
            self.assertEqual(extract_cc_session_name(bg_sid), "Background Title")
            self.assertEqual(resolve_parked_job_session("aaaa1111"), (job_sid, "Job Title"))
            self.assertEqual(resolve_session_from_pid(111), (job_sid, "Job Title"))
            self.assertEqual(resolve_live_bg_session_by_name("Background Title"), (bg_sid, "Background Title"))
            self.assertEqual(resolve_title_from_foreground_process("w1:p1"), (bg_sid, "Background Title"))

    def test_ensure_hook_guard(self):
        """Herdr owns herdr-agent-state.sh and rewrites it on every integration install, so the
        background-session guard is laid over the shipped file: added once, skipped when the
        file already has one (e.g. after Herdr fixes herdr#5261), and never forced onto a file
        whose anchor line moved."""
        import tempfile
        from pathlib import Path
        from sync import ensure_hook_guard, HOOK_GUARD_ANCHOR

        shipped = ("#!/bin/sh\n# HERDR_INTEGRATION_ID=claude\n# HERDR_INTEGRATION_VERSION=10\n"
                   '[ "${HERDR_ENV:-}" = "1" ] || exit 0\n' + HOOK_GUARD_ANCHOR +
                   "command -v python3 >/dev/null 2>&1 || exit 0\n")
        with tempfile.TemporaryDirectory() as d:
            hook = Path(d) / "herdr-agent-state.sh"
            hook.write_text(shipped)
            hook.chmod(0o755)
            self.assertEqual(ensure_hook_guard(hook), "patched")
            patched = hook.read_text()
            self.assertIn('[ -z "${CLAUDE_JOB_DIR:-}" ] || exit 0', patched)
            self.assertLess(patched.index(HOOK_GUARD_ANCHOR), patched.index("CLAUDE_JOB_DIR"))
            self.assertLess(patched.index("CLAUDE_JOB_DIR"), patched.index("command -v python3"))
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
            self.assertEqual(ensure_hook_guard(hook), "already-guarded")
            self.assertEqual(hook.read_text(), patched)

            hook.write_text(shipped.replace(HOOK_GUARD_ANCHOR, '[ -n "$HERDR_PANE_ID" ] || exit 0\n'))
            self.assertEqual(ensure_hook_guard(hook), "anchor-missing")
            self.assertNotIn("CLAUDE_JOB_DIR", hook.read_text())

            hook.write_text("#!/bin/sh\n# HERDR_INTEGRATION_ID=codex\n" + HOOK_GUARD_ANCHOR)
            self.assertEqual(ensure_hook_guard(hook), "not-claude-hook")
            self.assertEqual(ensure_hook_guard(Path(d) / "missing.sh"), "no-hook")

    def test_is_live_claude_process_rejects_impostors(self):
        """signal 0 only proves a pid is taken; identity needs the command and procStart."""
        from unittest.mock import patch
        from sync import _is_live_claude_process, _proc_start_matches

        self.assertFalse(_is_live_claude_process(None))
        self.assertFalse(_is_live_claude_process(-1))
        self.assertFalse(_is_live_claude_process(0))

        ps = "SNs+ Wed Sep 16 13:28:43 2026 claude bg-spare --bg-spare /tmp/x.sock"
        zombie = "Z+ Wed Sep 16 13:28:43 2026 claude bg-spare --bg-spare x"
        other = "SNs+ Wed Sep 16 13:28:43 2026 /usr/bin/python3 unrelated.py"

        class R:
            def __init__(self, out): self.returncode, self.stdout = 0, out

        with patch("sync.os.kill", return_value=None):
            with patch("subprocess.run", return_value=R(ps)):
                self.assertTrue(_is_live_claude_process(1, "Wed Sep 16 13:28:43 2026"))
                # A procStart from a different instant means the pid was recycled.
                self.assertFalse(_is_live_claude_process(1, "Mon Jan  1 00:00:00 2020"))
            with patch("subprocess.run", return_value=R(zombie)):
                self.assertFalse(_is_live_claude_process(1))
            with patch("subprocess.run", return_value=R(other)):
                self.assertFalse(_is_live_claude_process(1))

        # ps prints local time while procStart records UTC; the same instant must match.
        self.assertTrue(_proc_start_matches("Wed Sep 16 13:28:43 2026", "Wed Sep 16 13:28:43 2026"))
        self.assertFalse(_proc_start_matches("Wed Sep 16 13:28:43 2026", "not a date"))
        self.assertFalse(_proc_start_matches("", "Wed Sep 16 13:28:43 2026"))

    def test_extract_title_from_screen_reverse_scan_picks_bottom_readline_bar(self):
        """Must scan bottom-up so tool/markdown dividers are ignored in favor of the active readline bar."""
        from unittest.mock import patch
        from sync import extract_title_from_screen

        screen_text = (
            "────────────────── Step 1: Query DB ──────────────────\n"
            "some tool output here\n"
            "───────────────────────────────── Moshi ─\n"
            "❯\n"
            "─────────────────────────────────────────\n"
        )
        fake_res = {"result": {"read": {"text": screen_text}}}
        with patch("sync.herdr_rpc", return_value=fake_res):
            title = extract_title_from_screen("w1:p1")
            self.assertEqual(title, "Moshi")


if __name__ == "__main__":
    unittest.main()
