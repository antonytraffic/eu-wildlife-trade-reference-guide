#!/usr/bin/env python3
"""
fix_split_tables.py — Merge GFM tables split across pages in the Word source.

When Word's table-to-markdown conversion repeats the header row at each page
break, consecutive table blocks (separated only by blank lines) with identical
first rows are merged into a single table.

The duplicate header and separator rows from the second block are removed;
all data rows are kept in order.

Usage:
    python fix_split_tables.py
"""

import yaml
from pathlib import Path
from rich.console import Console
from rich.table import Table as RichTable

OUTPUT_DIR = Path("output")
SKIP_FILES = {"_footer_content.md", "annexes.md", "01_preliminary_content.md", "_summaries.json"}

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


# ── Merge logic ───────────────────────────────────────────────────────────────

def is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def is_blank(line: str) -> bool:
    return line.strip() == ""


def merge_split_tables(body: str) -> tuple[str, int]:
    """
    Scan the body for consecutive GFM table blocks separated only by blank
    lines whose first (header) rows are identical.  Merge each such pair by
    removing the blank gap, duplicate header, and duplicate separator from the
    second block.

    Returns (new_body, number_of_merges).
    """
    lines = body.splitlines(keepends=True)
    merges = 0
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if not is_table_line(line):
            out.append(line)
            i += 1
            continue

        # ── Collect table block ──────────────────────────────────────────────
        table_lines: list[str] = []
        while i < len(lines) and is_table_line(lines[i]):
            table_lines.append(lines[i])
            i += 1

        if len(table_lines) < 2:
            out.extend(table_lines)
            continue

        # ── Try to merge with the immediately following table ────────────────
        while True:
            # Peek past blank lines
            j = i
            while j < len(lines) and is_blank(lines[j]):
                j += 1

            # Is there a table right after the blanks?
            if j >= len(lines) or not is_table_line(lines[j]):
                break  # non-blank non-table content or end of file

            # Collect next table block
            next_table: list[str] = []
            k = j
            while k < len(lines) and is_table_line(lines[k]):
                next_table.append(lines[k])
                k += 1

            if len(next_table) < 2:
                break

            # Compare header rows (first line of each block)
            if table_lines[0].rstrip() != next_table[0].rstrip():
                break  # different tables

            # Same header → merge: drop blank gap, drop next_table's header+sep
            # (next_table[0] = duplicate header, next_table[1] = duplicate separator)
            table_lines = table_lines + next_table[2:]
            i = k
            merges += 1

        out.extend(table_lines)

    return "".join(out), merges


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold]fix_split_tables.py[/bold]")

    report = RichTable(
        show_header=True, header_style="bold white on black",
        show_lines=True, width=80,
    )
    report.add_column("File",    width=55)
    report.add_column("Merges",  width=7,  justify="right")
    report.add_column("Status",  width=8)

    total_merges = 0
    files_written = 0

    for p in sorted(OUTPUT_DIR.glob("*.md")):
        if p.name in SKIP_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_fm(text)
        if not meta or "sub_pages" in meta:
            continue

        new_body, merges = merge_split_tables(body)
        if merges == 0:
            continue

        p.write_text(make_file(meta, new_body), encoding="utf-8")
        total_merges += merges
        files_written += 1
        report.add_row(p.name[:54], str(merges), "[green]OK[/green]")

    console.print(report)
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{total_merges} table(s) merged across {files_written} file(s)."
    )


if __name__ == "__main__":
    main()
