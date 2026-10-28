"""启动时严格校验配置；测试凭证仅用于本地模拟。"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    time_mode: str = 'simulated'
    timezone: str = 'Asia/Shanghai'
    check_interval_seconds: float = 1
    simulated_start: str = '2026-10-28T14:00:00+08:00'
    db_path: str = './data/simulated.db'
    test_tokens: dict = field(default_factory=lambda: {'demo-token-a': 'user-a', 'demo-token-b': 'user-b'})
    demo_admin_token: str = 'local-demo-admin-only'
    enable_demo_controls: bool = True

    @property
    def tz(self):
        return ZoneInfo(self.timezone)


def load_settings(environ=None):
    env = os.environ if environ is None else environ
    mode = env.get('APP_TIME_MODE', 'simulated').strip().lower()
    if mode not in ('real', 'simulated'):
        raise ValueError('APP_TIME_MODE must be real or simulated')
    zone = env.get('APP_TIMEZONE', 'Asia/Shanghai')
    if zone != 'Asia/Shanghai':
        raise ValueError('APP_TIMEZONE must be Asia/Shanghai')
    ZoneInfo(zone)
    interval = float(env.get('APP_CHECK_INTERVAL_SECONDS', '1'))
    import math
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('APP_CHECK_INTERVAL_SECONDS must be positive and finite')
    start = env.get('APP_SIMULATED_START', '2026-10-28T14:00:00+08:00')
    parsed = datetime.fromisoformat(start)
    if 'T' not in start or parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond % 1000:
        raise ValueError('APP_SIMULATED_START needs date, time, timezone and millisecond precision')
    tokens = json.loads(env.get('APP_TEST_TOKENS_JSON', '{"demo-token-a":"user-a","demo-token-b":"user-b"}'))
    if not isinstance(tokens, dict) or not tokens or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in tokens.items()):
        raise ValueError('APP_TEST_TOKENS_JSON must map nonempty tokens to users')
    admin = env.get('APP_DEMO_ADMIN_TOKEN', 'local-demo-admin-only')
    if not admin or admin in tokens:
        raise ValueError('Demo admin token must be nonempty and distinct from user tokens')
    controls = env.get('APP_ENABLE_DEMO_CONTROLS', 'true').lower()
    if controls not in ('true', 'false'):
        raise ValueError('APP_ENABLE_DEMO_CONTROLS must be true or false')
    return Settings(mode, zone, interval, start, env.get('APP_DB_PATH', './data/' + mode + '.db'), tokens, admin, controls == 'true')