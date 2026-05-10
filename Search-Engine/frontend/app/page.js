"use client";

import { useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

const quickTags = ["pdf", "images", "reports", "last week", "size > 10MB"];
const recentSearches = [
  "invoice pdf",
  "marketing deck",
  "images from april",
  "contracts 2024",
];

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");

  const subtitle = useMemo(() => {
    return "Search your object storage with natural language.";
  }, []);

  const onSearch = async (event) => {
    event.preventDefault();
    if (!query.trim()) {
      return;
    }

    setStatus("loading");
    setError("");

    try {
      const baseUrl = API_BASE ? `${API_BASE}/search` : "/api/search";
      const response = await fetch(
        `${baseUrl}?q=${encodeURIComponent(query.trim())}`,
      );
      if (!response.ok) {
        throw new Error("Search service unavailable");
      }
      const data = await response.json();
      setResults(data.results || []);
      setStatus("done");
    } catch (err) {
      setError(err.message || "Search failed");
      setStatus("error");
    }
  };

  return (
    <main className="page">
      <div className="glow" />
      <section className="hero">
        <div className="hero-content">
          <span className="eyebrow">Enterprise Search</span>
          <h1>
            <span className="hpe-mark">HPE</span> Search
          </h1>
          <p>{subtitle}</p>
          <form className="search" onSubmit={onSearch}>
            <input
              type="search"
              placeholder="Try: quarterly report pdf, images last week..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button type="submit" disabled={status === "loading"}>
              {status === "loading" ? "Searching..." : "Search"}
            </button>
          </form>
          <div className="tags">
            {quickTags.map((tag) => (
              <button key={tag} type="button" onClick={() => setQuery(tag)}>
                {tag}
              </button>
            ))}
          </div>
          <div className="recent">
            <h4>Recent searches</h4>
            <div className="recent-list">
              {recentSearches.map((item) => (
                <button key={item} type="button" onClick={() => setQuery(item)}>
                  {item}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="results">
        <div className="results-header">
          <h2>Results</h2>
        </div>
        {status === "idle" && (
          <div className="empty">Start with a query to see ranked results.</div>
        )}
        {status === "error" && <div className="error">{error}</div>}
        {status === "done" && results.length === 0 && (
          <div className="empty">No matches yet. Try a different query.</div>
        )}
        <div className="grid">
          {results.map((item) => (
            <article className="result-card" key={item.object_name}>
              <div>
                <h3>{item.object_name}</h3>
                <p>{item.bucket}</p>
              </div>
              <div className="meta">
                <span>{item.extension}</span>
                <span>{item.size_bytes} bytes</span>
                <span>{item.last_modified}</span>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
