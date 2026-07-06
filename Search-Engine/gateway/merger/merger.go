// Package merger provides Top-K result merging and deduplication logic.
package merger

import (
	"sort"

	pb "github.com/hpe/search-engine/gateway/proto"
)

const defaultLimit = 10
const maxLimit = 50

// Result is the gateway's canonical result type (JSON-serialisable).
type Result struct {
	ID            string  `json:"id"`
	ObjectName    string  `json:"object_name"`
	Bucket        string  `json:"bucket"`
	SizeBytes     int64   `json:"size_bytes"`
	ContentType   string  `json:"content_type"`
	Extension     string  `json:"extension"`
	LastModified  string  `json:"last_modified"`
	SemanticScore float32 `json:"semantic_score"`
	KeywordScore  float32 `json:"keyword_score"`
	CombinedScore float32 `json:"combined_score"`
	Snippet       string  `json:"snippet"`
}

// SearchResponse is the top-level JSON body returned to the frontend.
type SearchResponse struct {
	Results           []Result `json:"results"`
	TotalHits         int      `json:"total_hits"`
	IntentText        string   `json:"intent_text"`
	ExtractedKeywords []string `json:"extracted_keywords"`
	AppliedFilters    []string `json:"applied_filters"`
}

// Merge converts a proto SearchQueryResponse into a gateway SearchResponse,
// deduplicates by ID, sorts by combined_score desc, and trims to limit.
//
// NOTE for Role 3: Redis caching should wrap this function's output.
// The caller (handlers/search.go) has a clearly marked integration point.
func Merge(proto *pb.SearchQueryResponse, limit int) SearchResponse {
	if limit <= 0 {
		limit = defaultLimit
	}
	if limit > maxLimit {
		limit = maxLimit
	}

	// Convert proto results → gateway results, deduplicate by Bucket + ObjectName
	seen := make(map[string]struct{}, len(proto.Results))
	unique := make([]Result, 0, len(proto.Results))
	for _, r := range proto.Results {
		fileKey := r.Bucket + "/" + r.ObjectName
		if _, dup := seen[fileKey]; dup {
			continue
		}
		seen[fileKey] = struct{}{}
		unique = append(unique, Result{
			ID:            r.Id,
			ObjectName:    r.ObjectName,
			Bucket:        r.Bucket,
			SizeBytes:     r.SizeBytes,
			ContentType:   r.ContentType,
			Extension:     r.Extension,
			LastModified:  r.LastModified,
			SemanticScore: r.SemanticScore,
			KeywordScore:  r.KeywordScore,
			CombinedScore: r.CombinedScore,
			Snippet:       r.Snippet,
		})
	}

	// Sort by combined_score descending
	sort.Slice(unique, func(i, j int) bool {
		return unique[i].CombinedScore > unique[j].CombinedScore
	})

	// Trim to limit
	if len(unique) > limit {
		unique = unique[:limit]
	}

	return SearchResponse{
		Results:           unique,
		TotalHits:         int(proto.TotalHits),
		IntentText:        proto.IntentText,
		ExtractedKeywords: proto.ExtractedKeywords,
		AppliedFilters:    proto.AppliedFilters,
	}
}
