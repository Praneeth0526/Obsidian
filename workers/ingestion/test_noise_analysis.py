"""
test_noise_analysis.py — Noise detection across all supported file formats.

Runs each file through Tika → Chunker and classifies every chunk against
a suite of noise detectors. Produces a per-file and aggregate report.

Usage:
    python test_noise_analysis.py file1.pdf file2.docx file3.pptx file4.txt ...

Requires Tika to be running: docker compose up -d
"""

import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tika_extractor import TikaExtractor, is_supported
from chunker import TextChunker, ChunkResult


# ---------------------------------------------------------------------------
# MIME type lookup
# ---------------------------------------------------------------------------

EXTENSION_TO_MIME = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt":  "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt":  "text/plain",
}


# ---------------------------------------------------------------------------
# Noise detectors
# Each detector returns (is_noisy: bool, detail: str)
# ---------------------------------------------------------------------------

def detect_hex_unicode(chunk: ChunkResult) -> tuple[bool, str]:
    """Hex-encoded Unicode blobs like <FEFF004100...> or raw hex runs."""
    # <FEFF...> style (PDF locale strings)
    if re.search(r"<FEFF[0-9A-Fa-f]{4,}", chunk.text):
        return True, "hex-unicode-blob (<FEFF...>)"
    # Long runs of pure hex (8+ consecutive hex pairs with no readable text)
    hex_run = re.findall(r"[0-9A-Fa-f]{16,}", chunk.text)
    non_space = chunk.text.replace(" ", "").replace("\n", "")
    if hex_run and len("".join(hex_run)) / max(len(non_space), 1) > 0.5:
        return True, "hex-run (>50% hex chars)"
    return False, ""


def detect_pdf_internals(chunk: ChunkResult) -> tuple[bool, str]:
    """PDF/PostScript internal syntax that leaked through Tika."""
    pdf_keywords = [
        r"/IncludeSlug",
        r"/OmitPlacedBitmaps",
        r"/OmitPlacedEPS",
        r"/OmitPlacedPDF",
        r"/DestinationProfileSelector",
        r"/PDFXOutputIntentProfileSelector",
        r"/PreserveEditing",
        r"/UntaggedCMYKHandling",
        r"/UntaggedRGBHandling",
        r"/SimulateOverprint",
        r"/Downsample16BitImages",
        r"/FlattenerPreset",
        r"/PresetSelector",
        r"/Namespace\s*\[",
        r"endstream",
        r"endobj",
        r"xref\s+\d+",
        r"%%EOF",
        r"BT\s+/F\d+",          # PDF text object operators
        r"Tf\s+\d+\s+Td",
    ]
    matches = [kw for kw in pdf_keywords if re.search(kw, chunk.text)]
    if matches:
        return True, f"pdf-internals ({', '.join(matches[:3])})"
    return False, ""


def detect_distiller_settings(chunk: ChunkResult) -> tuple[bool, str]:
    """Adobe Distiller / Acrobat settings blocks."""
    distiller_patterns = [
        r"Acrobat Distiller",
        r"GrayACSImageDict",
        r"ColorACSImageDict",
        r"MonoImageDict",
        r"AntiAliasColorImages",
        r"AutoFilterColorImages",
        r"ColorImageDownsampleType",
        r"GrayImageDownsampleType",
        r"PreserveCopyPage",
        r"PreserveEPSInfo",
        r"PreserveHalftoneInfo",
    ]
    matches = [p for p in distiller_patterns if re.search(p, chunk.text)]
    if matches:
        return True, f"distiller-settings ({', '.join(matches[:2])})"
    return False, ""


def detect_xml_metadata(chunk: ChunkResult) -> tuple[bool, str]:
    """Raw XML / XMP metadata that leaked through."""
    if re.search(r"<x:xmpmeta|<rdf:RDF|xmlns:|xpacket", chunk.text):
        return True, "xml-xmp-metadata"
    if re.search(r"<\?xml version", chunk.text):
        return True, "xml-declaration"
    return False, ""


def detect_locale_strings(chunk: ChunkResult) -> tuple[bool, str]:
    """Multi-language locale string blocks (ENU/DEU/FRA/NLD etc.)."""
    locale_codes = re.findall(r"/(?:ENU|DEU|FRA|NLD|ITA|ESP|PTB|DAN|NOR|SVE|FIN|PLK|CSY|HUN|TRK|RUS|ELL|ARA|HEB|JPN|KOR|CHS|CHT)\s+\(", chunk.text)
    if len(locale_codes) >= 2:
        return True, f"locale-strings ({len(locale_codes)} language blocks)"
    return False, ""


def detect_high_non_ascii_ratio(chunk: ChunkResult) -> tuple[bool, str]:
    """Chunks where most characters are non-ASCII (binary residue)."""
    if not chunk.text:
        return False, ""
    non_ascii = sum(1 for c in chunk.text if ord(c) > 127)
    ratio = non_ascii / len(chunk.text)
    if ratio > 0.4:
        return True, f"high-non-ascii ({ratio:.0%} non-ASCII chars)"
    return False, ""


def detect_repetitive_content(chunk: ChunkResult) -> tuple[bool, str]:
    """Chunks that are mostly the same short pattern repeated."""
    text = chunk.text.strip()
    if len(text) < 40:
        return False, ""
    # Split into 10-char windows and check uniqueness
    windows = [text[i:i+10] for i in range(0, len(text)-10, 10)]
    unique_ratio = len(set(windows)) / max(len(windows), 1)
    if unique_ratio < 0.2:
        return True, f"repetitive-content (only {unique_ratio:.0%} unique 10-char windows)"
    return False, ""


def detect_whitespace_dominated(chunk: ChunkResult) -> tuple[bool, str]:
    """Chunks that are mostly whitespace / line breaks."""
    printable = sum(1 for c in chunk.text if c.strip())
    ratio = printable / max(len(chunk.text), 1)
    if ratio < 0.3:
        return True, f"whitespace-dominated ({ratio:.0%} printable chars)"
    return False, ""


def detect_numeric_only(chunk: ChunkResult) -> tuple[bool, str]:
    """Chunks that are pure numbers/punctuation with no real words."""
    words = re.findall(r"[a-zA-Z]{3,}", chunk.text)
    if not words and len(chunk.text) > 30:
        return True, "no-words (numeric/symbol only)"
    return False, ""


# Registry of all detectors
DETECTORS = [
    detect_hex_unicode,
    detect_pdf_internals,
    detect_distiller_settings,
    detect_xml_metadata,
    detect_locale_strings,
    detect_high_non_ascii_ratio,
    detect_repetitive_content,
    detect_whitespace_dominated,
    detect_numeric_only,
]


# ---------------------------------------------------------------------------
# Chunk analysis
# ---------------------------------------------------------------------------

@dataclass
class ChunkAnalysis:
    chunk: ChunkResult
    noise_flags: list[str] = field(default_factory=list)

    @property
    def is_noisy(self) -> bool:
        return len(self.noise_flags) > 0


def analyse_chunk(chunk: ChunkResult) -> ChunkAnalysis:
    """Run all detectors against a single chunk."""
    analysis = ChunkAnalysis(chunk=chunk)
    for detector in DETECTORS:
        flagged, detail = detector(chunk)
        if flagged:
            analysis.noise_flags.append(detail)
    return analysis


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

@dataclass
class FileReport:
    filename: str
    content_type: str
    raw_bytes: int
    extracted_chars: int
    total_chunks: int
    noisy_chunks: list[ChunkAnalysis] = field(default_factory=list)
    extraction_error: str = ""

    @property
    def clean_count(self) -> int:
        return self.total_chunks - len(self.noisy_chunks)

    @property
    def noise_ratio(self) -> float:
        return len(self.noisy_chunks) / max(self.total_chunks, 1)


async def process_file(
    file_path: Path,
    extractor: TikaExtractor,
    chunker: TextChunker,
) -> FileReport:
    suffix = file_path.suffix.lower()
    content_type = EXTENSION_TO_MIME.get(suffix, "application/octet-stream")
    file_bytes = file_path.read_bytes()

    report = FileReport(
        filename=file_path.name,
        content_type=content_type,
        raw_bytes=len(file_bytes),
        extracted_chars=0,
        total_chunks=0,
    )

    if not is_supported(content_type):
        report.extraction_error = f"Unsupported MIME type: {content_type}"
        return report

    result = await extractor.extract(file_bytes, content_type=content_type)
    if not result.success:
        report.extraction_error = result.error or "Extraction failed"
        return report

    if result.is_empty:
        report.extraction_error = "Tika returned empty text"
        return report

    report.extracted_chars = len(result.text)
    chunks = chunker.chunk(result.text)
    report.total_chunks = len(chunks)

    for chunk in chunks:
        analysis = analyse_chunk(chunk)
        if analysis.is_noisy:
            report.noisy_chunks.append(analysis)

    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

NOISE_BAR = {
    "clean":    "🟢",
    "low":      "🟡",  # < 15% noisy
    "medium":   "🟠",  # 15–40% noisy
    "high":     "🔴",  # > 40% noisy
}

def noise_level(ratio: float) -> str:
    if ratio == 0:     return "clean"
    if ratio < 0.15:   return "low"
    if ratio < 0.40:   return "medium"
    return "high"


def print_file_report(report: FileReport) -> None:
    print(f"\n{'=' * 70}")
    print(f"📄 {report.filename}  [{report.content_type}]")
    print(f"{'=' * 70}")

    if report.extraction_error:
        print(f"  ❌ Skipped: {report.extraction_error}")
        return

    level = noise_level(report.noise_ratio)
    icon  = NOISE_BAR[level]
    print(f"  Raw bytes:       {report.raw_bytes:>10,}")
    print(f"  Extracted chars: {report.extracted_chars:>10,}")
    print(f"  Total chunks:    {report.total_chunks:>10}")
    print(f"  Clean chunks:    {report.clean_count:>10}")
    print(f"  Noisy chunks:    {len(report.noisy_chunks):>10}  "
          f"({report.noise_ratio:.0%})  {icon} {level.upper()}")

    if not report.noisy_chunks:
        print("\n  ✅ No noise detected in any chunk.")
        return

    # Count noise types across all noisy chunks
    noise_type_counts: dict[str, int] = {}
    for a in report.noisy_chunks:
        for flag in a.noise_flags:
            # Normalise to the base type label
            base = flag.split(" (")[0]
            noise_type_counts[base] = noise_type_counts.get(base, 0) + 1

    print("\n  ── Noise breakdown ──")
    for ntype, count in sorted(noise_type_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 30)
        print(f"    {ntype:<35} {count:>3} chunks  {bar}")

    print("\n  ── Noisy chunk details ──")
    for a in report.noisy_chunks:
        c = a.chunk
        flags_str = " | ".join(a.noise_flags)
        preview = c.text[:80].replace("\n", "↵").replace("\r", "")
        print(f"    Chunk {c.chunk_index+1:>4}/{c.total_chunks}  "
              f"[chars {c.start_char}–{c.end_char}]")
        print(f"           Flags:   {flags_str}")
        print(f"           Preview: {preview}...")
        print()


def print_aggregate_report(reports: list[FileReport]) -> None:
    valid = [r for r in reports if not r.extraction_error]
    if not valid:
        print("\n⚠️  No files were successfully processed.")
        return

    total_chunks = sum(r.total_chunks for r in valid)
    total_noisy  = sum(len(r.noisy_chunks) for r in valid)
    total_clean  = total_chunks - total_noisy

    # Collect all noise type counts
    all_noise: dict[str, int] = {}
    for r in valid:
        for a in r.noisy_chunks:
            for flag in a.noise_flags:
                base = flag.split(" (")[0]
                all_noise[base] = all_noise.get(base, 0) + 1

    print(f"\n{'=' * 70}")
    print("📊 AGGREGATE NOISE REPORT")
    print(f"{'=' * 70}")
    print(f"  Files processed:  {len(valid)} / {len(reports)}")
    print(f"  Total chunks:     {total_chunks}")
    print(f"  Clean chunks:     {total_clean}  ({total_clean/max(total_chunks,1):.0%})")
    print(f"  Noisy chunks:     {total_noisy}  ({total_noisy/max(total_chunks,1):.0%})")

    if all_noise:
        print("\n  ── Noise types across all files ──")
        for ntype, count in sorted(all_noise.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 40)
            print(f"    {ntype:<35} {count:>3}  {bar}")

    print("\n  ── Per-file summary ──")
    for r in reports:
        if r.extraction_error:
            print(f"    ❌  {r.filename:<35} ERROR: {r.extraction_error}")
        else:
            level = noise_level(r.noise_ratio)
            icon  = NOISE_BAR[level]
            print(f"    {icon}  {r.filename:<35} "
                  f"{r.total_chunks:>4} chunks, "
                  f"{len(r.noisy_chunks):>3} noisy "
                  f"({r.noise_ratio:.0%})")

    overall_ratio = total_noisy / max(total_chunks, 1)
    level = noise_level(overall_ratio)
    icon  = NOISE_BAR[level]
    print(f"\n  Overall noise level: {icon} {level.upper()} ({overall_ratio:.0%})")

    if total_noisy > 0:
        print("\n  ⚠️  Noisy chunks found — a text-cleaning step is recommended")
        print("     before embedding to avoid polluting the OpenSearch index.")
    else:
        print("\n  ✅ All chunks are clean across all files!")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python test_noise_analysis.py file1.pdf file2.docx ...")
        print("\nSupported formats: .pdf  .doc  .docx  .ppt  .pptx  .txt")
        return

    file_paths = [Path(p) for p in sys.argv[1:]]

    # Validate paths
    missing = [p for p in file_paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"❌ File not found: {p}")
        return

    extractor = TikaExtractor(tika_url="http://localhost:9998")
    chunker   = TextChunker(chunk_size=512, chunk_overlap=50)

    print("=" * 70)
    print("🔬 Tika → Chunker Noise Analysis")
    print("=" * 70)

    # Health check
    print("\nChecking Tika server...")
    if not await extractor.health_check():
        print("❌ Tika is not reachable at http://localhost:9998")
        print("   Run: docker compose up -d")
        return
    print("✅ Tika is healthy\n")

    print(f"Processing {len(file_paths)} file(s)...\n")

    reports: list[FileReport] = []
    for fp in file_paths:
        print(f"  → {fp.name} ...", end=" ", flush=True)
        report = await process_file(fp, extractor, chunker)
        reports.append(report)
        if report.extraction_error:
            print(f"❌ {report.extraction_error}")
        else:
            print(f"✅ {report.total_chunks} chunks, "
                  f"{len(report.noisy_chunks)} noisy")

    # Per-file detailed reports
    for report in reports:
        print_file_report(report)

    # Aggregate summary
    print_aggregate_report(reports)


if __name__ == "__main__":
    asyncio.run(main())
