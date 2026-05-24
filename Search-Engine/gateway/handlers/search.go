// Package handlers contains HTTP handler implementations for the API Gateway.
package handlers

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"

	"github.com/hpe/search-engine/gateway/grpcclient"
	"github.com/hpe/search-engine/gateway/merger"
)

// SearchHandler holds dependencies for the search and health endpoints.
type SearchHandler struct {
	client *grpcclient.Client
}

// NewSearchHandler constructs a SearchHandler with the given gRPC client.
func NewSearchHandler(client *grpcclient.Client) *SearchHandler {
	return &SearchHandler{client: client}
}

// Search handles GET /search?q=<query>&limit=<n>
//
// Integration points for Role 3 (Redis caching):
//
//	BEFORE the gRPC call  → check Redis cache by query hash
//	AFTER  the gRPC call  → store merged result in Redis with TTL
//
// Those hooks are marked with "// [ROLE-3 HOOK]" comments below.
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

	// [ROLE-3 HOOK] ── Cache check ─────────────────────────────────────────
	// queryHash := cache.Hash(query)
	// if cached, ok := cache.Get(queryHash); ok {
	//     log.Printf("[*] Cache HIT for %q", query)
	//     writeJSON(w, http.StatusOK, cached)
	//     return
	// }
	// ──────────────────────────────────────────────────────────────────────

	// Cache miss → call PyWorker-2 over gRPC
	protoResp, err := h.client.Search(query, int32(limit))
	if err != nil {
		log.Printf("[!] gRPC error: %v", err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "Search service unavailable. Please try again.",
		})
		return
	}

	result := merger.Merge(protoResp, limit)

	// [ROLE-3 HOOK] ── Cache store ─────────────────────────────────────────
	// cache.Set(queryHash, result, cache.DefaultTTL)
	// ──────────────────────────────────────────────────────────────────────

	log.Printf("[+] Returning %d results for %q (intent: %q)",
		len(result.Results), query, result.IntentText)

	writeJSON(w, http.StatusOK, result)
}

// Health handles GET /health — checks gRPC reachability with a no-op query.
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

	writeJSON(w, httpStatus, map[string]string{
		"status":   status,
		"pyworker": pyworkerStatus,
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
