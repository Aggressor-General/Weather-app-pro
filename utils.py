"""Small shared helpers: an in-memory TTL cache and a per-client usage limiter.

Both are in-memory, which is fine for a single server process. For multiple
servers, swap them for Redis (the interfaces are deliberately tiny).
"""
import threading
import time
from collections import defaultdict


class TTLCache:
    """Key/value cache where every entry expires after `ttl_seconds`."""

    def __init__(self, ttl_seconds, max_items=500):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key, value):
        with self._lock:
            if len(self._data) >= self.max_items:
                now = time.time()
                for k in [k for k, (exp, _) in self._data.items() if exp < now]:
                    del self._data[k]
                if len(self._data) >= self.max_items:
                    oldest = min(self._data, key=lambda k: self._data[k][0])
                    del self._data[oldest]
            self._data[key] = (time.time() + self.ttl, value)


class UsageLimiter:
    """Allows at most `per_minute` and `per_day` calls per client key."""

    def __init__(self, per_minute, per_day):
        self.per_minute = per_minute
        self.per_day = per_day
        self._hits = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits[key] if now - t < 86400]
            recent = [t for t in hits if now - t < 60]
            if len(recent) >= self.per_minute or len(hits) >= self.per_day:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True
