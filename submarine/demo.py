"""固定演示：使用固定随机源跑通一局完整可演示流程。

运行方式：python -m submarine.demo
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .cli import Shell
from .core import SonarGame

SCRIPT = [
    "status",
    "scan",
    "scan",
    "lock T1",
    "heading 45",
    "depth 200",
    "scan",
    "silent on",
    "scan",
    "silent off",
    "undo",
    "save {save}",
    "load {save}",
    "status",
    "bogus-command",
    "",
    "lock T1",
    "quit",
]


def run_demo(seed: int = 70) -> list:
    shell = Shell(SonarGame(seed=seed))
    save_path = Path(tempfile.gettempdir()) / "submarine_demo_save.json"
    transcript = []
    for line in SCRIPT:
        command = line.format(save=save_path)
        output = shell.execute(command)
        transcript.append((command, output))
        print("sonar> %s" % command)
        print(output)
    save_path.unlink(missing_ok=True)
    return transcript


if __name__ == "__main__":
    run_demo()
