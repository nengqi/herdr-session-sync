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


if __name__ == "__main__":
    unittest.main()
