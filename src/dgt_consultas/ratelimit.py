"""Thread-safe AIMD token-bucket rate limiter.

Shared by every PETETE request in the scraper.
"""

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """Thread-safe AIMD token-bucket rate limiter.

    Additive-increase, multiplicative-decrease: each success nudges the rate
    up by ``aimd_step`` (capped at ``max_rate``); each failure halves it
    (floored at ``min_rate``). Same shape as TCP congestion control — the
    bucket converges on whatever sustained rate the server tolerates.
    """

    rate: float = 1.0  # tokens per second (current target)
    burst: int = 5
    min_rate: float = 0.2
    max_rate: float = 4.0
    aimd_step: float = 0.1
    success_threshold: int = 10  # successes between additive bumps
    _tokens: float = field(init=False, default=0.0)
    _last: float = field(init=False, default=0.0)
    _success_streak: int = field(init=False, default=0)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.rate = max(self.min_rate, min(self.max_rate, self.rate))
        self._tokens = float(self.burst)
        self._last = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens < 1:
                sleep_time = (1 - self._tokens) / self.rate
                self._tokens = 0
            else:
                self._tokens -= 1
                sleep_time = 0.0
        if sleep_time > 0:
            logger.debug(
                "Rate limit: sleeping %.2fs (rate=%.2f/s)", sleep_time, self.rate
            )
            time.sleep(sleep_time)
            with self._lock:
                self._last = time.monotonic()

    def record_success(self) -> None:
        """Additive increase after a streak of successes."""
        with self._lock:
            self._success_streak += 1
            if (
                self._success_streak >= self.success_threshold
                and self.rate < self.max_rate
            ):
                old = self.rate
                self.rate = min(self.max_rate, self.rate + self.aimd_step)
                self._success_streak = 0
                logger.info("AIMD: rate %.2f -> %.2f/s", old, self.rate)

    def record_failure(self) -> None:
        """Multiplicative decrease on server errors."""
        with self._lock:
            self._success_streak = 0
            if self.rate > self.min_rate:
                old = self.rate
                self.rate = max(self.min_rate, self.rate * 0.5)
                logger.warning(
                    "AIMD: rate %.2f -> %.2f/s (server pressure)", old, self.rate
                )
