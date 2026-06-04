"""
backend/cache/redis_cache.py
=============================
Industry-grade Redis cache for HPE Search search results.

Architecture role (from diagram)
---------------------------------
  Step 1 — Go API Gateway checks Redis (query hash → cache HIT → return immediately)
  Step 4 — After OpenSearch/Qdrant return Top-K results, STORE them in Redis with TTL

This module owns Step 4: storing merged search results after they come back
from OpenSearch. The Go API Gateway (Prarthana) owns Step 1 (the cache check
before calling the search layer).

The Python search layer calls `cache_search_results()` after every cache MISS
so the next identical query is served from Redis in <1 ms.

Owner: Search Database Administrator (Priyadarshini Sarja)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from redis.retry import Retry
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)

def _get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(_JsonFormatter())
        logger.addHandler(h)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logger

log = _get_logger("hpe_search.redis_cache")


# ── Configuration ─────────────────────────────────────────────────────────────

REDIS_HOST         = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT         = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB           = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD     = os.getenv("REDIS_PASSWORD", None)

# TTL strategy (seconds)
TTL_DEFAULT        = int(os.getenv("REDIS_TTL_DEFAULT",   "300"))   #  5 min — general queries
TTL_FILTERED       = int(os.getenv("REDIS_TTL_FILTERED",  "600"))   # 10 min — filtered (more specific, less likely to change)
TTL_POPULAR        = int(os.getenv("REDIS_TTL_POPULAR",  "1800"))   # 30 min — high-hit-count queries

# Promote to popular after this many hits
POPULAR_THRESHOLD  = int(os.getenv("REDIS_POPULAR_THRESHOLD", "10"))

# Key prefixes
PREFIX_RESULT      = "hpe-search:search:result:"
PREFIX_HITCOUNT    = "hpe-search:search:hits:"
PREFIX_STATS       = "hpe-search:cache:stats"


# ── Query hasher ──────────────────────────────────────────────────────────────

def build_cache_key(
    query_text: str,
    *,
    extension:  Optional[str]       = None,
    bucket:     Optional[str]       = None,
    owner:      Optional[str]       = None,
    tags:       Optional[List[str]] = None,
    date_from:  Optional[str]       = None,
    date_to:    Optional[str]       = None,
    size:       int                 = 10,
    from_:      int                 = 0,
) -> str:
    """
    Deterministic cache key from query + all filter parameters.
    Matches the hashing logic the Go API Gateway must replicate in cache/redis.go.

    Algorithm: SHA-256 of canonical JSON string → hex digest prefix.
    Canonical = sorted keys, lowercase query, sorted tags.
    """
    canonical = {
        "q":         query_text.strip().lower(),
        "extension": (extension or "").lower(),
        "bucket":    bucket or "",
        "owner":     owner or "",
        "tags":      sorted(tags or []),
        "date_from": date_from or "",
        "date_to":   date_to or "",
        "size":      size,
        "from":      from_,
    }
    raw     = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest  = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"{PREFIX_RESULT}{digest}"


def _hit_count_key(cache_key: str) -> str:
    digest = cache_key.removeprefix(PREFIX_RESULT)
    return f"{PREFIX_HITCOUNT}{digest}"


# ── Cache client ──────────────────────────────────────────────────────────────

class RedisCache:
    """
    Thread-safe Redis cache for search results.

    Usage pattern (search layer)
    ----------------------------
        cache = RedisCache()

        key  = build_cache_key(query_text, extension="pdf")
        hit  = cache.get(key)
        if hit:
            return hit                          # served from cache

        results = opensearch_client.search(...) # cache MISS — go to OpenSearch
        cache.set(key, results, has_filters=bool(extension))
        return results
    """

    def __init__(self) -> None:
        retry = Retry(ExponentialBackoff(cap=10, base=0.5), retries=5)
        self._r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            retry=retry,
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
            health_check_interval=30,
        )
        log.info("Redis cache client initialised at %s:%d db=%d", REDIS_HOST, REDIS_PORT, REDIS_DB)

    # ── Core get/set ──────────────────────────────────────────────────────────

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Returns cached search results dict, or None on miss/error.
        Also increments the hit counter and promotes to longer TTL if popular.
        """
        try:
            raw = self._r.get(cache_key)
            if raw is None:
                self._increment_stat("miss")
                return None

            # Increment hit count
            hit_key   = _hit_count_key(cache_key)
            hit_count = self._r.incr(hit_key)
            self._r.expire(hit_key, TTL_POPULAR * 2)

            # Promote TTL for popular queries
            if hit_count == POPULAR_THRESHOLD:
                self._r.expire(cache_key, TTL_POPULAR)
                log.info("Promoted cache key to popular TTL (hits=%d): %s", hit_count, cache_key)

            self._increment_stat("hit")
            result = json.loads(raw)
            result["_cache"] = {
                "hit":       True,
                "hit_count": hit_count,
                "key":       cache_key,
            }
            return result

        except Exception as exc:
            log.warning("Redis GET failed (returning None): %s", exc)
            self._increment_stat("error")
            return None

    def set(
        self,
        cache_key:   str,
        results:     Dict[str, Any],
        *,
        has_filters: bool = False,
        ttl:         Optional[int] = None,
    ) -> bool:
        """
        Store search results in Redis.

        TTL selection:
          - Explicit ttl overrides everything
          - has_filters=True → TTL_FILTERED (10 min)
          - default          → TTL_DEFAULT  ( 5 min)
        Promoted to TTL_POPULAR automatically after POPULAR_THRESHOLD hits.

        Returns True on success.
        """
        if ttl is None:
            ttl = TTL_FILTERED if has_filters else TTL_DEFAULT

        # Strip internal cache metadata before storing
        payload = {k: v for k, v in results.items() if not k.startswith("_cache")}
        payload["_stored_at"] = datetime.now(timezone.utc).isoformat()

        try:
            self._r.setex(cache_key, ttl, json.dumps(payload))
            self._increment_stat("store")
            log.info("Cached result (ttl=%ds): %s", ttl, cache_key)
            return True
        except Exception as exc:
            log.warning("Redis SET failed (non-fatal): %s", exc)
            self._increment_stat("error")
            return False

    def invalidate(self, cache_key: str) -> bool:
        """Explicitly remove a single cached result."""
        try:
            self._r.delete(cache_key)
            return True
        except Exception as exc:
            log.warning("Redis DELETE failed: %s", exc)
            return False

    def invalidate_all(self) -> int:
        """
        Flush all HPE Search cache keys.
        Used after bulk re-ingestion when results would be stale.
        Returns number of keys deleted.
        """
        try:
            keys = list(self._r.scan_iter(f"{PREFIX_RESULT}*"))
            if keys:
                self._r.delete(*keys)
            log.info("Invalidated %d cache keys", len(keys))
            return len(keys)
        except Exception as exc:
            log.warning("Bulk invalidation failed: %s", exc)
            return 0

    # ── Health & stats ────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Returns True when Redis is reachable."""
        try:
            return self._r.ping()
        except Exception:
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns cache hit/miss/error counters + Redis INFO snapshot.
        Exposed at GET /cache/stats by the Go API.
        """
        try:
            raw_stats = self._r.hgetall(PREFIX_STATS)
            info      = self._r.info("stats")
            key_count = sum(1 for _ in self._r.scan_iter(f"{PREFIX_RESULT}*"))

            hits   = int(raw_stats.get("hit",   0))
            misses = int(raw_stats.get("miss",  0))
            total  = hits + misses

            return {
                "hits":              hits,
                "misses":            misses,
                "stores":            int(raw_stats.get("store", 0)),
                "errors":            int(raw_stats.get("error", 0)),
                "hit_rate_pct":      round(hits / total * 100, 2) if total else 0.0,
                "cached_keys":       key_count,
                "redis_evictions":   info.get("evicted_keys", 0),
                "redis_memory_used": info.get("used_memory_human", "n/a"),
            }
        except Exception as exc:
            log.error("Failed to fetch cache stats: %s", exc)
            return {}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _increment_stat(self, field: str) -> None:
        try:
            self._r.hincrby(PREFIX_STATS, field, 1)
        except Exception:
            pass   # stats are best-effort


# ── Cached search wrapper ─────────────────────────────────────────────────────

def cached_search(
    query_text:    str,
    query_vector:  List[float],
    search_fn,                       # callable: (query_text, query_vector, **filters) → dict
    *,
    extension:     Optional[str]       = None,
    bucket:        Optional[str]       = None,
    owner:         Optional[str]       = None,
    tags:          Optional[List[str]] = None,
    date_from:     Optional[str]       = None,
    date_to:       Optional[str]       = None,
    size:          int                 = 10,
    from_:         int                 = 0,
    cache:         Optional[RedisCache] = None,
) -> Dict[str, Any]:
    """
    Drop-in wrapper that adds Redis caching around any search_fn call.

    Flow:
        1. Build deterministic cache key
        2. Check Redis → return immediately on HIT
        3. On MISS: call search_fn (OpenSearch hybrid query)
        4. Store result in Redis with appropriate TTL
        5. Return result

    This function is the single integration point between OpenSearch results
    and Redis caching — call it from the gRPC search worker or directly from
    integration tests.

    Parameters
    ----------
    search_fn : callable with signature
        (query_text, query_vector, extension, bucket, owner, tags,
         date_from, date_to, size, from_) → dict
    """
    _cache = cache or _get_default_cache()

    has_filters = any([extension, bucket, owner, tags, date_from, date_to])
    cache_key   = build_cache_key(
        query_text,
        extension=extension,
        bucket=bucket,
        owner=owner,
        tags=tags,
        date_from=date_from,
        date_to=date_to,
        size=size,
        from_=from_,
    )

    # ── Step 1: cache check ───────────────────────────────────────────────────
    t0  = time.perf_counter()
    hit = _cache.get(cache_key)
    if hit is not None:
        hit["_latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        log.info("Cache HIT (%.1f ms): %s", hit["_latency_ms"], query_text[:60])
        return hit

    # ── Step 3: search (cache MISS) ───────────────────────────────────────────
    log.info("Cache MISS — querying OpenSearch: %s", query_text[:60])
    results = search_fn(
        query_text,
        query_vector,
        extension=extension,
        bucket=bucket,
        owner=owner,
        tags=tags,
        date_from=date_from,
        date_to=date_to,
        size=size,
        from_=from_,
    )

    # ── Step 4: store result ──────────────────────────────────────────────────
    _cache.set(cache_key, results, has_filters=has_filters)

    results["_cache"]      = {"hit": False, "key": cache_key}
    results["_latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return results


# ── Module-level singleton ────────────────────────────────────────────────────

_default_cache: Optional[RedisCache] = None

def _get_default_cache() -> RedisCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = RedisCache()
    return _default_cache

def get_cache() -> RedisCache:
    return _get_default_cache()
