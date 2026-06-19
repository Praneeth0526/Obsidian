package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/hpe/search-engine/gateway/cache"
	"github.com/hpe/search-engine/gateway/grpcclient"
	"github.com/hpe/search-engine/gateway/handlers"
)

func main() {
	port := getEnv("GATEWAY_PORT", "8080")
	pyworkerHost := getEnv("PYWORKER_HOST", "localhost")
	pyworkerPort := getEnv("PYWORKER_PORT", "50052")

	// ── Redis cache (Step 1 & 4 of search pipeline) ───────────────────────────
	redisCache := cache.New()
	defer redisCache.Close()

	// ── gRPC connection to PyWorker-2 ─────────────────────────────────────────
	pyworkerAddr := fmt.Sprintf("%s:%s", pyworkerHost, pyworkerPort)
	client, err := grpcclient.New(pyworkerAddr)
	if err != nil {
		log.Fatalf("[FATAL] Cannot connect to PyWorker-2 at %s: %v", pyworkerAddr, err)
	}
	defer client.Close()

	log.Printf("[+] Connected to PyWorker-2 at %s", pyworkerAddr)

	// ── HTTP routes ───────────────────────────────────────────────────────────
	mux := http.NewServeMux()

	searchHandler := handlers.NewSearchHandler(client, redisCache)
	mux.HandleFunc("GET /search", searchHandler.Search)
	mux.HandleFunc("GET /health", searchHandler.Health)

	// CORS middleware so the Next.js frontend (port 3000) can call us
	server := &http.Server{
		Addr:    ":" + port,
		Handler: corsMiddleware(mux),
	}

	log.Printf("[*] Go API Gateway listening on :%s", port)
	log.Printf("[*] Endpoints: GET /search?q=<query>&limit=<n>  |  GET /health")
	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("[FATAL] Server error: %v", err)
	}
}

// corsMiddleware adds CORS headers so the Next.js frontend can reach the gateway.
func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
