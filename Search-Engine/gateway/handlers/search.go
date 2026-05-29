// Package handlers contains HTTP handler implementations for the API Gateway.
package handlers

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"

	"github.com/hpe/search-engine/gateway/cache"
	"github.com/hpe/search-engine/gateway/grpcclient"
	"github.com/hpe/search-engine/gateway/merger"
)

// SearchHandler holds dependencies for the search and health endpoints.
type SearchHandler struct {
	client *grpcclient.Client
	cache  *cache.Client
}

// NewSearchHandler constructs a SearchHandler with the given gRPC client and Redis cache.
func NewSearchHandler(client *grpcclient.Client, redisCache *cache.Client) *SearchHandler {
	return &SearchHandler{client: client, cache: redisCache}
}

// Search handles GET /search?q=<query>&limit=<n>
//
// Pipeline (matches Flow.png):
//
//	Step 1 — Check Redis cache  → HIT: return immediately
//	Step 2 — gRPC call to PyWorker-2 (NLP parse + embed + OpenSearch hybrid query)
//	Step 3 — Merge & deduplicate results
//	Step 4 — Store result in Redis with TTL
func (h *SearchHandler) Search(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		writeJSON(w, http.StatusOK, merger.SearchResponse{Results: []merger.Result{}})
		return
	}

	limitStr := r.URL.Query().Get("limit")
	limit := 10
	if limitStr != "" {
		if n, err := strconv.Atoi(limitStr); err == nil && n > 0 {
			limit = n
		}
	}

	log.Printf("[*] Search request: q=%q limit=%d", query, limit)

	// ── Step 1: Redis cache check ─────────────────────────────────────────────
	if cached, ok := h.cache.Get(query); ok {
		log.Printf("[*] Cache HIT for %q", query)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(cached)
		return
	}
	// ─────────────────────────────────────────────────────────────────────────

	// ── Step 2: Cache miss → gRPC call to PyWorker-2 ─────────────────────────
	protoResp, err := h.client.Search(query, int32(limit))
	if err != nil {
		log.Printf("[!] gRPC error: %v", err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "Search service unavailable. Please try again.",
		})
		return
	}

	// ── Step 3: Merge & deduplicate ───────────────────────────────────────────
	result := merger.Merge(protoResp, limit)

	// ── Step 4: Store in Redis cache ──────────────────────────────────────────
	h.cache.Set(query, result)
	// ─────────────────────────────────────────────────────────────────────────

	log.Printf("[+] Returning %d results for %q (intent: %q)",
		len(result.Results), query, result.IntentText)

	w.Header().Set("X-Cache", "MISS")
	writeJSON(w, http.StatusOK, result)
}

// Health handles GET /health — checks gRPC and Redis reachability.
func (h *SearchHandler) Health(w http.ResponseWriter, r *http.Request) {
	_, err := h.client.Search("health-check", 1)
	status := "ok"
	pyworkerStatus := "reachable"
	httpStatus := http.StatusOK

	if err != nil {
		status = "degraded"
		pyworkerStatus = "unreachable"
		httpStatus = http.StatusServiceUnavailable
		log.Printf("[!] Health check — PyWorker-2 unreachable: %v", err)
	}

	redisStatus := "reachable"
	if !h.cache.Healthy() {
		redisStatus = "unreachable"
		// Redis failure is non-fatal — search still works, just uncached
		log.Printf("[WARN] Health check — Redis unreachable")
	}

	writeJSON(w, httpStatus, map[string]string{
		"status":   status,
		"pyworker": pyworkerStatus,
		"redis":    redisStatus,
	})
}

// writeJSON is a helper that sets Content-Type and serialises v as JSON.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("[!] JSON encode error: %v", err)
	}
}
