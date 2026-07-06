"use client";

import { useState, useRef, useEffect, useCallback } from "react";

const API_BASE = "";

// ── Clipboard helper (works on HTTP too) ───────────────────────────────────
function copyToClipboard(text) {
  if (navigator?.clipboard?.writeText) {
    return navigator.clipboard.writeText(text);
  }
  // Fallback: create a temporary textarea
  return new Promise((resolve, reject) => {
    const el = document.createElement("textarea");
    el.value = text;
    el.setAttribute("readonly", "");
    el.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0;";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    ok ? resolve() : reject(new Error("execCommand copy failed"));
  });
}

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

  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(extension)) {
    return (
      <svg className="image-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
      </svg>
    );
  }

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

  return (
    <svg className="generic-icon icon-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <polyline points="13 2 13 9 20 9" />
    </svg>
  );
}

/* ── Copy Icon ── */
function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

/* ── Edit Icon ── */
function EditIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

/* ── Download Icon ── */
function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

/* ── Chevron Icon ── */
function ChevronIcon({ open }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.25s ease" }}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

/* ── AI Summary Panel ── */
function SummaryPanel({ results, query, intentText }) {
  const [open, setOpen] = useState(true);

  if (!results || results.length === 0) return null;

  const top = results[0];
  const fileName = top.object_name?.split("/").pop() || top.object_name || "—";

  const pct = (v) => `${Math.round((v || 0) * 100)}%`;

  const typeCounts = results.reduce((acc, r) => {
    const ext = (r.extension || "unknown").toLowerCase();
    acc[ext] = (acc[ext] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className={`summary-panel ${open ? "summary-open" : "summary-closed"}`}>
      <button className="summary-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="summary-title">
          <svg className="summary-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          AI Summary
        </span>
        <span className="summary-chevron"><ChevronIcon open={open} /></span>
      </button>

      {open && (
        <div className="summary-body">

          {/* AI-generated summary card */}
          {top.snippet && (
            <div className="summary-card">
              <div className="summary-card-label">Document Summary</div>
              <p className="summary-card-text">{top.snippet}</p>
            </div>
          )}

          {/* Top result compact info */}
          <div className="summary-file-row">
            <FileIcon ext={top.extension} />
            <span className="summary-filename" title={top.object_name}>{fileName}</span>
          </div>

          {/* 2×2 metadata grid */}
          <div className="summary-meta-grid">
            <div className="meta-grid-item">
              <span className="meta-grid-label">Bucket</span>
              <span className="meta-grid-value">{top.bucket || "—"}</span>
            </div>
            <div className="meta-grid-item">
              <span className="meta-grid-label">Type</span>
              <span className="meta-grid-value">{top.extension ? `.${top.extension}` : "—"}</span>
            </div>
            <div className="meta-grid-item">
              <span className="meta-grid-label">Size</span>
              <span className="meta-grid-value">{formatBytes(top.size_bytes)}</span>
            </div>
            <div className="meta-grid-item">
              <span className="meta-grid-label">Uploaded</span>
              <span className="meta-grid-value">{formatDate(top.last_modified)}</span>
            </div>
          </div>

          {/* Inline score badges */}
          <div className="score-badges">
            <span className="score-badge score-semantic" title="Semantic similarity score">
              S {pct(top.semantic_score)}
            </span>
            <span className="score-badge score-keyword" title="Keyword match score">
              K {pct(top.keyword_score)}
            </span>
            <span className="score-badge score-combined" title="Final combined score">
              ★ {pct(top.combined_score)}
            </span>
            <span className="score-badge score-count">
              {results.length} match{results.length !== 1 ? "es" : ""}
            </span>
            {Object.entries(typeCounts).map(([ext, count]) => (
              <span key={ext} className="score-badge score-type">.{ext} {count}</span>
            ))}
          </div>

        </div>
      )}
    </div>
  );
}


/* ── User Query Bubble with Copy & Edit ── */
function UserBubble({ text, onEdit }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await copyToClipboard(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard denied — silently ignore */
    }
  }, [text]);

  const handleEdit = useCallback(() => {
    onEdit(text);
  }, [text, onEdit]);

  return (
    <div className="user-bubble-wrap">
      <div className="bubble user-bubble">
        <svg className="query-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        {text}
      </div>
      <div className="bubble-actions">
        {/* Copy button */}
        <div className="bubble-action-wrap">
          <button
            className="bubble-action-btn"
            onClick={handleCopy}
            aria-label="Copy query"
            title="Copy query"
          >
            <CopyIcon />
          </button>
          {copied && <span className="tooltip-copied">Copied!</span>}
        </div>
        {/* Edit button */}
        <button
          className="bubble-action-btn"
          onClick={handleEdit}
          aria-label="Edit query"
          title="Edit query"
        >
          <EditIcon />
        </button>
      </div>
    </div>
  );
}

/* ── Preview Icon ── */
function PreviewIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/* ── Preview Modal ── */
function PreviewModal({ item, onClose }) {
  const ext = (item?.extension || "").toLowerCase();
  const isImage = ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext);
  const isPdf   = ext === "pdf";
  // previewUrl → inline (no dl param) so <img> and <iframe> render in-browser
  // downloadUrl → attachment (dl=1) so browser triggers Save As dialog
  const baseUrl = item
    ? `/api/download?bucket=${encodeURIComponent(item.bucket)}&key=${encodeURIComponent(item.object_name)}`
    : "";
  const previewUrl  = baseUrl;
  const downloadUrl = baseUrl ? `${baseUrl}&dl=1` : "";
  const fileName = item?.object_name?.split("/").pop() || item?.object_name || "";

  // Close on backdrop click
  const handleBackdrop = (e) => { if (e.target === e.currentTarget) onClose(); };

  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!item) return null;

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal-box">
        {/* Header */}
        <div className="modal-header">
          <div className="modal-title-row">
            <FileIcon ext={item.extension} />
            <span className="modal-filename">{fileName}</span>
          </div>
          <div className="modal-actions">
            <a
              className="modal-download-btn"
              href={downloadUrl}
              download={fileName}
              aria-label="Download"
              title="Download"
            >
              <DownloadIcon />
              <span>Download</span>
            </a>
            <button className="modal-close-btn" onClick={onClose} aria-label="Close">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="modal-body">
          {isImage && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt={fileName} className="modal-image" />
          )}
          {isPdf && (
            <iframe src={previewUrl} className="modal-iframe" title={fileName} />
          )}
          {!isImage && !isPdf && (
            <div className="modal-meta">
              <div className="modal-meta-icon"><FileIcon ext={item.extension} /></div>
              <p className="modal-meta-name">{fileName}</p>
              <div className="modal-meta-pills">
                <span className="meta-pill bucket">{item.bucket}</span>
                <span className="meta-pill size">{formatBytes(item.size_bytes)}</span>
                <span className="meta-pill date">{formatDate(item.last_modified)}</span>
                {item.extension && <span className="meta-pill ext">.{item.extension}</span>}
              </div>
              <p className="modal-meta-hint">Preview is not available for this file type.<br/>Use the Download button above.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── File-type CSS class helper ── */
function getTypeClass(ext) {
  const e = (ext || "").toLowerCase();
  if (e === "pdf") return "type-pdf";
  if (["doc", "docx", "txt", "pages"].includes(e)) return "type-doc";
  if (["xls", "xlsx", "csv", "numbers"].includes(e)) return "type-xls";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(e)) return "type-img";
  if (["zip", "tar", "gz", "rar", "7z"].includes(e)) return "type-zip";
  return "";
}

/* ── Result card with Preview + Download ── */
function ResultCard({ item, onPreview }) {
  const [downloading, setDownloading] = useState(false);
  const typeClass = getTypeClass(item.extension);
  const fileName = item.object_name.split("/").pop() || item.object_name;

  const handleDownload = useCallback((e) => {
    e.stopPropagation();
    if (downloading) return;
    setDownloading(true);
    const params = new URLSearchParams({ bucket: item.bucket, key: item.object_name, dl: "1" });
    const link = document.createElement("a");
    link.href = `/api/download?${params.toString()}`;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => setDownloading(false), 1500);
  }, [item, fileName, downloading]);

  return (
    <div className={`result-card ${typeClass}`} onClick={() => onPreview(item)}>
      <div className="card-icon">
        <FileIcon ext={item.extension} />
      </div>
      <div className="card-body">
        <div className="card-name" title={item.object_name}>{fileName}</div>
        <div className="card-meta">
          <span className="meta-pill bucket">{item.bucket}</span>
          <span className="meta-pill size">{formatBytes(item.size_bytes)}</span>
          <span className="meta-pill date">{formatDate(item.last_modified)}</span>
          {item.extension && <span className="meta-pill ext">.{item.extension}</span>}
        </div>
      </div>
      <div className="card-action-group" onClick={(e) => e.stopPropagation()}>
        <button
          className="preview-btn"
          onClick={(e) => { e.stopPropagation(); onPreview(item); }}
          aria-label="Preview file"
          title="Preview file"
        >
          <PreviewIcon />
        </button>
        <button
          className={`download-btn ${downloading ? "downloading" : ""}`}
          onClick={handleDownload}
          aria-label="Download file"
          title="Download file"
          disabled={downloading}
        >
          {downloading ? (
            <svg className="spin-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
            </svg>
          ) : (
            <DownloadIcon />
          )}
        </button>
      </div>
    </div>
  );
}

/* ── Main Page ── */
export default function Home() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [previewItem, setPreviewItem] = useState(null);
  const [recentQueries, setRecentQueries] = useState([]);
  const chatEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    try {
      const stored = localStorage.getItem("hpe_recent_queries");
      if (stored) {
        setRecentQueries(JSON.parse(stored));
      }
    } catch (e) {
      console.error("Failed to load recent queries", e);
    }
  }, []);

  const addRecentQuery = (q) => {
    setRecentQueries(prev => {
      const updated = [q, ...prev.filter(x => x !== q)].slice(0, 10);
      try {
        localStorage.setItem("hpe_recent_queries", JSON.stringify(updated));
      } catch(e) {}
      return updated;
    });
  };

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const sendSearch = async (q) => {
    const trimmed = q.trim();
    if (!trimmed || loading) return;

    addRecentQuery(trimmed);

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
        {
          role: "assistant",
          results: data.results || [],
          query: trimmed,
          intentText: data.intent_text || "",
        },
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

  /* Edit handler: pre-fill input and focus */
  const handleEditQuery = useCallback((text) => {
    setQuery(text);
    setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(text.length, text.length);
    }, 50);
  }, []);

  const isFirstSearch = messages.length === 0;

  /* Find the latest assistant message with results for summary panel */
  const latestAssistant = [...messages].reverse().find(
    (m) => m.role === "assistant" && m.results && m.results.length > 0
  );

  const handlePreview = useCallback((item) => setPreviewItem(item), []);
  const handleClosePreview = useCallback(() => setPreviewItem(null), []);

  // ── Live polling: silently re-fetch latest query every 30s ─────────────────
  const [pendingUpdate, setPendingUpdate] = useState(null); // new results waiting
  const [showToast, setShowToast] = useState(false);
  const pollingRef = useRef(null);

  useEffect(() => {
    // Only poll when there is an active search result
    if (!latestAssistant) {
      if (pollingRef.current) clearInterval(pollingRef.current);
      return;
    }
    const lastQuery = latestAssistant.query;
    const currentNames = (latestAssistant.results || []).map((r) => r.object_name).sort().join(",");

    pollingRef.current = setInterval(async () => {
      try {
        const baseUrl = API_BASE ? `${API_BASE}/search` : "/api/search";
        const res = await fetch(`${baseUrl}?q=${encodeURIComponent(lastQuery)}&limit=8`);
        if (!res.ok) return;
        const data = await res.json();
        const newNames = (data.results || []).map((r) => r.object_name).sort().join(",");
        // Show toast only if results actually changed
        if (newNames !== currentNames) {
          setPendingUpdate(data);
          setShowToast(true);
          clearInterval(pollingRef.current);
        }
      } catch (_) { /* silently ignore poll errors */ }
    }, 30000); // poll every 30 seconds

    return () => clearInterval(pollingRef.current);
  }, [latestAssistant]);

  // Apply the pending update when user clicks Refresh on toast
  const applyUpdate = useCallback(() => {
    if (!pendingUpdate || !latestAssistant) return;
    setMessages((prev) =>
      prev.map((m, i) =>
        i === prev.lastIndexOf(latestAssistant)
          ? { ...m, results: pendingUpdate.results, intentText: pendingUpdate.intent_text }
          : m
      )
    );
    setPendingUpdate(null);
    setShowToast(false);
  }, [pendingUpdate, latestAssistant]);

  const dismissToast = useCallback(() => {
    setShowToast(false);
  }, []);


  return (
    <div className="app">
      {/* Preview modal */}
      {previewItem && <PreviewModal item={previewItem} onClose={handleClosePreview} />}

      {/* Live update toast */}
      {showToast && (
        <div className="live-toast" role="alert">
          <span className="live-toast-dot" />
          <span className="live-toast-text">New results available</span>
          <button className="live-toast-btn" onClick={applyUpdate}>Refresh</button>
          <button className="live-toast-dismiss" onClick={dismissToast} aria-label="Dismiss">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      )}

      {/* Background mesh */}
      <div className="bg-mesh" />

      {/* Header */}
      <header className="header">
        <div className="logo" onClick={() => setMessages([])}>
          <span className="logo-hpe">Object</span>
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
              <span className="hero-hpe">Object</span>
              <span className="hero-search">Search</span>
            </div>
            <p className="hero-sub">Search your object storage with natural language.</p>
            {recentQueries.length > 0 ? (
              <div className="hero-recent">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "6px", marginBottom: "16px", color: "var(--text-secondary)", fontSize: "0.9rem", fontWeight: 500 }}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{width: 16, height: 16}}>
                    <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
                  </svg>
                  <span>Recent Searches</span>
                </div>
                <div className="suggestion-chips">
                  {recentQueries.slice(0, 5).map((s) => (
                    <button key={s} className="chip" onClick={() => sendSearch(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="suggestion-chips">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="chip" onClick={() => sendSearch(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Google-style per-turn layout */}
        {!isFirstSearch && (
          <div className="chat-container">
            {messages.map((msg, i) => (
              msg.role === "user" ? (
                /* ── Query row: full-width centered bubble ── */
                <div key={i} className="query-turn">
                  <UserBubble text={msg.text} onEdit={handleEditQuery} />
                </div>
              ) : (
                /* ── Response row: left results + right summary ── */
                <div key={i} className="response-turn">
                  {/* Left: results */}
                  <div className="response-main">
                    <div className="assistant-header">
                      <span className="assistant-badge">Object Search</span>
                      {msg.results && (
                        <span className="result-count">
                          {/* Pulsing live dot on the latest result row */}
                          {i === messages.lastIndexOf(latestAssistant) && (
                            <span className="live-dot" title="Live — auto-refreshes every 30s" />
                          )}
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
                          <ResultCard key={j} item={item} onPreview={handlePreview} />
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Right: per-turn AI Summary */}
                  {msg.results && msg.results.length > 0 && (
                    <aside className="response-sidebar">
                      <SummaryPanel
                        results={msg.results}
                        query={msg.query}
                        intentText={msg.intentText}
                      />
                    </aside>
                  )}
                </div>
              )
            ))}

            {loading && (
              <div className="response-turn">
                <div className="response-main">
                  <div className="assistant-bubble typing">
                    <span /><span /><span />
                  </div>
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
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            )}
            {/* Mic / Send button (right) */}
            <button type="submit" className="send-btn" disabled={loading || !query.trim()}>
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            </button>
          </div>

          {/* Suggestions dropdown */}
          {(() => {
            const lowerQuery = query.toLowerCase().trim();
            let suggestions = [];
            if (lowerQuery) {
              const matchingRecents = recentQueries.filter(q => q.toLowerCase().includes(lowerQuery));
              const matchingStatic = SUGGESTIONS.filter(q => q.toLowerCase().includes(lowerQuery) && !matchingRecents.includes(q));
              suggestions = [...matchingRecents, ...matchingStatic];
            } else {
              suggestions = recentQueries;
            }
            suggestions = suggestions.slice(0, 5);

            if (showSuggestions && suggestions.length > 0) {
              return (
                <div className="dropdown">
                  {suggestions.map((s) => (
                    <div key={s} className="dropdown-item" onMouseDown={() => sendSearch(s)}>
                      <svg className="dropdown-history-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
                      </svg>
                      <span>{s}</span>
                    </div>
                  ))}
                </div>
              );
            }
            return null;
          })()}
        </form>
        <p className="input-hint">Try: &ldquo;quarterly report pdf&rdquo; or &ldquo;images from last week&rdquo;</p>
      </div>
    </div>
  );
}
