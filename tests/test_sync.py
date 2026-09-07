import unittest
import sys
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

        # Valid pid file with valid session and custom title
        valid_json = json.dumps({
            "pid": 1234,
            "sessionId": "12345678-1234-1234-1234-123456789abc",
            "name": "Fallback Name"
        })
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=valid_json)), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value="Extracted Title"):
            sid, title = resolve_session_from_pid(1234)
            self.assertEqual(sid, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(title, "Extracted Title")

        # Valid pid file falling back to JSON name when transcript title not found
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=valid_json)), \
             patch("sync.is_valid_cc_session", return_value=True), \
             patch("sync.extract_cc_session_name", return_value=None):
            sid, title = resolve_session_from_pid(1234)
            self.assertEqual(sid, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(title, "Fallback Name")

        # Valid pid file but session is ghost / not on disk
        with patch("sync.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=valid_json)), \
             patch("sync.is_valid_cc_session", return_value=False):
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

            # Assert pane.report_agent_session was called with authoritative id
            report_calls = [c for c in rpc_calls if c[0] == "pane.report_agent_session"]
            self.assertEqual(len(report_calls), 1)
            self.assertEqual(report_calls[0][1]["pane_id"], "w1:p1")
            self.assertEqual(report_calls[0][1]["agent_session_id"], "authoritative-uuid-1234")

            # Assert pane.rename and pane.report_metadata were also called
            rename_calls = [c for c in rpc_calls if c[0] == "pane.rename"]
            self.assertEqual(len(rename_calls), 1)
            self.assertEqual(rename_calls[0][1]["label"], "Authoritative Title")

    def test_sync_pane_discards_ghost_session_without_foreground(self):
        from unittest.mock import patch, MagicMock
        from sync import sync_pane, StateManager

        mock_state = MagicMock(spec=StateManager)
        mock_pane = {
            "pane_id": "w1:p2",
            "label": "Old Label",
            "title": "Old Title",
            "terminal_title": "btop",
            "agent_session": {"value": "ghost-subagent-uuid-000"},
            "cwd": "/Users/bytedance/project",
        }

        rpc_calls = []
        def fake_herdr_rpc(method, params=None, timeout=1.5):
            rpc_calls.append((method, params))
            return {"result": {"ok": True}}

        with patch("sync.resolve_title_from_foreground_process", return_value=(None, None)), \
             patch("sync.is_valid_cc_session", return_value=False), \
             patch("sync.extract_cc_session_name") as mock_extract, \
             patch("sync.herdr_rpc", side_effect=fake_herdr_rpc):
            updated = sync_pane(mock_pane, mock_state)
            self.assertTrue(updated)
            # Must NOT attempt to look up ghost session name
            mock_extract.assert_not_called()
            # Must fall back to terminal_title 'btop'
            rename_calls = [c for c in rpc_calls if c[0] == "pane.rename"]
            self.assertEqual(rename_calls[0][1]["label"], "btop")


if __name__ == "__main__":
    unittest.main()
