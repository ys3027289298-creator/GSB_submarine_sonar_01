"""终端命令解析与固定演示流程测试。"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from submarine.cli import Shell
from submarine.core import SonarGame
from submarine.demo import run_demo


def quiet_shell(seed=70):
    shell = Shell(SonarGame(seed=seed))
    shell.game.rng.uniform = lambda a, b: 0.99 if (a, b) == (0.0, 1.0) else 0.0
    return shell


class CommandParsingTest(unittest.TestCase):
    def test_illegal_command_rejected(self):
        shell = quiet_shell()
        out = shell.execute("warp-speed 9")
        self.assertIn("非法命令", out)

    def test_empty_command_rejected(self):
        shell = quiet_shell()
        self.assertIn("空命令", shell.execute(""))
        self.assertIn("空命令", shell.execute("   "))

    def test_missing_argument_rejected(self):
        shell = quiet_shell()
        self.assertIn("错误", shell.execute("lock"))
        self.assertIn("错误", shell.execute("heading"))
        self.assertIn("错误", shell.execute("silent maybe"))

    def test_bad_number_rejected(self):
        shell = quiet_shell()
        self.assertIn("错误", shell.execute("heading abc"))

    def test_help_and_quit(self):
        shell = quiet_shell()
        self.assertIn("scan", shell.execute("help"))
        shell.execute("quit")
        self.assertFalse(shell.running)


class CommandFlowTest(unittest.TestCase):
    def test_scan_lock_heading_flow(self):
        shell = quiet_shell()
        self.assertIn("扫描 #0", shell.execute("scan"))
        self.assertIn("已锁定 T1", shell.execute("lock T1"))
        out = shell.execute("heading 90")
        self.assertIn("锁定距离", out)
        self.assertIn("错误", shell.execute("lock T1"))  # 重复锁定

    def test_undo_flow(self):
        shell = quiet_shell()
        shell.execute("scan")
        self.assertIn("已撤销", shell.execute("undo"))
        self.assertIn("错误", shell.execute("undo"))  # 重复撤销

    def test_save_load_flow(self):
        shell = quiet_shell()
        shell.execute("scan")
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.json")
            self.assertIn("已保存", shell.execute("save " + path))
            out = shell.execute("load " + path)
            self.assertIn("已加载", out)
            self.assertEqual(shell.game.battery, 100.0 - 6.0)
            self.assertGreater(len(shell.game.contacts), 0)

    def test_load_empty_save_reports_error(self):
        shell = quiet_shell()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            path.write_text("", encoding="utf-8")
            self.assertIn("错误", shell.execute("load " + str(path)))


class DemoTest(unittest.TestCase):
    def test_fixed_demo_runs_clean(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            transcript = run_demo(seed=70)
        self.assertTrue(transcript)
        outputs = [out for _, out in transcript]
        self.assertTrue(any("已锁定" in o for o in outputs))
        self.assertTrue(any("已加载" in o for o in outputs))
        self.assertTrue(any("非法命令" in o for o in outputs))
        self.assertTrue(any("空命令" in o for o in outputs))
        self.assertFalse(any(o.startswith("错误: 电量不足") for o in outputs))

    def test_demo_is_deterministic(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            first = run_demo(seed=70)
            second = run_demo(seed=70)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
