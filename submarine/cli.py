"""终端界面：解析并执行文本命令。核心逻辑全部在 core 模块。"""
from __future__ import annotations

from .core import (
    DETECTION_THRESHOLD,
    GameError,
    SaveGameError,
    SonarGame,
)

USAGE = (
    "可用命令: scan | undo | lock <id> | heading <deg> | depth <m> | "
    "silent on|off | save <path> | load <path> | status | help | quit"
)


class Shell:
    """一个简单的命令行壳，持有当前对局。"""

    def __init__(self, game: SonarGame | None = None):
        self.game = game if game is not None else SonarGame()
        self.running = True

    def execute(self, line: str) -> str:
        text = (line or "").strip()
        if not text:
            return "空命令：请输入命令，输入 help 查看用法"
        parts = text.split()
        cmd, args = parts[0].lower(), parts[1:]
        handler = getattr(self, "_cmd_%s" % cmd, None)
        if handler is None or cmd.startswith("_"):
            return "非法命令: %s（输入 help 查看用法）" % cmd
        try:
            return handler(args)
        except GameError as exc:
            return "错误: %s" % exc
        except ValueError:
            return "错误: 参数格式不正确"

    # ---- 命令实现 -----------------------------------------------------

    def _cmd_help(self, args):
        return USAGE

    def _cmd_quit(self, args):
        self.running = False
        return "已退出"

    def _cmd_scan(self, args):
        record = self.game.scan()
        return (
            "扫描 #%d 完成：海况=%s 半径=%.1f 新增=%s 更新=%s 溢出=%s 电量=%.1f"
            % (
                record.seq,
                record.sea_state,
                record.radius,
                ",".join(record.new_ids) or "无",
                ",".join(record.updated_ids) or "无",
                ",".join(record.overflow_ids) or "无",
                record.battery_after,
            )
        )

    def _cmd_undo(self, args):
        record = self.game.undo_scan()
        return "已撤销扫描 #%d（历史记录保留）" % record.seq

    def _cmd_lock(self, args):
        if len(args) != 1:
            return "错误: 用法 lock <id>"
        contact = self.game.lock(args[0])
        return "已锁定 %s，距离 %.1f" % (contact.id, contact.distance)

    def _cmd_heading(self, args):
        if len(args) != 1:
            return "错误: 用法 heading <deg>"
        self.game.change_heading(float(args[0]))
        locked = self.game.locked_distance
        suffix = "，锁定距离 %.1f" % locked if locked is not None else ""
        return "航向 %.1f%s" % (self.game.heading, suffix)

    def _cmd_depth(self, args):
        if len(args) != 1:
            return "错误: 用法 depth <m>"
        self.game.set_depth(float(args[0]))
        return "深度 %.1f，探测半径 %.1f" % (
            self.game.depth,
            self.game.detection_radius(),
        )

    def _cmd_silent(self, args):
        if len(args) != 1 or args[0] not in ("on", "off"):
            return "错误: 用法 silent on|off"
        self.game.set_silent(args[0] == "on")
        return "静音模式：%s" % ("开" if self.game.silent else "关")

    def _cmd_save(self, args):
        if len(args) != 1:
            return "错误: 用法 save <path>"
        self.game.save(args[0])
        return "已保存到 %s" % args[0]

    def _cmd_load(self, args):
        if len(args) != 1:
            return "错误: 用法 load <path>"
        try:
            self.game = SonarGame.load(args[0])
        except SaveGameError as exc:
            return "错误: %s" % exc
        return (
            "已加载：电量 %.1f，接触 %d 个，历史 %d 条"
            % (self.game.battery, len(self.game.contacts), len(self.game.history))
        )

    def _cmd_status(self, args):
        game = self.game
        contacts = ", ".join(
            "%s(%s,%.1f)" % (c.id, c.status.value, c.distance)
            for c in game.contacts.values()
        ) or "无"
        return (
            "阶段=%s 电量=%.1f 航向=%.1f 深度=%.1f 海况=%s 静音=%s "
            "半径=%.1f 阈值=%.2f 接触=[%s] 历史=%d 条"
            % (
                game.phase.value,
                game.battery,
                game.heading,
                game.depth,
                game.sea_state.value,
                "开" if game.silent else "关",
                game.detection_radius(),
                DETECTION_THRESHOLD,
                contacts,
                len(game.history),
            )
        )


def main():
    shell = Shell()
    print("潜艇声呐搜索终端。" + USAGE)
    while shell.running:
        try:
            line = input("sonar> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        print(shell.execute(line))


if __name__ == "__main__":
    main()
