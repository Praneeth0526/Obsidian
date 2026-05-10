"""
NLP Query Parser - Extracts intent, keywords, and filters from natural language queries
Uses LangChain when available, with a rule-based fallback.
"""
import json
import os
import re
from typing import Tuple, List
from datetime import datetime, timedelta

from langchain_core.prompts import PromptTemplate


class NLPQueryParser:
    """Parses natural language queries to extract filters, intent text, and keywords."""

    # Date patterns
    DATE_PATTERNS = [
        (r"\blast\s+week\b", "last_week"),
        (r"\blast\s+month\b", "last_month"),
        (r"\blast\s+year\b", "last_year"),
        (r"\byesterday\b", "yesterday"),
        (r"\btoday\b", "today"),
        (r"\bfrom\s+(\w+)\b", "from_month"),
        (r"\bin\s+(\w+)\b", "in_month"),
    ]

    # Size patterns
    SIZE_PATTERNS = [
        (r"greater\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_gt"),
        (r"bigger\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_gt"),
        (r"larger\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_gt"),
        (r">\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_gt"),
        (r"smaller\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_lt"),
        (r"less\s+than\s+(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_lt"),
        (r"<\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", "size_lt"),
    ]

    # Type patterns
    TYPE_PATTERNS = [
        (r"\b(pdfs?)\b", "pdf"),
        (r"\b(images?|pictures?|photos?)\b", "image"),
        (r"\b(videos?|movies?)\b", "video"),
        (r"\b(documents?|docs?)\b", "document"),
        (r"\b(audio|music|songs?)\b", "audio"),
        (r"\b(archives?|zips?|compressed)\b", "archive"),
        (r"\.(\w+)\b", "extension"),
    ]

    # Common words to remove (stopwords)
    STOPWORDS = {
        "the",
        "a",
        "an",
        "all",
        "some",
        "any",
        "every",
        "each",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "find",
        "get",
        "show",
        "display",
        "list",
        "retrieve",
        "files",
        "file",
        "documents",
        "document",
        "objects",
        "object",
        "that",
        "which",
        "what",
        "where",
        "when",
        "how",
        "why",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "must",
        "want",
        "need",
        "like",
        "love",
        "hate",
        "please",
        "kindly",
        "thanks",
        "thank",
    }

    def __init__(self):
        self.llm = self._init_llm()
        self.prompt = PromptTemplate(
            template=(
                "You are a search query parser. Extract intent, keywords, and filters.\n"
                "Return JSON with keys: intent, keywords, filters.\n\n"
                "Query: {query}\n\n"
                "Filters must be strings like: date:last_week, size:size_gt:10MB, type:pdf, extension:pdf."
            ),
            input_variables=["query"],
        )

    def parse(self, query: str) -> Tuple[str, List[str], List[str]]:
        """
        Parse a natural language query.

        Args:
            query: The natural language query string

        Returns:
            Tuple of (intent_text, keywords, filters_list)
        """
        if not query or not query.strip():
            return "", [], []

        query = query.strip()

        if self.llm:
            try:
                response = (self.prompt | self.llm).invoke({"query": query})
                content = getattr(response, "content", str(response))
                parsed = self._extract_json(content)
                if parsed:
                    intent = parsed.get("intent", "")
                    keywords = parsed.get("keywords", []) or []
                    filters = parsed.get("filters", []) or []
                    return intent, [str(k) for k in keywords], [str(f) for f in filters]
            except Exception as exc:
                print(f"[!] LangChain parsing failed: {exc}. Falling back to rules.")

        return self._rule_based_parse(query)

    def _extract_date_filters(self, query: str) -> List[str]:
        """Extract date-related filters from query."""
        filters = []

        for pattern, filter_type in self.DATE_PATTERNS:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                if filter_type in ('from_month', 'in_month') and match.group(1):
                    month = match.group(1).lower()
                    filters.append(f"month:{month}")
                else:
                    filters.append(f"date:{filter_type}")

        return filters

    def _extract_size_filters(self, query: str) -> List[str]:
        """Extract size-related filters from query."""
        filters = []

        for pattern, filter_type in self.SIZE_PATTERNS:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                size_value = match.group(1)
                size_unit = match.group(2).upper()
                filters.append(f"size:{filter_type}:{size_value}{size_unit}")

        return filters

    def _extract_type_filters(self, query: str) -> List[str]:
        """Extract file type filters from query."""
        filters = []

        # First check explicit type keywords
        for pattern, type_name in self.TYPE_PATTERNS[:-1]:  # Skip extension pattern
            if re.search(pattern, query, re.IGNORECASE):
                filters.append(f"type:{type_name}")

        # Then check for file extensions
        extension_match = re.search(r'\.(\w+)', query)
        if extension_match:
            ext = extension_match.group(1).lower()
            filters.append(f"extension:{ext}")

        return filters

    def _extract_intent_text(self, query: str) -> str:
        """Extract the remaining text for semantic search after removing filters."""
        text = query

        # Remove date patterns
        for pattern, _ in self.DATE_PATTERNS:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Remove size patterns
        for pattern, _ in self.SIZE_PATTERNS:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Remove type patterns
        for pattern, _ in self.TYPE_PATTERNS:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Remove extra whitespace and common words
        words = text.split()
        meaningful_words = [w for w in words if w.lower() not in self.STOPWORDS]

        return " ".join(meaningful_words) if meaningful_words else ""

    def _extract_keywords(self, intent_text: str) -> List[str]:
        """Extract keywords from intent text."""
        keywords = [w for w in intent_text.split() if w and len(w) > 2]
        return keywords

    def _rule_based_parse(self, query: str) -> Tuple[str, List[str], List[str]]:
        query_lower = query.lower().strip()
        filters: List[str] = []

        filters.extend(self._extract_date_filters(query_lower))
        filters.extend(self._extract_size_filters(query_lower))
        filters.extend(self._extract_type_filters(query_lower))

        intent_text = self._extract_intent_text(query_lower)
        keywords = self._extract_keywords(intent_text)

        return intent_text, keywords, filters

    def _extract_json(self, text: str) -> dict:
        """Extract JSON object from model output."""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}

    def _init_llm(self):
        """Initialize LangChain LLM if API key and deps are available."""
        if not os.getenv("OPENAI_API_KEY"):
            return None
        try:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(temperature=0)
        except Exception:
            try:
                from langchain.chat_models import ChatOpenAI

                return ChatOpenAI(temperature=0)
            except Exception:
                return None

    def get_date_range(self, filter_str: str) -> Tuple[datetime, datetime]:
        """Convert date filter to datetime range."""
        now = datetime.now()

        if filter_str == 'last_week':
            return now - timedelta(days=7), now
        elif filter_str == 'last_month':
            return now - timedelta(days=30), now
        elif filter_str == 'last_year':
            return now - timedelta(days=365), now
        elif filter_str == 'yesterday':
            return now - timedelta(days=1), now
        elif filter_str == 'today':
            return now - timedelta(days=1), now

        return now - timedelta(days=7), now  # default to last week