"""单进程后台周期检查，支持有界且可中断的停止。"""
from datetime import datetime, timezone
import logging
import threading

logger = logging.getLogger(__name__)


class BackgroundScheduler:
    def __init__(self, check, interval_seconds):
        if interval_seconds <= 0:
            raise ValueError('check interval must be positive')
        self.check = check
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None
        self.last_checked_at = None

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='bill-followup-check', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                self.check()
            except Exception as error:
                self.last_error = f'{type(error).__name__}: {error}'
                logger.exception('Background check failed; next interval will check again')
            finally:
                self.last_checked_at = datetime.now(timezone.utc).isoformat()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.is_alive:
            raise RuntimeError('Background check did not stop within 5 seconds')