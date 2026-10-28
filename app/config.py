"""应用配置：启动时从环境变量读取，修改后重启生效（不在运行中切换）。

对应 Codex 设计的双时间模式：
- real：使用真实北京时间，禁止虚拟时间推进接口；
- simulated：通过 Swagger 推进虚拟时间，推进后自动检查，禁止倒退。
两种模式默认使用不同数据库，避免演示数据污染。
"""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    time_mode: str                 # real / simulated
    timezone: str                  # 如 Asia/Shanghai
    check_interval_seconds: int    # 自动检查间隔（生产调度用，测试不依赖）
    simulated_start: str           # 模拟起始时间（仅首次初始化使用）
    db_path: str                   # SQLite 文件路径

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_settings() -> Settings:
    mode = _env("APP_TIME_MODE", "simulated").strip().lower()
    if mode not in ("real", "simulated"):
        mode = "simulated"
    timezone = _env("APP_TIMEZONE", "Asia/Shanghai")
    interval = int(_env("APP_CHECK_INTERVAL_SECONDS", "60"))
    simulated_start = _env("APP_SIMULATED_START", "2026-10-28T14:00:00+08:00")
    # 两种模式默认不同数据库，避免演示数据污染
    default_db = "./data/simulated.db" if mode == "simulated" else "./data/real.db"
    db_path = _env("APP_DB_PATH", default_db)
    return Settings(mode, timezone, interval, simulated_start, db_path)


settings = load_settings()
