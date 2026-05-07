#!/usr/bin/env python3
"""
fix_bullet_lists.py — Add a./b./c. lettering to consecutive lowercase-starting
paragraph blocks in output/ markdown files.

These arise from Word bullet-list items that the markdown converter rendered
as plain paragraphs (no bullet prefix). The docx lowerLetter lists were handled
by fix_lettered_lists.py; this script covers the remaining bullet-format lists.

Heuristic: a run of 2+ consecutive blocks (separated by blank lines) where each
block's first non-whitespace character is a lowercase letter, and the block is
not already labelled (a. / a) prefix) and is not a heading/table/bullet/footnote.

Usage:
    python fix_bullet_lists.py
"""

import re
import yaml
from pathlib import Path
from rich.console import Console
from rich.table import Table

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


# ── Block classification ───────────────────────────────────────────────────────

def is_already_lettered(block: str) -> bool:
    """True if block already starts with a letter prefix like 'a. ' or 'a) '."""
    return bool(re.match(r'^[a-z][.)]\s', block))


def is_skippable(block: str) -> bool:
    """True if block should never receive lettering (heading, table, bullet, etc.)."""
    s = block.strip()
    if not s:
        return True
    first = s[0]
    # Headings, tables, existing bullets, bold-only lines (**/***), footnotes, links
    if first in ('#', '|', '-', '*', '[', '!', '`', '~', '>'):
        return True
    # Already lettered
    if is_already_lettered(s):
        return True
    # Numbered list items: "1. " or "1) "
    if re.match(r'^\d+[.)]\s', s):
        return True
    # All-uppercase first word (e.g. "NOTE:", "WARNING:") — skip
    first_word = s.split()[0].rstrip(':.,')
    if first_word.isupper() and len(first_word) > 1:
        return True
    return False


def starts_with_lower(block: str) -> bool:
    """True if block starts with a lowercase letter (after stripping) and is eligible."""
    s = block.strip()
    if not s or is_skippable(s):
        return False
    return bool(re.match(r'^[a-z]', s))


# ── Core processor ────────────────────────────────────────────────────────────

def process_body(body: str) -> tuple[str, int]:
    """
    Return (new_body, groups_applied).
    Splits on blank lines, finds runs of lowercase-starting blocks, letters them.
    """
    # Split into alternating [content, separator, content, …]
    raw = re.split(r'(\n\n+)', body)
    contents = raw[0::2]
    seps     = raw[1::2]

    letters = "abcdefghijklmnopqrstuvwxyz"
    groups_applied = 0

    result_contents: list[str] = []
    i = 0
    while i < len(contents):
        block = contents[i]
        if starts_with_lower(block):
            # Collect the full run
            run: list[int] = [i]
            j = i + 1
            while j < len(contents) and starts_with_lower(contents[j]):
                run.append(j)
                j += 1

            if len(run) >= 2:
                # Apply lettering
                for k, idx in enumerate(run):
                    letter = letters[k % 26]
                    result_contents.append(f"{letter}. {contents[idx].strip()}")
                groups_applied += 1
                i = j
                continue

        # Not a qualifying run — keep as-is
        result_contents.append(contents[i])
        i += 1

    # Re-interleave with separators
    out: list[str] = []
    for j, c in enumerate(result_contents):
        out.append(c)
        if j < len(seps):
            out.append(seps[j])
    return "".join(out), groups_applied


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold]fix_bullet_lists.py[/bold]")

    table = Table(show_header=True, header_style="bold white on black",
                  show_lines=True, width=90)
    table.add_column("File",   width=55)
    table.add_column("Groups", width=7, justify="right")
    table.add_column("Status", width=8)

    total_groups = 0
    files_written = 0

    for p in sorted(OUTPUT_DIR.glob("*.md")):
        if p.name in SKIP_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_fm(text)
        if not meta:
            continue
        if "sub_pages" in meta:
            continue

        new_body, groups = process_body(body)

        if groups == 0:
            continue

        p.write_text(make_file(meta, new_body), encoding="utf-8")
        total_groups += groups
        files_written += 1
        table.add_row(p.name[:54], str(groups), "[green]OK[/green]")

    console.print(table)
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{total_groups} groups lettered across {files_written} files."
    )


if __name__ == "__main__":
    main()
