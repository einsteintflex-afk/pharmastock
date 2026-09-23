# ============================================================
# RATE LIMITING
# ============================================================
# Sliding-window limits kept in memory per process:
#   * sign-in and password-reset endpoints: per client IP (brute force)
#   * all other API calls: per session token, or per IP when anonymous
# With several application processes each keeps its own window; the
# reverse proxy (deploy/nginx.conf) adds a shared per-IP limit in front.

import threading
import time
from collections import deque


class SlidingWindow:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record a request. Returns None if allowed, else seconds to wait."""
        if self.limit <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, hits[0] + self.window - now)
            hits.append(now)
            if len(self._hits) > 50_000:  # bound memory: drop idle keys
                for idle in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[idle]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
