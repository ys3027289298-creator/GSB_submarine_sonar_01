"""潜艇声呐搜索回合核心逻辑。

仅依赖 Python 标准库。随机源为固定种子的 ``random.Random`` 实例，
存档为 JSON 格式，状态使用枚举表示。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class GameError(Exception):
    """游戏逻辑基础异常。"""


class InsufficientBatteryError(GameError):
    """电量不足，无法执行主动扫描。"""


class ContactCapacityError(GameError):
    """接触列表容量不足。"""


class SaveGameError(GameError):
    """存档数据缺失或损坏。"""


class SeaState(Enum):
    CALM = "calm"
    MODERATE = "moderate"
    ROUGH = "rough"


class ContactStatus(Enum):
    DETECTED = "detected"
    LOCKED = "locked"
    LOST = "lost"


class GamePhase(Enum):
    READY = "ready"
    SEARCHING = "searching"
    LOCKED = "locked"
    FINISHED = "finished"


SEA_STATE_FACTOR = {
    SeaState.CALM: 1.0,
    SeaState.MODERATE: 0.85,
    SeaState.ROUGH: 0.7,
}

BASE_DETECTION_RADIUS = 120.0
MAX_DEPTH = 300.0
DEPTH_ATTENUATION = 0.6
MIN_DEPTH_FACTOR = 0.2
DETECTION_THRESHOLD = 0.35
NOISE_AMPLITUDE = 0.15
SCAN_BATTERY_COST = 6.0
SILENT_SCAN_BATTERY_COST = 0.0
DEFAULT_CONTACT_CAPACITY = 8
TURN_STEP = 12.0
SEA_STATE_DRIFT = 0.25
SAVE_VERSION = 1


@dataclass
class Target:
    id: str
    x: float
    y: float
    depth: float = 50.0

    def to_dict(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "depth": self.depth}

    @classmethod
    def from_dict(cls, data: dict) -> "Target":
        return cls(
            id=str(data["id"]),
            x=float(data["x"]),
            y=float(data["y"]),
            depth=float(data.get("depth", 50.0)),
        )


@dataclass
class Contact:
    id: str
    bearing: float
    distance: float
    signal: float
    status: ContactStatus = ContactStatus.DETECTED

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bearing": self.bearing,
            "distance": self.distance,
            "signal": self.signal,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Contact":
        return cls(
            id=str(data["id"]),
            bearing=float(data["bearing"]),
            distance=float(data["distance"]),
            signal=float(data["signal"]),
            status=ContactStatus(data["status"]),
        )


@dataclass
class ScanRecord:
    seq: int
    sea_state: str
    radius: float
    battery_after: float
    new_ids: list = field(default_factory=list)
    updated_ids: list = field(default_factory=list)
    overflow_ids: list = field(default_factory=list)
    undone: bool = False

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "sea_state": self.sea_state,
            "radius": self.radius,
            "battery_after": self.battery_after,
            "new_ids": list(self.new_ids),
            "updated_ids": list(self.updated_ids),
            "overflow_ids": list(self.overflow_ids),
            "undone": self.undone,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScanRecord":
        return cls(
            seq=int(data["seq"]),
            sea_state=str(data["sea_state"]),
            radius=float(data["radius"]),
            battery_after=float(data["battery_after"]),
            new_ids=list(data.get("new_ids", [])),
            updated_ids=list(data.get("updated_ids", [])),
            overflow_ids=list(data.get("overflow_ids", [])),
            undone=bool(data.get("undone", False)),
        )


class SonarGame:
    """一局声呐搜索的核心状态与回合逻辑。"""

    def __init__(self, seed: int = 70, capacity: int = DEFAULT_CONTACT_CAPACITY,
                 targets: list | None = None):
        if capacity < 1:
            raise GameError("接触列表容量必须为正整数")
        self.seed = seed
        self.rng = random.Random(seed)
        self.capacity = capacity
        self.battery = 100.0
        self.heading = 0.0
        self.depth = 60.0
        self.x = 0.0
        self.y = 0.0
        self.sea_state = SeaState.CALM
        self.silent = False
        self.phase = GamePhase.READY
        self.contacts: dict[str, Contact] = {}
        self.history: list[ScanRecord] = []
        self.locked_id: str | None = None
        self.locked_distance: float | None = None
        self._scan_seq = 0
        self.targets = list(targets) if targets is not None else self._default_targets()
        self._target_index = {t.id: t for t in self.targets}

    @staticmethod
    def _default_targets() -> list:
        return [
            Target("T1", 60.0, 0.0, 40.0),
            Target("T2", 0.0, 90.0, 80.0),
            Target("T3", 150.0, 0.0, 60.0),
            Target("T4", -40.0, 30.0, 55.0),
            Target("T5", 30.0, -100.0, 70.0),
        ]

    # ---- 探测模型 -------------------------------------------------------

    def detection_radius(self) -> float:
        """探测半径随深度与海况变化：越深、海况越差，半径越小。"""
        depth_factor = max(
            MIN_DEPTH_FACTOR,
            1.0 - DEPTH_ATTENUATION * self.depth / MAX_DEPTH,
        )
        return BASE_DETECTION_RADIUS * depth_factor * SEA_STATE_FACTOR[self.sea_state]

    @staticmethod
    def _passes_filter(signal: float) -> bool:
        """噪声过滤：临界信号（等于阈值）必须保留。"""
        return signal >= DETECTION_THRESHOLD

    def _bearing_to(self, target: Target) -> float:
        angle = math.degrees(math.atan2(target.y - self.y, target.x - self.x))
        return (90.0 - angle) % 360.0

    def _distance_to(self, target: Target) -> float:
        return math.hypot(target.x - self.x, target.y - self.y)

    # ---- 回合动作 -------------------------------------------------------

    def _apply_sea_state_event(self) -> None:
        roll = self.rng.uniform(0.0, 1.0)
        if roll >= SEA_STATE_DRIFT:
            return
        states = list(SeaState)
        idx = states.index(self.sea_state)
        step = self.rng.choice((-1, 1))
        self.sea_state = states[max(0, min(len(states) - 1, idx + step))]

    def scan(self) -> ScanRecord:
        """执行一次主动声呐扫描。

        结算顺序：电量检查 -> 海况事件 -> 计算半径 -> 噪声过滤 -> 登记接触。
        同一接触目标按 id 去重，重复扫描只更新不重复登记。
        """
        cost = SILENT_SCAN_BATTERY_COST if self.silent else SCAN_BATTERY_COST
        if self.battery < cost:
            raise InsufficientBatteryError(
                "电量不足，无法执行主动扫描: %.1f < %.1f" % (self.battery, cost)
            )
        # 海况事件必须先于探测结算，否则半径会使用过期海况。
        self._apply_sea_state_event()
        self.battery -= cost
        radius = self.detection_radius()
        record = ScanRecord(
            seq=self._scan_seq,
            sea_state=self.sea_state.value,
            radius=radius,
            battery_after=self.battery,
        )
        self._scan_seq += 1
        for target in self.targets:
            dist = self._distance_to(target)
            if dist > radius:
                continue
            signal = (1.0 - dist / radius) + self.rng.uniform(
                -NOISE_AMPLITUDE, NOISE_AMPLITUDE
            )
            if not self._passes_filter(signal):
                continue
            existing = self.contacts.get(target.id)
            if existing is not None:
                existing.distance = dist
                existing.bearing = self._bearing_to(target)
                existing.signal = signal
                record.updated_ids.append(target.id)
                continue
            if len(self.contacts) >= self.capacity:
                record.overflow_ids.append(target.id)
                continue
            self.contacts[target.id] = Contact(
                id=target.id,
                bearing=self._bearing_to(target),
                distance=dist,
                signal=signal,
            )
            record.new_ids.append(target.id)
        self.history.append(record)
        if self.phase is GamePhase.READY:
            self.phase = GamePhase.SEARCHING
        return record

    def undo_scan(self) -> ScanRecord:
        """撤销最近一次扫描的登记效果，但保留历史记录。"""
        if not self.history:
            raise GameError("没有可撤销的扫描记录")
        record = self.history[-1]
        if record.undone:
            raise GameError("最近一次扫描已被撤销，请勿重复撤销")
        for cid in record.new_ids:
            contact = self.contacts.get(cid)
            if contact is None:
                continue
            if contact.status is ContactStatus.LOCKED:
                continue
            del self.contacts[cid]
        record.undone = True
        return record

    def lock(self, contact_id: str) -> Contact:
        contact = self.contacts.get(contact_id)
        if contact is None:
            raise GameError("未知接触目标: %s" % contact_id)
        if self.locked_id == contact_id:
            raise GameError("目标 %s 已锁定，请勿重复锁定" % contact_id)
        if self.locked_id is not None and self.locked_id in self.contacts:
            self.contacts[self.locked_id].status = ContactStatus.DETECTED
        contact.status = ContactStatus.LOCKED
        self.locked_id = contact_id
        self.locked_distance = contact.distance
        self.phase = GamePhase.LOCKED
        return contact

    def change_heading(self, degrees: float) -> None:
        """改变航向；转向中潜艇前进一步，锁定目标距离随之更新。"""
        self.heading = (self.heading + float(degrees)) % 360.0
        self.x += TURN_STEP * math.cos(math.radians(self.heading))
        self.y += TURN_STEP * math.sin(math.radians(self.heading))
        self._refresh_contact_geometry()

    def _refresh_contact_geometry(self) -> None:
        for cid, contact in self.contacts.items():
            target = self._target_index.get(cid)
            if target is None:
                continue
            contact.distance = self._distance_to(target)
            contact.bearing = self._bearing_to(target)
        if self.locked_id is not None and self.locked_id in self.contacts:
            self.locked_distance = self.contacts[self.locked_id].distance

    def set_depth(self, depth: float) -> None:
        self.depth = max(0.0, min(MAX_DEPTH, float(depth)))

    def set_silent(self, enabled: bool) -> None:
        self.silent = bool(enabled)

    # ---- 存档 -----------------------------------------------------------

    def to_dict(self) -> dict:
        rng_state = self.rng.getstate()
        return {
            "version": SAVE_VERSION,
            "seed": self.seed,
            "capacity": self.capacity,
            "battery": self.battery,
            "heading": self.heading,
            "depth": self.depth,
            "x": self.x,
            "y": self.y,
            "sea_state": self.sea_state.value,
            "silent": self.silent,
            "phase": self.phase.value,
            "locked_id": self.locked_id,
            "locked_distance": self.locked_distance,
            "scan_seq": self._scan_seq,
            "contacts": [c.to_dict() for c in self.contacts.values()],
            "history": [r.to_dict() for r in self.history],
            "targets": [t.to_dict() for t in self.targets],
            "rng_state": [rng_state[0], list(rng_state[1]), rng_state[2]],
        }

    def save(self, path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "SonarGame":
        if not isinstance(data, dict) or not data:
            raise SaveGameError("存档数据为空或格式非法")
        for key in ("battery", "contacts", "targets"):
            if key not in data:
                raise SaveGameError("存档缺少必要字段: %s" % key)
        game = cls(
            seed=int(data.get("seed", 70)),
            capacity=int(data.get("capacity", DEFAULT_CONTACT_CAPACITY)),
            targets=[Target.from_dict(t) for t in data["targets"]],
        )
        game.battery = float(data["battery"])
        game.heading = float(data.get("heading", 0.0))
        game.depth = float(data.get("depth", 60.0))
        game.x = float(data.get("x", 0.0))
        game.y = float(data.get("y", 0.0))
        game.sea_state = SeaState(data.get("sea_state", SeaState.CALM.value))
        game.silent = bool(data.get("silent", False))
        game.phase = GamePhase(data.get("phase", GamePhase.READY.value))
        game.locked_id = data.get("locked_id")
        game.locked_distance = data.get("locked_distance")
        if game.locked_distance is not None:
            game.locked_distance = float(game.locked_distance)
        game._scan_seq = int(data.get("scan_seq", 0))
        # 接触列表按 id 去重，保证加载后与内存模型一致。
        game.contacts = {}
        for item in data["contacts"]:
            contact = Contact.from_dict(item)
            game.contacts[contact.id] = contact
        game.history = [ScanRecord.from_dict(r) for r in data.get("history", [])]
        rng_state = data.get("rng_state")
        if rng_state is not None:
            game.rng.setstate(
                (int(rng_state[0]), tuple(int(v) for v in rng_state[1]), rng_state[2])
            )
        return game

    @classmethod
    def load(cls, path) -> "SonarGame":
        text = Path(path).read_text(encoding="utf-8")
        if not text.strip():
            raise SaveGameError("存档文件为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SaveGameError("存档不是合法 JSON: %s" % exc) from exc
        return cls.from_dict(data)
