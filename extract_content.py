"""
EU Wildlife Trade Reference Guide — DOCX extraction and markdown structuring.

Usage:
    python extract_content.py

Reads:  data/CITES Reference Guide_Nov 2025 FIN_clean.docx
Writes: output/<NN>_<slug>.md  (one file per Heading 1 paragraph)

Headers and footers are in the Word document's dedicated header/footer sections
and are never visited during body iteration — no special stripping is required.

Page numbers are approximated by counting explicit page-break elements
(w:br type="page") and paragraph-level section breaks in the XML.  The document
contains no lastRenderedPageBreak markers, so numbers are best-effort.

Requires ANTHROPIC_API_KEY in environment for summary generation.
"""

import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import anthropic
import yaml
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from rich.console import Console
from rich.table import Table as RichTable
from rich import box

# ── Configuration ─────────────────────────────────────────────────────────────

DOCX_PATH  = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.docx")
OUTPUT_DIR = Path("output")

LOW_WORD_COUNT_THRESHOLD = 50
SUMMARY_MODEL            = "claude-haiku-4-5-20251001"

# Word namespace URI
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# ── Style classification ───────────────────────────────────────────────────────

# Word heading style → markdown prefix  (None = Heading 1, triggers new file)
HEADING_STYLES: dict[str, str | None] = {
    "Heading 1": None,
    "Heading 2": "##",
    "Heading 3": "###",
    "Heading 4": "####",
    "Heading 5": "#####",
    "Heading 6": "######",
}

# Table-of-contents and index styles → skip entirely (noise for RAG)
SKIP_STYLES = frozenset({
    "toc 1", "toc 2", "toc 3", "toc 4", "toc 5",
    "toc 6", "toc 7", "toc 8", "toc 9",
    "table of figures", "index 2", "index 1",
})

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text[:60]


def iter_block_items(doc: Document):
    """
    Yield every paragraph and table in the document body, in document order.
    This is the standard python-docx pattern for mixed content iteration.
    Headers, footers, footnotes, and comments are NOT visited.
    """
    for child in doc.element.body.iterchildren():
        tag = child.tag  # Clark notation: {namespace}localname
        if tag == f"{{{W}}}p":
            yield DocxParagraph(child, doc)
        elif tag == f"{{{W}}}tbl":
            yield DocxTable(child, doc)
        # sectPr (section properties), bookmarkStart, etc. → silently skipped


def page_breaks_in_para(para_el) -> int:
    """
    Count page-boundary events inside a paragraph's XML element.

    Counted separately so we don't double-count:
      1. Explicit page-break runs:  <w:br w:type="page"/>
      2. Section breaks (nextPage / evenPage / oddPage) in <w:pPr/w:sectPr>
    """
    n = 0
    # Explicit page-break character inside a run
    for br in para_el.iter(f"{{{W}}}br"):
        if br.get(f"{{{W}}}type") == "page":
            n += 1
    # Section break encoded in paragraph properties
    pPr = para_el.find(f"{{{W}}}pPr")
    if pPr is not None:
        sectPr = pPr.find(f"{{{W}}}sectPr")
        if sectPr is not None:
            type_el = sectPr.find(f"{{{W}}}type")
            val = type_el.get(f"{{{W}}}val", "") if type_el is not None else ""
            # Default (no <w:type>) is nextPage
            if val in ("", "nextPage", "evenPage", "oddPage"):
                n += 1
    return n


def list_marker(para: DocxParagraph) -> str | None:
    """
    Return a markdown list prefix if the paragraph is a list item, else None.
    Indent level is derived from the ilvl attribute in w:numPr.
    """
    style_name = para.style.name if para.style else ""

    # Explicit Word bullet styles
    if re.search(r"\bBullet\b", style_name, re.IGNORECASE):
        m = re.search(r"(\d+)\s*$", style_name)
        level = max(0, int(m.group(1)) - 1) if m else 0
        return "  " * level + "- "

    # Explicit Word numbered list styles
    if re.search(r"\bNumber\b", style_name, re.IGNORECASE):
        m = re.search(r"(\d+)\s*$", style_name)
        level = max(0, int(m.group(1)) - 1) if m else 0
        return "  " * level + "1. "

    # List Paragraph: inspect numPr for bullet/numbered info
    if style_name == "List Paragraph":
        pPr = para._p.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                ilvl_el = numPr.find(qn("w:ilvl"))
                level = int(ilvl_el.get(qn("w:val"), "0")) if ilvl_el is not None else 0
                return "  " * level + "- "

    return None


def para_text(para: DocxParagraph) -> str:
    """
    Return the plain text of a paragraph, normalising whitespace.
    Tab characters (used in heading numbering like "1.\tTitle") are replaced
    with a single space.
    """
    return re.sub(r"\s+", " ", para.text.replace("\t", " ")).strip()


def table_to_markdown(table: DocxTable) -> str:
    """
    Convert a python-docx Table to a GFM markdown table.

    Horizontally merged cells appear as repeated cell objects in python-docx.
    We deduplicate within each row using the underlying XML element identity.
    """
    if not table.rows:
        return ""

    rows_data: list[list[str]] = []
    for row in table.rows:
        seen: set[int] = set()
        cells: list[str] = []
        for cell in row.cells:
            cell_id = id(cell._tc)
            if cell_id in seen:
                cells.append("")   # placeholder for merged span
            else:
                seen.add(cell_id)
                text = re.sub(r"\s+", " ", cell.text.replace("\n", " ")).strip()
                cells.append(text)
        rows_data.append(cells)

    if not rows_data:
        return ""

    # Pad all rows to the same column count
    max_cols = max(len(r) for r in rows_data)
    rows_data = [r + [""] * (max_cols - len(r)) for r in rows_data]

    header = rows_data[0]
    sep    = ["---"] * max_cols
    lines  = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep)    + " |",
    ]
    for row in rows_data[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def generate_summary(text: str, title: str, client: anthropic.Anthropic) -> str:
    """Call Claude Haiku for a one-sentence chapter summary."""
    try:
        response = client.messages.create(
            model=SUMMARY_MODEL,
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": (
                    f"Chapter title: {title}\n\n"
                    f"Content excerpt:\n{text[:3000]}\n\n"
                    "Write exactly one sentence (under 30 words) summarising what this "
                    "chapter covers. Return only the sentence, no preamble."
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        console.print(f"[yellow]Warning: summary API call failed — {exc}[/yellow]")
        return "Summary unavailable."


def write_chapter_file(
    chapter_num: int,
    title: str,
    page_start: int,
    page_end: int,
    body_md: str,
    has_tables: bool,
    summary: str,
    output_dir: Path,
) -> Path:
    slug     = slugify(title) or f"chapter_{chapter_num:02d}"
    filename = output_dir / f"{chapter_num:02d}_{slug}.md"

    frontmatter = {
        "title":          title,
        "chapter_number": chapter_num,
        "page_start":     page_start,
        "page_end":       page_end,
        "has_tables":     has_tables,
        "summary":        summary,
    }
    yaml_block = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    content    = f"---\n{yaml_block}---\n\n# {title}\n\n{body_md.strip()}\n"
    filename.write_text(content, encoding="utf-8")
    return filename


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_docx(docx_path: Path) -> list[dict]:
    """
    Walk the document body and split into chapters at every Heading 1.

    Returns a list of chapter dicts:
        title       — str   (cleaned heading text)
        page_start  — int
        page_end    — int
        lines       — list[str]  (markdown lines for the body)
        has_tables  — bool
        word_count  — int
    """
    doc = Document(str(docx_path))

    chapters: list[dict]    = []
    current:  dict | None   = None
    current_lines: list[str] = []
    current_page = 1

    def new_chapter(title: str) -> dict:
        return {
            "title":      title,
            "page_start": current_page,
            "page_end":   current_page,
            "lines":      [],
            "has_tables": False,
        }

    def close_current() -> None:
        nonlocal current, current_lines
        if current is not None:
            # Collapse runs of blank lines to a single blank
            body = "\n".join(current_lines)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            current["lines"] = body.splitlines()
            current["word_count"] = len(body.split())
            chapters.append(current)
        current       = None
        current_lines = []

    for item in iter_block_items(doc):

        # ── TABLE ─────────────────────────────────────────────────────────────
        if isinstance(item, DocxTable):
            if current is None:
                current = new_chapter("Preliminary Content")
            md = table_to_markdown(item)
            if md:
                current_lines.append("")
                current_lines.append(md)
                current_lines.append("")
                current["has_tables"] = True
            continue

        # ── PARAGRAPH ─────────────────────────────────────────────────────────
        para: DocxParagraph = item
        style_name = para.style.name if para.style else "Normal"
        text       = para_text(para)

        # Track page breaks BEFORE processing this paragraph's content
        current_page += page_breaks_in_para(para._p)
        if current is not None:
            current["page_end"] = current_page

        # Skip TOC / index entries entirely
        if style_name in SKIP_STYLES:
            continue

        # ── Heading 1 → new chapter file ──────────────────────────────────────
        # Skip blank Heading 1 paragraphs (spurious empty headings in the source doc)
        if style_name == "Heading 1":
            if not text:
                continue
            close_current()
            current       = new_chapter(text)
            current_lines = []
            continue

        # ── Sub-headings (Heading 2–6) → ## / ### within current chapter ─────
        if style_name in HEADING_STYLES:
            prefix = HEADING_STYLES[style_name]  # "##", "###", etc.
            if text:
                if current is None:
                    current = new_chapter("Preliminary Content")
                current_lines.append("")
                current_lines.append(f"{prefix} {text}")
                current_lines.append("")
            continue

        # ── List items ────────────────────────────────────────────────────────
        marker = list_marker(para)
        if marker is not None:
            if text:
                if current is None:
                    current = new_chapter("Preliminary Content")
                current_lines.append(f"{marker}{text}")
            continue

        # ── Body text (Normal, Body Text, Caption, Quote, etc.) ──────────────
        if text:
            if current is None:
                current = new_chapter("Preliminary Content")
            current_lines.append(text)
        else:
            # Blank paragraph → paragraph separator
            current_lines.append("")

    close_current()
    return chapters


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(chapters: list[dict]) -> None:
    console.print("\n")
    console.rule("[bold green]Extraction Report[/bold green]")

    tbl = RichTable(box=box.SIMPLE_HEAD, show_lines=False, expand=False)
    tbl.add_column("File",   style="cyan",    min_width=30, max_width=55)
    tbl.add_column("Pages",  style="default", justify="center", min_width=10)
    tbl.add_column("Words",  style="default", justify="right",  min_width=8)
    tbl.add_column("Tables", style="default", justify="center", min_width=7)

    low_confidence: list[tuple[int, str]] = []

    for i, ch in enumerate(chapters, start=1):
        slug  = slugify(ch["title"]) or f"chapter_{i:02d}"
        fname = f"{i:02d}_{slug}.md"
        pages = f"{ch['page_start']}-{ch['page_end']}"
        wc    = ch.get("word_count", 0)
        tbl.add_row(fname, pages, f"{wc:,}", "yes" if ch["has_tables"] else "no")
        if wc < LOW_WORD_COUNT_THRESHOLD:
            low_confidence.append((i, fname))

    console.print(tbl)

    console.print("\n")
    console.rule("[bold yellow]Low-Confidence Chapters[/bold yellow]")
    if low_confidence:
        console.print(
            f"[yellow](!!) {len(low_confidence)} chapter(s) have fewer than "
            f"{LOW_WORD_COUNT_THRESHOLD} words:[/yellow]"
        )
        for num, fname in low_confidence:
            console.print(f"  {num:02d}. {fname}")
        console.print(
            "  [dim]Review these — they may be stub sections or "
            "image-only pages.[/dim]\n"
        )
    else:
        console.print("[green](OK) No low-confidence chapters detected.[/green]\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold blue]EU Wildlife Trade Reference Guide — DOCX Extractor[/bold blue]")

    if not DOCX_PATH.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: [cyan]{DOCX_PATH}[/cyan]")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print(
            "[bold yellow]Warning:[/bold yellow] ANTHROPIC_API_KEY not set -- "
            "summaries will be skipped."
        )
    claude = anthropic.Anthropic(api_key=api_key) if api_key else None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Extract ────────────────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Reading [cyan]{DOCX_PATH.name}[/cyan]...[/bold cyan]")
    chapters = extract_docx(DOCX_PATH)
    console.print(f"  Extracted [bold]{len(chapters)}[/bold] chapters.\n")

    # ── 2. Write markdown files ───────────────────────────────────────────────
    console.print("[bold cyan]Writing markdown files...[/bold cyan]")

    for i, ch in enumerate(chapters, start=1):
        title     = ch["title"]
        body_text = "\n".join(ch["lines"])

        if claude:
            console.print(
                f"  Summarising {i}/{len(chapters)}: [italic]{title[:65]}[/italic]"
            )
            summary = generate_summary(body_text, title, claude)
            time.sleep(0.3)
        else:
            summary = "Summary unavailable (no API key)."

        path = write_chapter_file(
            chapter_num=i,
            title=title,
            page_start=ch["page_start"],
            page_end=ch["page_end"],
            body_md=body_text,
            has_tables=ch["has_tables"],
            summary=summary,
            output_dir=OUTPUT_DIR,
        )
        console.print(f"  [green]+[/green] {path.name}")

    # ── 3. Report ─────────────────────────────────────────────────────────────
    print_report(chapters)
    console.print(
        f"[bold green]Done.[/bold green] "
        f"Files written to [cyan]{OUTPUT_DIR}/[/cyan]"
    )


if __name__ == "__main__":
    main()
