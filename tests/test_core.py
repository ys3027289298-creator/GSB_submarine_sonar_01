"""声呐搜索回合核心逻辑测试（红态修复回归）。"""
import json
import tempfile
import unittest
from pathlib import Path

from submarine.core import (
    DETECTION_THRESHOLD,
    SCAN_BATTERY_COST,
    SEA_STATE_DRIFT,
    ContactCapacityError,
    ContactStatus,
    GameError,
    GamePhase,
    InsufficientBatteryError,
    SaveGameError,
    SeaState,
    SonarGame,
    Target,
)


def calm_game(seed=70, **kwargs):
    """构造海况永不漂移的对局，保证测试确定性。"""
    game = SonarGame(seed=seed, **kwargs)
    game.rng.uniform = lambda a, b: 0.99 if (a, b) == (0.0, 1.0) else 0.0
    return game


class DedupTest(unittest.TestCase):
    def test_same_contact_not_registered_twice(self):
        game = calm_game()
        first = game.scan()
        self.assertIn("T1", first.new_ids)
        count_after_first = len(game.contacts)
        second = game.scan()
        self.assertEqual(len(game.contacts), count_after_first)
        self.assertEqual(second.new_ids, [])
        self.assertIn("T1", second.updated_ids)


class NoiseFilterTest(unittest.TestCase):
    def test_threshold_signal_is_kept(self):
        self.assertTrue(SonarGame._passes_filter(DETECTION_THRESHOLD))

    def test_below_threshold_signal_is_dropped(self):
        self.assertFalse(SonarGame._passes_filter(DETECTION_THRESHOLD - 1e-9))

    def test_scan_keeps_boundary_signal_contact(self):
        target = Target("B1", 50.0, 0.0)
        game = SonarGame(seed=1, targets=[target])
        game.rng.uniform = lambda a, b: 0.99 if (a, b) == (0.0, 1.0) else 0.0
        radius = game.detection_radius()
        # 调整目标距离，使基础信号恰好等于阈值。
        target.x = radius * (1.0 - DETECTION_THRESHOLD)
        record = game.scan()
        self.assertIn("B1", game.contacts)
        self.assertIn("B1", record.new_ids)


class DepthRadiusTest(unittest.TestCase):
    def test_deeper_depth_shrinks_detection_radius(self):
        game = calm_game()
        shallow = game.detection_radius()
        game.set_depth(240.0)
        deep = game.detection_radius()
        self.assertLess(deep, shallow)

    def test_depth_change_alters_scan_outcome(self):
        game = calm_game()
        shallow_record = game.scan()
        self.assertIn("T1", shallow_record.new_ids)
        deep_game = calm_game()
        deep_game.set_depth(240.0)
        deep_record = deep_game.scan()
        self.assertNotIn("T1", deep_record.new_ids)  # 深水半径收缩，信号低于阈值
        self.assertLess(deep_record.radius, shallow_record.radius)


class SilentBatteryTest(unittest.TestCase):
    def test_silent_scan_consumes_no_battery(self):
        game = calm_game()
        game.set_silent(True)
        before = game.battery
        game.scan()
        self.assertEqual(game.battery, before)

    def test_active_scan_consumes_battery(self):
        game = calm_game()
        before = game.battery
        game.scan()
        self.assertAlmostEqual(game.battery, before - SCAN_BATTERY_COST)

    def test_scan_rejected_when_battery_insufficient(self):
        game = calm_game()
        game.battery = SCAN_BATTERY_COST - 0.5
        with self.assertRaises(InsufficientBatteryError):
            game.scan()


class LockedDistanceTest(unittest.TestCase):
    def test_heading_change_updates_locked_distance(self):
        game = calm_game()
        game.scan()
        game.lock("T1")
        before = game.locked_distance
        game.change_heading(90.0)
        self.assertIsNotNone(game.locked_distance)
        self.assertNotAlmostEqual(game.locked_distance, before)
        target = game._target_index["T1"]
        expected = ((target.x - game.x) ** 2 + (target.y - game.y) ** 2) ** 0.5
        self.assertAlmostEqual(game.locked_distance, expected)


class UndoScanTest(unittest.TestCase):
    def test_undo_keeps_history_record(self):
        game = calm_game()
        game.scan()
        game.undo_scan()
        self.assertEqual(len(game.history), 1)
        self.assertTrue(game.history[-1].undone)
        self.assertEqual(game.contacts, {})

    def test_undo_twice_raises(self):
        game = calm_game()
        game.scan()
        game.undo_scan()
        with self.assertRaises(GameError):
            game.undo_scan()

    def test_undo_without_scan_raises(self):
        game = calm_game()
        with self.assertRaises(GameError):
            game.undo_scan()


class SeaStateOrderTest(unittest.TestCase):
    def test_sea_state_event_applies_before_detection(self):
        # 目标距离 100m：平静海况半径 105.6 可探测，恶劣海况 73.9 不可探测。
        target = Target("S1", 100.0, 0.0)
        game = SonarGame(seed=1, targets=[target])
        game.sea_state = SeaState.CALM

        def fake_uniform(a, b):
            if (a, b) == (0.0, 1.0):
                return SEA_STATE_DRIFT / 2.0  # 触发海况事件
            return 0.0

        game.rng.uniform = fake_uniform
        game.rng.choice = lambda seq: 1  # CALM -> MODERATE -> ROUGH 方向
        game.sea_state = SeaState.MODERATE
        record = game.scan()
        self.assertEqual(game.sea_state, SeaState.ROUGH)
        self.assertEqual(record.sea_state, SeaState.ROUGH.value)
        self.assertNotIn("S1", game.contacts)


class SaveLoadTest(unittest.TestCase):
    def test_roundtrip_preserves_battery_and_contacts(self):
        game = calm_game()
        game.scan()
        game.lock("T1")
        game.change_heading(30.0)
        game.scan()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "save.json"
            game.save(path)
            loaded = SonarGame.load(path)
        self.assertEqual(loaded.battery, game.battery)
        self.assertEqual(list(loaded.contacts), list(game.contacts))
        self.assertEqual(loaded.locked_id, game.locked_id)
        self.assertEqual(loaded.locked_distance, game.locked_distance)
        self.assertEqual(len(loaded.history), len(game.history))
        self.assertEqual(loaded.phase, game.phase)
        # 固定随机源状态也必须一致：后续随机序列相同。
        self.assertEqual(loaded.rng.random(), game.rng.random())

    def test_save_file_is_json(self):
        game = calm_game()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "save.json"
            game.save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertIn("contacts", data)
        self.assertIn("battery", data)

    def test_load_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(SaveGameError):
                SonarGame.load(path)

    def test_load_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(SaveGameError):
                SonarGame.load(path)

    def test_load_missing_fields_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(SaveGameError):
                SonarGame.load(path)


class CapacityTest(unittest.TestCase):
    def test_capacity_boundary_exact_fill(self):
        targets = [Target("C%d" % i, 10.0 * (i + 1), 0.0) for i in range(3)]
        game = calm_game(capacity=3, targets=targets)
        record = game.scan()
        self.assertEqual(len(game.contacts), 3)
        self.assertEqual(record.overflow_ids, [])

    def test_capacity_overflow_not_registered(self):
        targets = [Target("C%d" % i, 10.0 * (i + 1), 0.0) for i in range(4)]
        game = calm_game(capacity=3, targets=targets)
        record = game.scan()
        self.assertEqual(len(game.contacts), 3)
        self.assertEqual(record.overflow_ids, ["C3"])
        self.assertNotIn("C3", game.contacts)

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(GameError):
            SonarGame(capacity=0)


class DuplicateInputTest(unittest.TestCase):
    def test_duplicate_lock_raises(self):
        game = calm_game()
        game.scan()
        game.lock("T1")
        with self.assertRaises(GameError):
            game.lock("T1")

    def test_lock_unknown_contact_raises(self):
        game = calm_game()
        game.scan()
        with self.assertRaises(GameError):
            game.lock("NOPE")


class EmptyDataTest(unittest.TestCase):
    def test_scan_with_no_targets(self):
        game = calm_game(targets=[])
        record = game.scan()
        self.assertEqual(record.new_ids, [])
        self.assertEqual(game.contacts, {})
        self.assertEqual(len(game.history), 1)

    def test_lock_with_empty_contacts_raises(self):
        game = calm_game(targets=[])
        with self.assertRaises(GameError):
            game.lock("T1")


class PhaseTest(unittest.TestCase):
    def test_phase_transitions(self):
        game = calm_game()
        self.assertEqual(game.phase, GamePhase.READY)
        game.scan()
        self.assertEqual(game.phase, GamePhase.SEARCHING)
        game.lock("T1")
        self.assertEqual(game.phase, GamePhase.LOCKED)
        self.assertEqual(game.contacts["T1"].status, ContactStatus.LOCKED)


if __name__ == "__main__":
    unittest.main()
