// Package cache provides a Redis-backed search result cache for the Go API Gateway.
//
// Architecture (from Flow.png):
//   Step 1 — Go Gateway checks Redis BEFORE calling PyWorker (cache HIT → return immediately)
//   Step 4 — After PyWorker returns results, STORE them in Redis with TTL (cache MISS path)
//
// Usage:
//
//	c := cache.New()           // reads REDIS_* env vars
//	if hit, ok := c.Get(q); ok { writeJSON(w, 200, hit); return }
//	result := merger.Merge(...)
//	c.Set(q, result)
package cache

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// Client wraps a Redis connection with search-specific helpers.
type Client struct {
	rdb              *redis.Client
	ttlDefault       time.Duration
	ttlPopular       time.Duration
	popularThreshold int64
}

// New constructs a Client from environment variables:
//
//	REDIS_HOST             (default: localhost)
//	REDIS_PORT             (default: 6379)
//	REDIS_DB               (default: 0)
//	REDIS_PASSWORD         (default: "")
//	REDIS_TTL_DEFAULT      (default: 300  seconds)
//	REDIS_TTL_POPULAR      (default: 1800 seconds)
//	REDIS_POPULAR_THRESHOLD(default: 10   hits)
func New() *Client {
	host := getEnv("REDIS_HOST", "localhost")
	port := getEnv("REDIS_PORT", "6379")
	db := parseInt(getEnv("REDIS_DB", "0"))
	password := getEnv("REDIS_PASSWORD", "")

	ttlDefault := time.Duration(parseInt(getEnv("REDIS_TTL_DEFAULT", "300"))) * time.Second
	ttlPopular := time.Duration(parseInt(getEnv("REDIS_TTL_POPULAR", "1800"))) * time.Second
	popularThreshold := int64(parseInt(getEnv("REDIS_POPULAR_THRESHOLD", "10")))

	rdb := redis.NewClient(&redis.Options{
		Addr:     fmt.Sprintf("%s:%s", host, port),
		Password: password,
		DB:       db,
	})

	// Non-fatal connection check — gateway degrades gracefully if Redis is down.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Printf("[WARN] Redis unreachable at %s:%s — cache disabled: %v", host, port, err)
	} else {
		log.Printf("[+] Redis connected at %s:%s (db=%d, ttl=%v)", host, port, db, ttlDefault)
	}

	return &Client{
		rdb:              rdb,
		ttlDefault:       ttlDefault,
		ttlPopular:       ttlPopular,
		popularThreshold: popularThreshold,
	}
}

// Key returns the canonical Redis key for a search query string.
// Format: "search:<sha256-hex(query)>"
func Key(query string) string {
	h := sha256.Sum256([]byte(query))
	return fmt.Sprintf("search:%x", h)
}

// Get retrieves a cached search result. Returns (result, true) on HIT, (nil, false) on MISS.
// Increments a hit counter so popular queries get longer TTLs on re-set.
func (c *Client) Get(query string) (json.RawMessage, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	key := Key(query)
	val, err := c.rdb.Get(ctx, key).Bytes()
	if err != nil {
		if err != redis.Nil {
			log.Printf("[WARN] Redis GET error for %q: %v", key, err)
		}
		return nil, false
	}

	// Increment hit counter (fire-and-forget; ignore error)
	_ = c.rdb.Incr(ctx, "hits:"+key)
	return json.RawMessage(val), true
}

// Set stores search results in Redis. TTL is extended for popular queries.
// v must be JSON-serialisable.
func (c *Client) Set(query string, v any) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	key := Key(query)
	data, err := json.Marshal(v)
	if err != nil {
		log.Printf("[WARN] Redis marshal error: %v", err)
		return
	}

	// Choose TTL based on hit count
	ttl := c.ttlDefault
	hits, _ := c.rdb.Get(ctx, "hits:"+key).Int64()
	if hits >= c.popularThreshold {
		ttl = c.ttlPopular
	}

	if err := c.rdb.Set(ctx, key, data, ttl).Err(); err != nil {
		log.Printf("[WARN] Redis SET error for %q: %v", key, err)
	}
}

// Healthy returns true if Redis responds to PING within 1 second.
func (c *Client) Healthy() bool {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	return c.rdb.Ping(ctx).Err() == nil
}

// Close shuts down the Redis connection pool.
func (c *Client) Close() error {
	return c.rdb.Close()
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func parseInt(s string) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return n
}
