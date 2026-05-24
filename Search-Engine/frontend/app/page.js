"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

const SUGGESTIONS = [
  "invoice pdf",
  "images from last week",
  "contracts 2024",
  "marketing deck pptx",
  "reports bigger than 10MB",
];

function formatBytes(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatDate(d) {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

function FileIcon({ ext }) {
  const extension = (ext || "").toLowerCase();
  
  // Document Files (PDF, DOC, DOCX, TXT)
  if (["pdf", "doc", "docx", "txt", "pages"].includes(extension)) {
    return (
      <svg className="doc-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <polyline points="10 9 9 9 8 9" />
      </svg>
    );
  }
  
  // Image Files
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(extension)) {
    return (
      <svg className="image-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
      </svg>
    );
  }
  
  // Spreadsheets
  if (["xls", "xlsx", "csv", "numbers"].includes(extension)) {
    return (
      <svg className="sheet-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="9" y1="3" x2="9" y2="21" />
        <line x1="15" y1="3" x2="15" y2="21" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="3" y1="15" x2="21" y2="15" />
      </svg>
    );
  }
  
  // Archive files
  if (["zip", "tar", "gz", "rar", "7z"].includes(extension)) {
    return (
      <svg className="zip-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="12" y1="3" x2="12" y2="21" />
        <path d="M12 8h3" />
        <path d="M9 12h3" />
        <path d="M12 16h3" />
      </svg>
    );
  }
  
  // Default Generic File Icon
  return (
    <svg className="generic-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <polyline points="13 2 13 9 20 9" />
    </svg>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const chatEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const sendSearch = async (q) => {
    const trimmed = q.trim();
    if (!trimmed || loading) return;

    // Add user query message
    setMessages((prev) => [...prev, { role: "user", text: trimmed }]);
    setQuery("");
    setLoading(true);
    setShowSuggestions(false);

    try {
      const baseUrl = API_BASE ? `${API_BASE}/search` : "/api/search";
      const res = await fetch(`${baseUrl}?q=${encodeURIComponent(trimmed)}&limit=8`);
      if (!res.ok) throw new Error("Search service unavailable");
      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", results: data.results || [], query: trimmed },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", error: err.message || "Search failed", query: trimmed },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    sendSearch(query);
  };

  const isFirstSearch = messages.length === 0;

  return (
    <div className="app">
      {/* Background mesh */}
      <div className="bg-mesh" />

      {/* Header */}
      <header className="header">
        <div className="logo" onClick={() => setMessages([])}>
          <span className="logo-hpe">HPE</span>
          <span className="logo-search">Search</span>
        </div>
        {!isFirstSearch && (
          <div className="header-subtitle">Object Storage · Natural Language</div>
        )}
      </header>

      {/* Main content */}
      <main className={`main ${isFirstSearch ? "centered" : "chat-mode"}`}>
        {isFirstSearch && (
          <div className="hero">
            <div className="hero-logo">
              <span className="hero-hpe">HPE</span>
              <span className="hero-search">Search</span>
            </div>
            <p className="hero-sub">Search your object storage with natural language.</p>
            <div className="suggestion-chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => sendSearch(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Chat thread */}
        {!isFirstSearch && (
          <div className="chat-thread">
            {messages.map((msg, i) => (
              <div key={i} className={`message-row ${msg.role}`}>
                {msg.role === "user" ? (
                  <div className="bubble user-bubble">
                    <svg className="query-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                    </svg>
                    {msg.text}
                  </div>
                ) : (
                  <div className="assistant-bubble">
                    <div className="assistant-header">
                      <span className="assistant-badge">HPE Search</span>
                      {msg.results && (
                        <span className="result-count">
                          {msg.results.length} result{msg.results.length !== 1 ? "s" : ""} for &ldquo;{msg.query}&rdquo;
                        </span>
                      )}
                    </div>

                    {msg.error && (
                      <div className="error-msg">
                        <svg className="error-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                        </svg>
                        <span>{msg.error}</span>
                      </div>
                    )}

                    {msg.results && msg.results.length === 0 && (
                      <div className="no-results">
                        No files matched <strong>&ldquo;{msg.query}&rdquo;</strong>. Try different keywords or check extension filters.
                      </div>
                    )}

                    {msg.results && msg.results.length > 0 && (
                      <div className="result-grid">
                        {msg.results.map((item, j) => (
                          <div key={j} className="result-card">
                            <div className="card-icon">
                              <FileIcon ext={item.extension} />
                            </div>
                            <div className="card-body">
                              <div className="card-name">{item.object_name}</div>
                              <div className="card-meta">
                                <span className="meta-pill bucket">{item.bucket}</span>
                                <span className="meta-pill size">{formatBytes(item.size_bytes)}</span>
                                <span className="meta-pill date">{formatDate(item.last_modified)}</span>
                                {item.extension && <span className="meta-pill ext">.{item.extension}</span>}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="message-row assistant">
                <div className="assistant-bubble typing">
                  <span /><span /><span />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
        )}
      </main>

      {/* Search input — always at bottom */}
      <div className={`input-bar ${isFirstSearch ? "input-center" : "input-bottom"}`}>
        <form className="search-form" onSubmit={handleSubmit}>
          <div className="input-wrap">
            <svg className="input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              ref={inputRef}
              className="search-input"
              type="text"
              placeholder="Ask anything about your files…"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setShowSuggestions(true); }}
              onFocus={() => setShowSuggestions(true)}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              autoComplete="off"
            />
            {query && (
              <button type="button" className="clear-btn" onClick={() => setQuery("")}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{width: 14, height: 14}}>
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            )}
            <button type="submit" className="send-btn" disabled={loading || !query.trim()}>
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            </button>
          </div>

          {/* Suggestions dropdown */}
          {showSuggestions && isFirstSearch && query.length > 0 && (
            <div className="dropdown">
              {SUGGESTIONS.filter((s) => s.includes(query.toLowerCase())).map((s) => (
                <div key={s} className="dropdown-item" onMouseDown={() => sendSearch(s)}>
                  <svg className="dropdown-history-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
                  </svg>
                  <span>{s}</span>
                </div>
              ))}
            </div>
          )}
        </form>
        <p className="input-hint">Try: &ldquo;quarterly report pdf&rdquo; or &ldquo;images from last week&rdquo;</p>
      </div>
    </div>
  );
}
