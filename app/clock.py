"""时钟：real 模式返回真实北京时间；simulated 模式使用持久化虚拟时间。

- 模拟起始时间仅在首次初始化时使用，之后（含重启）读取已保存时间；
- 禁止时间倒退；
- 测试可使用 TestClock（手动设置），不依赖环境变量。
"""
from datetime import datetime, timedelta

from .config import settings
from .models import AppMeta

VIRTUAL_NOW_KEY = "virtual_now"


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError

    def advance(self, **kwargs) -> datetime:
        raise NotImplementedError


class SystemClock(Clock):
    """real 模式：真实时间，禁止推进虚拟时间。"""
    def now(self) -> datetime:
        return datetime.now(settings.tz)

    def advance(self, **kwargs) -> datetime:
        raise RuntimeError("real 模式禁止推进虚拟时间")


class SimulatedClock(Clock):
    """simulated 模式：虚拟时间持久化在 app_meta，重启后保留。"""
    def __init__(self, db):
        self.db = db

    def now(self) -> datetime:
        meta = self.db.get(AppMeta, VIRTUAL_NOW_KEY)
        if meta and meta.value:
            return datetime.fromisoformat(meta.value)
        start = datetime.fromisoformat(settings.simulated_start)
        self._save(start)
        return start

    def _save(self, t: datetime) -> None:
        meta = self.db.get(AppMeta, VIRTUAL_NOW_KEY)
        if meta:
            meta.value = t.isoformat()
        else:
            self.db.add(AppMeta(key=VIRTUAL_NOW_KEY, value=t.isoformat()))
        self.db.flush()

    def advance(self, minutes: int = 0, hours: int = 0, seconds: int = 0) -> datetime:
        cur = self.now()
        nxt = cur + timedelta(minutes=minutes, hours=hours, seconds=seconds)
        if nxt < cur:
            raise ValueError("禁止时间倒退")
        self._save(nxt)
        return nxt

    def set(self, t: datetime) -> datetime:
        cur = self.now()
        if t < cur:
            raise ValueError("禁止时间倒退")
        self._save(t)
        return t


class TestClock(Clock):
    """测试用：完全可控，可自由设置（允许倒退，便于构造场景）。"""
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, minutes: int = 0, hours: int = 0, seconds: int = 0) -> datetime:
        self._now += timedelta(minutes=minutes, hours=hours, seconds=seconds)
        return self._now

    def set(self, t: datetime) -> datetime:
        self._now = t
        return t


def make_clock(db) -> Clock:
    """根据配置创建生产时钟。"""
    if settings.time_mode == "real":
        return SystemClock()
    return SimulatedClock(db)
