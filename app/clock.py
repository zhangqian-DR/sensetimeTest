"""UTC integer-millisecond clocks; simulated changes commit before returning."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .models import AppMeta


def parse_time_ms(value):
    if isinstance(value, bool):
        raise ValueError('Time must not be boolean')
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 'T' in value:
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError('Provide full ISO8601 date, time and timezone')
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond % 1000:
        raise ValueError('Provide timezone and at most millisecond precision')
    return int(parsed.timestamp() * 1000)


def format_time(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, ZoneInfo('Asia/Shanghai')).isoformat(timespec='milliseconds')


class SystemClock:
    def now_ms(self):
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def advance_to(self, target_ms):
        raise ValueError('real mode prohibits simulated time advancement')


class SimulatedClock:
    def __init__(self, database, initial=None):
        self.database = database
        self.initial = parse_time_ms(initial if initial is not None else database.settings.simulated_start)
        with self.database.session_factory.begin() as session:
            if session.get(AppMeta, 'virtual_now') is None:
                session.add(AppMeta(key='virtual_now', value=str(self.initial)))

    def now_ms(self):
        with self.database.session_factory() as session:
            return int(session.get(AppMeta, 'virtual_now').value)

    def advance_to(self, target_ms):
        target = parse_time_ms(target_ms)
        with self.database.session_factory.begin() as session:
            row = session.get(AppMeta, 'virtual_now')
            if target < int(row.value):
                raise ValueError('Simulated time cannot move backwards')
            row.value = str(target)
            row.updated_at = target
        return target


def make_clock(database, settings):
    return SimulatedClock(database, settings.simulated_start) if settings.time_mode == 'simulated' else SystemClock()