"""
test_report_generator.py — Report generation for E2E pipeline QA.

Produces three output types:
    1. JSON test report  — structured metrics following the specified schema
    2. Markdown summary  — human-readable table with pass/fail, warnings, timings
    3. OpenSearch docs    — per-chunk/per-image JSON files matching index-mapping.json

Usage:
    from test_report_generator import (
        TestCaseResult, ReportGenerator, build_opensearch_doc_document,
        build_opensearch_doc_image,
    )
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TestCaseResult:
    """Metrics captured for a single test case."""

    test_case_id: str
    file_format: str
    size_variant: str
    file_size_bytes: int

    # MinIO upload
    upload_timestamp: Optional[str] = None
    minio_bucket: Optional[str] = None
    minio_object_key: Optional[str] = None

    # Kafka
    kafka_consumed: Optional[bool] = None

    # Tika extraction
    tika_char_count: Optional[int] = None
    tika_duration_ms: Optional[float] = None
    tika_success: Optional[bool] = None

    # Chunking
    chunk_count: Optional[int] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None

    # Embedding
    embedding_dims: Optional[int] = None
    embedding_duration_ms: Optional[float] = None
    embedding_model: Optional[str] = None

    # Output file
    output_file_path: Optional[str] = None
    output_file_valid: Optional[bool] = None

    # Overall
    total_pipeline_latency_ms: Optional[float] = None
    passed: bool = False
    errors: Optional[str] = None
    warnings: Optional[list[str]] = field(default_factory=list)

    # Edge case metadata
    is_edge_case: bool = False
    expected_failure: bool = False


@dataclass
class HealthCheckResult:
    """Health status of an infrastructure service."""

    service: str
    healthy: bool
    detail: str = ""
    response_time_ms: Optional[float] = None


@dataclass
class TestSummary:
    """Aggregate summary of the test run."""

    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    avg_latency_ms: float = 0.0
    slowest_case: str = ""
    fastest_case: str = ""
    performance_warnings: int = 0
    critical_failures: int = 0


# ---------------------------------------------------------------------------
# OpenSearch document builders
# ---------------------------------------------------------------------------

def build_opensearch_doc_document(
    *,
    embedding: list[float],
    object_key: str,
    bucket: str,
    filename: str,
    content_type: str,
    text: str,
    chunk_index: int,
    total_chunks: int,
    start_char: int,
    end_char: int,
    file_size_bytes: int,
    tika_metadata: Optional[dict] = None,
    ingested_at: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a single OpenSearch document for a text chunk.

    Matches the schema in infrastructure/opensearch/index-mapping.json.
    """
    return {
        "embedding": embedding,
        "object_key": object_key,
        "bucket": bucket,
        "filename": filename,
        "content_type": content_type,
        "doc_type": "document",
        "text": text,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "start_char": start_char,
        "end_char": end_char,
        "file_size_bytes": file_size_bytes,
        "ingested_at": ingested_at or datetime.now(timezone.utc).isoformat(),
        "tika_metadata": tika_metadata or {},
    }


def build_opensearch_doc_image(
    *,
    embedding: list[float],
    object_key: str,
    bucket: str,
    filename: str,
    content_type: str,
    orig_width: int,
    orig_height: int,
    file_size_bytes: int,
    ingested_at: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a single OpenSearch document for an image.

    Matches the schema in infrastructure/opensearch/index-mapping.json.
    """
    return {
        "embedding": embedding,
        "object_key": object_key,
        "bucket": bucket,
        "filename": filename,
        "content_type": content_type,
        "doc_type": "image",
        "orig_width": orig_width,
        "orig_height": orig_height,
        "file_size_bytes": file_size_bytes,
        "ingested_at": ingested_at or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

PERF_WARNING_THRESHOLD_MS = 5000
EXPECTED_EMBEDDING_DIMS = 384


class ReportGenerator:
    """Generates JSON, markdown, and OpenSearch document outputs."""

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or str(uuid.uuid4())
        self.timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # JSON report
    # ------------------------------------------------------------------

    def generate_json_report(
        self,
        results: list[TestCaseResult],
        health_checks: list[HealthCheckResult],
    ) -> str:
        """Generate the structured JSON test report."""
        summary = self._compute_summary(results)

        report = {
            "test_run_id": self.run_id,
            "timestamp": self.timestamp,
            "environment": {
                "kafka": "apache/kafka:3.8.1",
                "minio": "minio/minio:RELEASE.2025-04-22",
                "tika": "apache/tika:latest",
                "model_server": "Obsidian Model Server 1.0.0",
                "text_model": "sentence-transformers/all-MiniLM-L6-v2",
                "image_model": "nomic-ai/nomic-embed-vision-v1.5",
                "expected_embedding_dims": EXPECTED_EMBEDDING_DIMS,
                "chunk_size": 512,
                "chunk_overlap": 50,
            },
            "infrastructure_health": [
                {
                    "service": hc.service,
                    "healthy": hc.healthy,
                    "detail": hc.detail,
                    "response_time_ms": hc.response_time_ms,
                }
                for hc in health_checks
            ],
            "results": [self._result_to_dict(r) for r in results],
            "summary": {
                "total_tests": summary.total_tests,
                "passed": summary.passed,
                "failed": summary.failed,
                "skipped": summary.skipped,
                "avg_latency_ms": round(summary.avg_latency_ms, 1),
                "slowest_case": summary.slowest_case,
                "fastest_case": summary.fastest_case,
                "performance_warnings": summary.performance_warnings,
                "critical_failures": summary.critical_failures,
            },
        }

        return json.dumps(report, indent=2, default=str)

    # ------------------------------------------------------------------
    # Markdown report
    # ------------------------------------------------------------------

    def generate_markdown_report(
        self,
        results: list[TestCaseResult],
        health_checks: list[HealthCheckResult],
    ) -> str:
        """Generate a human-readable markdown summary."""
        summary = self._compute_summary(results)
        lines: list[str] = []

        # Header
        lines.append(f"# E2E Pipeline QA Test Report")
        lines.append("")
        lines.append(f"**Run ID:** `{self.run_id}`")
        lines.append(f"**Timestamp:** {self.timestamp}")
        lines.append("")

        # Infrastructure health
        lines.append("## Infrastructure Health")
        lines.append("")
        lines.append("| Service | Status | Detail | Response (ms) |")
        lines.append("|---------|--------|--------|---------------|")
        for hc in health_checks:
            icon = "✅" if hc.healthy else "❌"
            rt = f"{hc.response_time_ms:.0f}" if hc.response_time_ms else "—"
            lines.append(f"| {hc.service} | {icon} | {hc.detail} | {rt} |")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Tests | {summary.total_tests} |")
        lines.append(f"| ✅ Passed | {summary.passed} |")
        lines.append(f"| ❌ Failed | {summary.failed} |")
        lines.append(f"| ⏭️ Skipped | {summary.skipped} |")
        lines.append(f"| Avg Latency | {summary.avg_latency_ms:.0f} ms |")
        lines.append(f"| Slowest | `{summary.slowest_case}` |")
        lines.append(f"| Fastest | `{summary.fastest_case}` |")
        lines.append(f"| ⚠️ Perf Warnings | {summary.performance_warnings} |")
        lines.append(f"| 🔴 Critical Failures | {summary.critical_failures} |")
        lines.append("")

        # Standard test results
        standard = [r for r in results if not r.is_edge_case]
        if standard:
            lines.append("## Standard Test Matrix")
            lines.append("")
            lines.append(
                "| # | Test Case | Format | Size | File Size | "
                "Tika Chars | Tika (ms) | Chunks | Embed Dims | Embed (ms) | "
                "Total (ms) | Status |"
            )
            lines.append(
                "|---|-----------|--------|------|-----------|"
                "-----------|-----------|--------|------------|------------|"
                "------------|--------|"
            )
            for i, r in enumerate(standard, 1):
                status = self._status_icon(r)
                tika_chars = f"{r.tika_char_count:,}" if r.tika_char_count is not None else "—"
                tika_ms = f"{r.tika_duration_ms:.0f}" if r.tika_duration_ms is not None else "—"
                chunks = str(r.chunk_count) if r.chunk_count is not None else "—"
                dims = str(r.embedding_dims) if r.embedding_dims is not None else "—"
                embed_ms = f"{r.embedding_duration_ms:.0f}" if r.embedding_duration_ms is not None else "—"
                total_ms = f"{r.total_pipeline_latency_ms:.0f}" if r.total_pipeline_latency_ms is not None else "—"
                fs = self._format_bytes(r.file_size_bytes)

                lines.append(
                    f"| {i} | `{r.test_case_id}` | {r.file_format} | {r.size_variant} | "
                    f"{fs} | {tika_chars} | {tika_ms} | {chunks} | {dims} | "
                    f"{embed_ms} | {total_ms} | {status} |"
                )
            lines.append("")

        # Edge case results
        edge_cases = [r for r in results if r.is_edge_case]
        if edge_cases:
            lines.append("## Edge Case Results")
            lines.append("")
            lines.append("| # | Test Case | Description | Expected | Status | Detail |")
            lines.append("|---|-----------|-------------|----------|--------|--------|")
            for i, r in enumerate(edge_cases, 1):
                status = self._status_icon(r)
                expected = "Failure" if r.expected_failure else "Success"
                detail = r.errors or "OK"
                if len(detail) > 60:
                    detail = detail[:57] + "..."
                lines.append(
                    f"| {i} | `{r.test_case_id}` | {r.file_format} {r.size_variant} | "
                    f"{expected} | {status} | {detail} |"
                )
            lines.append("")

        # Warnings
        warnings = [r for r in results if r.warnings]
        if warnings:
            lines.append("## Warnings")
            lines.append("")
            for r in warnings:
                for w in r.warnings:
                    lines.append(f"- ⚠️ **{r.test_case_id}**: {w}")
            lines.append("")

        # Failures
        failures = [r for r in results if not r.passed]
        unexpected_failures = [f for f in failures if not f.expected_failure]
        if unexpected_failures:
            lines.append("## Failures (Unexpected)")
            lines.append("")
            for r in unexpected_failures:
                lines.append(f"### `{r.test_case_id}`")
                lines.append(f"- **Error:** {r.errors}")
                lines.append(f"- **Format:** {r.file_format}, **Size:** {r.size_variant}")
                lines.append("")

        lines.append("---")
        lines.append(f"*Generated by E2E Pipeline QA Test Suite — {self.timestamp}*")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def save_reports(
        self,
        output_dir: Path,
        results: list[TestCaseResult],
        health_checks: list[HealthCheckResult],
    ) -> tuple[Path, Path]:
        """
        Save JSON and markdown reports to the output directory.

        Returns (json_path, markdown_path).
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        json_path = output_dir / "report.json"
        md_path = output_dir / "report.md"

        json_content = self.generate_json_report(results, health_checks)
        json_path.write_text(json_content, encoding="utf-8")
        logger.info("JSON report saved: %s", json_path)

        md_content = self.generate_markdown_report(results, health_checks)
        md_path.write_text(md_content, encoding="utf-8")
        logger.info("Markdown report saved: %s", md_path)

        return json_path, md_path

    def save_opensearch_docs(
        self,
        output_dir: Path,
        docs: list[tuple[str, dict]],
    ) -> Path:
        """
        Save OpenSearch-ready documents to a subdirectory.

        Args:
            output_dir: Base output directory.
            docs: List of (filename, doc_dict) tuples.

        Returns:
            Path to the opensearch_docs directory.
        """
        docs_dir = output_dir / "opensearch_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        for filename, doc in docs:
            doc_path = docs_dir / filename
            doc_path.write_text(
                json.dumps(doc, indent=2, default=str), encoding="utf-8"
            )

        logger.info(
            "Saved %d OpenSearch documents to %s", len(docs), docs_dir
        )
        return docs_dir

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_summary(self, results: list[TestCaseResult]) -> TestSummary:
        """Compute aggregate summary from test results."""
        summary = TestSummary(total_tests=len(results))

        latencies: list[float] = []
        latency_map: dict[str, float] = {}

        for r in results:
            if r.passed:
                summary.passed += 1
            else:
                summary.failed += 1

            if r.total_pipeline_latency_ms is not None:
                latencies.append(r.total_pipeline_latency_ms)
                latency_map[r.test_case_id] = r.total_pipeline_latency_ms

                if r.total_pipeline_latency_ms > PERF_WARNING_THRESHOLD_MS:
                    summary.performance_warnings += 1

            if (
                r.embedding_dims is not None
                and r.embedding_dims != EXPECTED_EMBEDDING_DIMS
                and not r.expected_failure
            ):
                summary.critical_failures += 1

        if latencies:
            summary.avg_latency_ms = sum(latencies) / len(latencies)
            summary.slowest_case = max(latency_map, key=latency_map.get)
            summary.fastest_case = min(latency_map, key=latency_map.get)

        return summary

    @staticmethod
    def _result_to_dict(r: TestCaseResult) -> dict[str, Any]:
        """Convert a TestCaseResult to a JSON-serializable dict."""
        d = {
            "test_case_id": r.test_case_id,
            "file_format": r.file_format,
            "size_variant": r.size_variant,
            "file_size_bytes": r.file_size_bytes,
            "upload_timestamp": r.upload_timestamp,
            "minio_bucket": r.minio_bucket,
            "minio_object_key": r.minio_object_key,
            "kafka_consumed": r.kafka_consumed,
            "tika_char_count": r.tika_char_count,
            "tika_duration_ms": (
                round(r.tika_duration_ms, 1) if r.tika_duration_ms is not None else None
            ),
            "chunk_count": r.chunk_count,
            "chunk_size": r.chunk_size,
            "chunk_overlap": r.chunk_overlap,
            "embedding_dims": r.embedding_dims,
            "embedding_duration_ms": (
                round(r.embedding_duration_ms, 1)
                if r.embedding_duration_ms is not None
                else None
            ),
            "embedding_model": r.embedding_model,
            "output_file_path": r.output_file_path,
            "output_file_valid": r.output_file_valid,
            "total_pipeline_latency_ms": (
                round(r.total_pipeline_latency_ms, 1)
                if r.total_pipeline_latency_ms is not None
                else None
            ),
            "passed": r.passed,
            "errors": r.errors,
            "warnings": r.warnings if r.warnings else None,
            "is_edge_case": r.is_edge_case,
            "expected_failure": r.expected_failure,
        }
        return d

    @staticmethod
    def _status_icon(r: TestCaseResult) -> str:
        """Return a status icon for a test result."""
        if r.passed:
            if r.warnings:
                return "⚠️ WARN"
            return "✅ PASS"
        if r.expected_failure:
            return "✅ EXPECTED FAIL"
        return "❌ FAIL"

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Human-readable byte size."""
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"
