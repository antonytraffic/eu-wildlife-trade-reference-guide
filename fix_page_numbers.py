#!/usr/bin/env python3
"""
fix_page_numbers.py — Update page_start and page_end in YAML frontmatter of
all output/ markdown files to match actual PDF page numbers.

Strategy:
  1. Parse ToC pages (PDF pages 3–7, indices 2–6) with pypdf.
  2. Extract (heading_text, page_num) pairs via dot-leader regex.
  3. For each markdown file determine a query:
       - Sub-pages  : use sub_section field (e.g. "3.1 Overview")
       - Annexes    : use title field (e.g. "Annex I")
       - Simple secs: use "N. title" constructed from section_number + title
  4. Match via prefix (numbered items) or rapidfuzz (80 % threshold).
  5. Write page_start; compute page_end as next section's page_start – 1.
  6. Print a rich table with results.
"""

import re
import sys
import yaml
from pathlib import Path

import pypdf
from rapidfuzz import fuzz, process as rfprocess
from rich.console import Console
from rich.table import Table

PDF_PATH   = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.pdf")
OUTPUT_DIR = Path("output")

# PDF pages 3–7 contain the ToC (0-indexed: 2–6).
TOC_PAGE_INDICES  = list(range(2, 7))
CONFIDENCE_MIN    = 80       # below this → flag, don't write

console = Console()

# ── YAML helpers ──────────────────────────────────────────────────────────────

def parse_fm(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        try:
            end = text.index("\n---\n", 4)
            meta = yaml.safe_load(text[4:end]) or {}
            body = text[end + 5:]
            return meta, body
        except (ValueError, yaml.YAMLError):
            pass
    return {}, text


def make_file(meta: dict, body: str) -> str:
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n\n{body.lstrip()}"


# ── PDF ToC extraction ─────────────────────────────────────────────────────────

# Lines with at least 3 dots followed by a page number.
_DOT_RE = re.compile(r"^(.+?)\s*\.{3,}\s*(\d{1,3})\s*$")
# Lines with fewer dots (sub-entries like "2.1 Title .....13")
_SPARSE_DOT_RE = re.compile(r"^(.+?)\s*\.{1,}\s*(\d{1,3})\s*$")


def extract_toc_entries(pdf_path: Path) -> list[tuple[str, int]]:
    """Return [(raw_heading, pdf_page_num), ...] from ToC pages."""
    reader = pypdf.PdfReader(str(pdf_path))
    entries: list[tuple[str, int]] = []
    seen: set[str] = set()

    for idx in TOC_PAGE_INDICES:
        if idx >= len(reader.pages):
            continue
        text = reader.pages[idx].extract_text() or ""
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            m = _DOT_RE.match(line) or _SPARSE_DOT_RE.match(line)
            if not m:
                continue
            heading = m.group(1).strip().rstrip(". ")
            page_num = int(m.group(2))
            if page_num < 1 or page_num > 500:
                continue
            key = heading.lower()
            if key in seen:
                continue
            seen.add(key)
            entries.append((heading, page_num))

    return entries


# ── Normalisation helpers ─────────────────────────────────────────────────────

_NUM_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+")

def _strip_num(s: str) -> str:
    return _NUM_PREFIX_RE.sub("", s.strip()).strip()


def _norm(s: str) -> str:
    """Lower-case, collapse whitespace, strip number prefix."""
    return _strip_num(s).lower()


# ── Matching ──────────────────────────────────────────────────────────────────

def match_by_prefix(entries: list[tuple[str, int]], prefix: str) -> tuple[int | None, str, float]:
    """Return (page_num, matched_heading, confidence) for first entry whose
    heading starts with *prefix* (case-insensitive)."""
    p = prefix.lower().strip()
    for heading, page_num in entries:
        if heading.lower().strip().startswith(p):
            return page_num, heading, 100.0
    return None, "", 0.0


def match_fuzzy(entries: list[tuple[str, int]], query: str) -> tuple[int | None, str, float]:
    """Fuzzy-match *query* against ToC headings using token_sort_ratio."""
    choices = [h for h, _ in entries]
    result = rfprocess.extractOne(query, choices, scorer=fuzz.token_sort_ratio)
    if result is None:
        return None, "", 0.0
    matched_text, score, idx = result
    return entries[idx][1], matched_text, float(score)


def find_page(entries: list[tuple[str, int]], query: str,
              num_prefix: str = "") -> tuple[int | None, str, float]:
    """
    Try prefix match first (reliable for numbered items), then fuzzy.
    *num_prefix*: e.g. "3.1" for sub-sections, "ANNEX I" for annexes.
    """
    if num_prefix:
        page, heading, conf = match_by_prefix(entries, num_prefix)
        if page is not None:
            return page, heading, conf

    # Fuzzy on cleaned query
    page, heading, conf = match_fuzzy(entries, query)
    return page, heading, conf


# ── Load all output files ─────────────────────────────────────────────────────

SKIP_FILES = {
    "_footer_content.md",
    "annexes.md",
    "01_preliminary_content.md",
    # Old split-source files (should not exist after restructuring but guard anyway)
    "04_3_what_are_the_rules_governing_trade_into_and_from_the_eu_fo.md",
    "05_4_what_rules_govern_internal_eu_trade.md",
}


def load_md_files() -> list[tuple[Path, dict, str]]:
    """Return [(path, meta, body)] for files we should update."""
    result = []
    for p in sorted(OUTPUT_DIR.glob("*.md")):
        if p.name in SKIP_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_fm(text)
        if not meta:
            continue
        # Skip parent pages (they have sub_pages key)
        if "sub_pages" in meta:
            continue
        result.append((p, meta, body))
    return result


# ── Determine query / num_prefix per file ─────────────────────────────────────

def _roman_to_int(r: str) -> int:
    """Convert roman numeral string to int for ordering."""
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    r = r.lower()
    total = 0
    prev = 0
    for ch in reversed(r):
        v = vals.get(ch, 0)
        total += v if v >= prev else -v
        prev = v
    return total


def build_query(meta: dict) -> tuple[str, str]:
    """Return (query_for_fuzzy, num_prefix_for_prefix_match)."""
    title       = meta.get("title", "")
    sub_section = meta.get("sub_section", "")
    section_num = meta.get("section_number", 0)
    parent      = meta.get("parent", "")

    # Annex sub-page
    if parent == "annexes":
        # title is like "Annex I", "Annex XIX"
        return title, title.upper()

    # Sub-page of section 3 or 4
    if sub_section:
        # sub_section like "3.1 Overview" — use the numeric prefix "3.1"
        m = re.match(r"^(\d+\.\d+)", sub_section)
        prefix = m.group(1) if m else ""
        return sub_section, prefix

    # Simple section — construct "N. Title"
    query = f"{section_num}. {title}"
    prefix = f"{section_num}."
    return query, prefix


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not PDF_PATH.exists():
        console.print(f"[red]PDF not found:[/red] {PDF_PATH}")
        sys.exit(1)

    console.rule("[bold]fix_page_numbers.py[/bold]")

    # Step 1: extract ToC entries
    console.print("\n[bold cyan]Step 1:[/bold cyan] Parsing PDF ToC pages …")
    entries = extract_toc_entries(PDF_PATH)
    console.print(f"  Found [bold]{len(entries)}[/bold] ToC entries")
    if not entries:
        console.print("[red]No entries found — aborting.[/red]")
        sys.exit(1)

    # Show first few for sanity check
    for h, p in entries[:6]:
        console.print(f"  [dim]p.{p:>3}  {h[:70]}[/dim]")

    # Step 2: load markdown files
    console.print("\n[bold cyan]Step 2:[/bold cyan] Loading output/ files …")
    md_files = load_md_files()
    console.print(f"  {len(md_files)} files to process")

    # Step 3: match each file
    console.print("\n[bold cyan]Step 3:[/bold cyan] Matching …")

    results: list[dict] = []

    for path, meta, body in md_files:
        query, num_prefix = build_query(meta)
        page, matched_heading, conf = find_page(entries, query, num_prefix)

        old_start = meta.get("page_start", "—")

        results.append({
            "path":    path,
            "meta":    meta,
            "body":    body,
            "query":   query,
            "matched": matched_heading,
            "conf":    conf,
            "page":    page,
            "old":     old_start,
        })

    # Step 4: compute page_end from sorted page_start values
    # Sort by matched page number to assign page_end = next_page_start - 1
    matched_results = [r for r in results if r["page"] is not None and r["conf"] >= CONFIDENCE_MIN]
    matched_results.sort(key=lambda r: r["page"])

    for i, r in enumerate(matched_results):
        if i + 1 < len(matched_results):
            # Ensure page_end >= page_start (sections can share a page)
            r["page_end"] = max(r["page"], matched_results[i + 1]["page"] - 1)
        else:
            # Last section — use last PDF page (216 for this document)
            r["page_end"] = 216

    # Build a lookup for easy access
    page_end_map: dict[Path, int] = {r["path"]: r["page_end"] for r in matched_results}

    # Step 5: write files
    console.print("\n[bold cyan]Step 5:[/bold cyan] Writing updated frontmatter …")

    table = Table(
        show_header=True,
        header_style="bold white on black",
        show_lines=True,
        width=120,
    )
    table.add_column("File",         width=42)
    table.add_column("Query",        width=26)
    table.add_column("Matched",      width=26)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Old p.", width=6, justify="right")
    table.add_column("New p.", width=6, justify="right")
    table.add_column("End p.", width=6, justify="right")

    written = flagged = skipped = 0

    for r in results:
        conf_int = int(r["conf"])
        page     = r["page"]
        note     = ""

        if page is None or conf_int < CONFIDENCE_MIN:
            row_style  = "yellow"
            new_start  = "—"
            new_end    = "—"
            note       = "LOW" if page else "MISS"
            flagged   += 1
        else:
            row_style = ""
            new_start = str(page)
            new_end   = str(page_end_map.get(r["path"], "?"))

            meta = dict(r["meta"])
            meta["page_start"] = page
            meta["page_end"]   = page_end_map.get(r["path"], meta.get("page_end", 0))

            r["path"].write_text(make_file(meta, r["body"]), encoding="utf-8")
            written += 1

        table.add_row(
            f"[{row_style}]{r['path'].name[:41]}[/{row_style}]" if row_style else r["path"].name[:41],
            r["query"][:25],
            r["matched"][:25],
            str(conf_int),
            str(r["old"]),
            f"[{row_style}]{new_start}[/{row_style}]" if row_style else new_start,
            f"[{row_style}]{new_end}[/{row_style}]" if row_style else new_end,
        )

    console.print(table)
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{written} updated · {flagged} flagged · {skipped} skipped"
    )

    if flagged:
        console.print(
            "[yellow]Flagged files need manual review "
            "(confidence < 80 % or no ToC match found).[/yellow]"
        )


if __name__ == "__main__":
    main()
