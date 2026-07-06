"""
NLP Query Parser - Extracts intent, keywords, and filters from natural language queries.
Uses spaCy (en_core_web_sm) for linguistic analysis — fully open-source, no API key needed.
Falls back gracefully to pure regex rules if spaCy is unavailable.
"""
import json
import re
from typing import Tuple, List
from datetime import datetime, timedelta


class NLPQueryParser:
    """Parses natural language queries to extract filters, intent text, and keywords."""

    # Date patterns  (regex → canonical filter key)
    DATE_PATTERNS = [
        (r"\blast\s+week\b",  "last_week"),
        (r"\blast\s+month\b", "last_month"),
        (r"\blast\s+year\b",  "last_year"),
        (r"\byesterday\b",    "yesterday"),
        (r"\btoday\b",        "today"),
        (r"\bfrom\s+(\w+)\b", "from_month"),
        (r"\bin\s+(\w+)\b",   "in_month"),
    ]

    # Size patterns
    SIZE_PATTERNS = [
        (r"greater\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_gt"),
        (r"bigger\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)",  "size_gt"),
        (r"larger\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)",  "size_gt"),
        (r">\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)",              "size_gt"),
        (r"smaller\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_lt"),
        (r"less\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)",    "size_lt"),
        (r"<\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)",              "size_lt"),
    ]

    # Explicit known extensions (matched with or without trailing 's' for plural)
    KNOWN_EXTENSIONS = [
        "png", "jpg", "jpeg", "gif", "svg", "webp",
        "pdf", "csv", "txt", "rtf", "zip", "rar", "tar", "gz", "7z",
        "docx", "xlsx", "pptx", "ppt", "xls",
        "mp3", "wav", "mp4", "mkv", "avi", "mov"
    ]

    # General file type patterns
    TYPE_PATTERNS = [
        (r"\b(images?|pictures?|photos?)\b",               "image"),
        (r"\b(videos?|movies?)\b",                         "video"),
        (r"\b(documents?|docs?|word\s+files?)\b",          "document"),
        (r"\b(audio|music|songs?)\b",                      "audio"),
        (r"\b(archives?|compressed)\b",                    "archive"),
        (r"\b(powerpoints?|slides?|presentations?)\b",     "presentation"),
        (r"\b(excel|spreadsheets?)\b",                     "spreadsheet"),
    ]

    # Conversational filler to strip out entirely
    CONVERSATIONAL_FILLER = [
        r"\b(show|get|find|search|give|bring)\s+(me|us)\b",
        r"\b(get|find|show|search)\b",
        r"\b(files?|documents?|objects?)\s+(related\s+to|about|on|for)\b",
        r"\b(related\s+to|about|on|for)\b"
    ]

    # Tokens to strip before building the intent / keyword string
    STOPWORDS = {
        "the", "a", "an", "all", "some", "any", "every", "each",
        "i", "me", "my", "we", "our", "you", "your",
        "find", "get", "show", "display", "list", "retrieve",
        "files", "file", "documents", "document", "objects", "object",
        "that", "which", "what", "where", "when", "how", "why",
        "can", "could", "would", "should", "may", "might", "must",
        "want", "need", "like", "love", "hate",
        "please", "kindly", "thanks", "thank",
    }

    # spaCy POS tags whose lemmas we keep as keywords
    _KEYWORD_POS = {"NOUN", "PROPN", "ADJ", "NUM"}

    def __init__(self):
        self.nlp = self._load_spacy()

    # ------------------------------------------------------------------
    # Public API  (unchanged from the original — search_worker.py is safe)
    # ------------------------------------------------------------------

    def parse(self, query: str) -> Tuple[str, List[str], List[str]]:
        """
        Parse a natural language query.

        Args:
            query: The raw user query string.

        Returns:
            Tuple of (intent_text, keywords, filters_list)
        """
        if not query or not query.strip():
            return "", [], []

        query = query.strip()

        if self.nlp:
            return self._spacy_parse(query)

        # spaCy unavailable — fall back to pure regex
        return self._rule_based_parse(query)

    def get_date_range(self, filter_str: str) -> Tuple[datetime, datetime]:
        """Convert a canonical date filter key to a datetime range."""
        now = datetime.now()
        mapping = {
            "last_week":  (now - timedelta(days=7),   now),
            "last_month": (now - timedelta(days=30),  now),
            "last_year":  (now - timedelta(days=365), now),
            "yesterday":  (now - timedelta(days=1),   now),
            "today":      (now - timedelta(hours=24), now),
        }
        return mapping.get(filter_str, (now - timedelta(days=7), now))

    # ------------------------------------------------------------------
    # spaCy-based parsing
    # ------------------------------------------------------------------

    def _spacy_parse(self, query: str) -> Tuple[str, List[str], List[str]]:
        """Use spaCy for linguistic analysis, then layer regex filters on top."""
        # 1. Extract structured filters with regex (same logic as before)
        filters: List[str] = []
        filters.extend(self._extract_date_filters(query))
        filters.extend(self._extract_size_filters(query))
        filters.extend(self._extract_type_filters(query))

        # 2. Strip filter-matched tokens and conversational filler before NLP
        stripped = self._strip_filter_text(query)
        stripped = self._strip_conversational_filler(stripped)

        # 3. Run spaCy on the stripped text
        doc = self.nlp(stripped)

        # 4. Collect lemmatised keywords from meaningful POS tags
        keywords: List[str] = []
        for token in doc:
            if (
                token.pos_ in self._KEYWORD_POS
                and not token.is_stop
                and not token.is_punct
                and not token.is_space
                and len(token.text) > 1
                and token.lemma_.lower() not in self.STOPWORDS
            ):
                keywords.append(token.lemma_.lower())

        # 5. Also pick up DATE / ORG / PRODUCT named entities as extra keywords
        for ent in doc.ents:
            if ent.label_ in {"ORG", "PRODUCT", "WORK_OF_ART", "FAC"}:
                keywords.append(ent.text.lower())

        # Deduplicate while preserving order
        seen = set()
        unique_keywords: List[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        intent_text = " ".join(unique_keywords)
        return intent_text, unique_keywords, filters

    # ------------------------------------------------------------------
    # Regex filter extraction  (shared by both spaCy and rule-based paths)
    # ------------------------------------------------------------------

    def _extract_date_filters(self, query: str) -> List[str]:
        filters = []
        exact_date_pattern = r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
        exact_match = re.search(exact_date_pattern, query, re.IGNORECASE)
        if exact_match:
            month = exact_match.group(1).lower()
            day = exact_match.group(2)
            year = exact_match.group(3) or str(datetime.now().year)
            filters.append(f"exact_date:{month}_{day}_{year}")

        for pattern, filter_type in self.DATE_PATTERNS:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                if filter_type in ("from_month", "in_month") and match.group(1):
                    filters.append(f"month:{match.group(1).lower()}")
                else:
                    filters.append(f"date:{filter_type}")
        return filters

    def _extract_size_filters(self, query: str) -> List[str]:
        filters = []
        for pattern, filter_type in self.SIZE_PATTERNS:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                filters.append(f"size:{filter_type}:{match.group(1)}{match.group(2).upper()}")
        return filters

    def _extract_type_filters(self, query: str) -> List[str]:
        filters = []
        # General types
        for pattern, type_name in self.TYPE_PATTERNS:
            if re.search(pattern, query, re.IGNORECASE):
                filters.append(f"type:{type_name}")
                
        # Known extensions without dot (e.g. "pngs", "docx")
        ext_pattern = r"\b(" + "|".join(self.KNOWN_EXTENSIONS) + r")s?\b"
        for match in re.finditer(ext_pattern, query, re.IGNORECASE):
            filters.append(f"extension:{match.group(1).lower()}")
            
        # Catch-all for extensions with dot (e.g. ".pdf")
        for match in re.finditer(r"\.(\w+)\b", query):
            filters.append(f"extension:{match.group(1).lower()}")
            
        return list(set(filters))

    def _strip_filter_text(self, query: str) -> str:
        """Remove date/size/type pattern text before passing to spaCy."""
        text = query
        exact_date_pattern = r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
        text = re.sub(exact_date_pattern, " ", text, flags=re.IGNORECASE)
        for pattern, _ in self.DATE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        for pattern, _ in self.SIZE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        for pattern, _ in self.TYPE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            
        # Strip known extensions and plurals
        ext_pattern = r"\b(" + "|".join(self.KNOWN_EXTENSIONS) + r")s?\b"
        text = re.sub(ext_pattern, " ", text, flags=re.IGNORECASE)
        
        # Strip dot extensions
        text = re.sub(r"\.\w+\b", " ", text, flags=re.IGNORECASE)
        
        return re.sub(r"\s+", " ", text).strip()

    def _strip_conversational_filler(self, text: str) -> str:
        """Remove conversational phrasing like 'show me files related to'."""
        for pattern in self.CONVERSATIONAL_FILLER:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    # ------------------------------------------------------------------
    # Pure rule-based fallback (used when spaCy is not installed)
    # ------------------------------------------------------------------

    def _rule_based_parse(self, query: str) -> Tuple[str, List[str], List[str]]:
        query_lower = query.lower().strip()
        filters: List[str] = []
        filters.extend(self._extract_date_filters(query_lower))
        filters.extend(self._extract_size_filters(query_lower))
        filters.extend(self._extract_type_filters(query_lower))
        intent_text = self._extract_intent_text_regex(query_lower)
        keywords = [w for w in intent_text.split() if len(w) > 2]
        return intent_text, keywords, filters

    def _extract_intent_text_regex(self, query: str) -> str:
        text = self._strip_filter_text(query)
        text = self._strip_conversational_filler(text)
        words = text.split()
        meaningful = [w for w in words if w.lower() not in self.STOPWORDS]
        return " ".join(meaningful) if meaningful else ""

    # ------------------------------------------------------------------
    # spaCy loader
    # ------------------------------------------------------------------

    @staticmethod
    def _load_spacy():
        """Load spaCy en_core_web_sm model. Returns None if not available."""
        try:
            import spacy
            try:
                nlp = spacy.load("en_core_web_sm")
                print("[+] spaCy NLP parser loaded (en_core_web_sm)")
                return nlp
            except OSError:
                print("[!] spaCy model 'en_core_web_sm' not found. "
                      "Run: python -m spacy download en_core_web_sm")
                return None
        except ImportError:
            print("[!] spaCy not installed. Using rule-based NLP fallback.")
            return None