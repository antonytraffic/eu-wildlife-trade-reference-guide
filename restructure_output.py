#!/usr/bin/env python3
"""
restructure_output.py — Apply structural changes to output/ markdown files.

Changes applied:
  1. Replace 'Chapter' -> 'Section' everywhere
  2. Add exclude_from_nav: true to preliminary content; create _footer_content.md
  3. Renumber sections 1-12; rename files accordingly
  4. Strip leading numbers from titles
  5. Split Section 3 and Section 4 into parent + sub-pages
  6. Consolidate 18 Annex files under a single annexes.md parent
"""

import re
import yaml
import shutil
from pathlib import Path
from slugify import slugify
from rich.console import Console
from rich.table import Table

OUTPUT_DIR = Path("output")
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


# ── Text helpers ──────────────────────────────────────────────────────────────

def strip_num_prefix(title: str) -> str:
    """Remove '3. ', '3.1 ', '12. ', '4.2.1. ' from the start of a title."""
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title).strip()


def chapter_to_section(text: str) -> str:
    """Replace Chapter -> Section, chapter -> section."""
    text = re.sub(r"\bChapter\b", "Section", text)
    text = re.sub(r"\bchapter\b", "section", text)
    return text


def strip_h1_prefix(body: str) -> str:
    """Remove leading number from the first # heading in body."""
    return re.sub(
        r"^(# )\d+(?:\.\d+)*\.?\s+",
        r"\1",
        body,
        count=1,
        flags=re.MULTILINE,
    )


def demote_headings(text: str) -> str:
    """Shift ## -> #, ### -> ##, #### -> ### etc. (used inside split sub-pages)."""
    lines = text.split("\n")
    result = []
    for line in lines:
        m = re.match(r"^(#{2,6})([ \t].*|$)", line)
        if m:
            new_hashes = "#" * (len(m.group(1)) - 1)
            line = new_hashes + m.group(2)
        result.append(line)
    return "\n".join(result)


def slug_from_filename(fname: str) -> str:
    """Extract slug from existing filename by stripping numeric prefix.
    '02_1_how_do_i_use_this_guide.md' -> 'how_do_i_use_this_guide'
    '12_how_are_cites_duties...md'    -> 'how_are_cites_duties...'
    """
    stem = Path(fname).stem
    m = re.match(r"^\d+(?:_\d+)*_(.+)$", stem)
    return m.group(1) if m else stem


def make_sub_slug(parent_num: int, sub_num: int, title: str) -> str:
    s = slugify(title, separator="_", max_length=50)
    return f"{parent_num:02d}_{sub_num}_{s}"


# ── Split body on ## headings ─────────────────────────────────────────────────

def split_by_h2(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (intro_text, [(h2_title, h2_body), ...])."""
    pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(pattern.finditer(body))
    if not matches:
        return body.strip(), []

    intro = body[: matches[0].start()].strip()
    sections = []
    for i, m in enumerate(matches):
        h2_title = m.group(1)
        content_start = body.index("\n", m.start()) + 1
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        h2_body = body[content_start:content_end].strip()
        sections.append((h2_title, h2_body))
    return intro, sections


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    report_rows: list[dict] = []  # for final rich table

    # ── Identify files ────────────────────────────────────────────────────────
    all_md = sorted(OUTPUT_DIR.glob("*.md"))
    preliminary = OUTPUT_DIR / "01_preliminary_content.md"

    # Files to split (by old filename)
    SPLIT_OLD = {
        "04_3_what_are_the_rules_governing_trade_into_and_from_the_eu_fo.md": 3,
        "05_4_what_rules_govern_internal_eu_trade.md": 4,
    }

    annex_files = sorted(
        f for f in all_md
        if re.match(r"\d+_annex_[ivxlcdm]+\.md$", f.name)
    )

    simple_files = [
        f for f in all_md
        if f.name != "01_preliminary_content.md"
        and f.name not in SPLIT_OLD
        and f not in annex_files
        and not f.name.startswith("_")
    ]

    # New section numbering for simple + split files (2-13 old -> 1-12 new)
    ordered_content = sorted(
        [f for f in all_md
         if f.name != "01_preliminary_content.md"
         and f not in annex_files
         and not f.name.startswith("_")],
        key=lambda f: f.name
    )
    old_to_new_num: dict[str, int] = {}
    for i, f in enumerate(ordered_content, 1):
        old_to_new_num[f.name] = i

    console.rule("[bold]Restructuring output/ markdown files[/bold]")

    # ── Step 1: Preliminary content ───────────────────────────────────────────
    console.print("\n[bold cyan]Step 1:[/bold cyan] Updating preliminary content")
    text = preliminary.read_text(encoding="utf-8")
    meta, body = parse_fm(text)
    meta.pop("chapter_number", None)
    meta["section_number"] = 0
    meta["exclude_from_nav"] = True
    body = chapter_to_section(body)
    preliminary.write_text(make_file(meta, body), encoding="utf-8")
    console.print(f"  [green]+[/green] Updated {preliminary.name} (exclude_from_nav: true)")
    report_rows.append({"num": "—", "file": preliminary.name, "title": "Preliminary Content",
                         "parent": "", "children": "", "note": "excluded from nav"})

    # ── Step 2: Footer content file ───────────────────────────────────────────
    console.print("\n[bold cyan]Step 2:[/bold cyan] Creating _footer_content.md")
    footer_path = OUTPUT_DIR / "_footer_content.md"
    footer_text = (
        "November 2025\n\n"
        "This is a revised and updated version, based on the previous edition of the Reference Guide "
        "to the European Union Wildlife Trade Regulations originally produced in 1998 by the European "
        "Commission, TRAFFIC Europe and WWF.\n\n"
        "This document does not necessarily represent the opinion of the European Commission and is not "
        "a legal interpretation of European Union legislation.\n\n"
        "The contents of this document may be freely reproduced provided that the source is adequately "
        "recorded: European Commission and TRAFFIC (2025). Reference Guide to the European Union Wildlife "
        "Trade Regulations. Brussels, Belgium.\n\n"
        "More details and information relating to the implementation and enforcement of CITES and the EU "
        "Wildlife Trade Regulations can be found on the website of the European Commission or by "
        "contacting the relevant authorities in EU Member States."
    )
    footer_path.write_text(footer_text, encoding="utf-8")
    console.print(f"  [green]+[/green] Created _footer_content.md")

    # ── Step 3: Simple section files (renumber, rename, strip title) ──────────
    console.print("\n[bold cyan]Step 3:[/bold cyan] Processing simple sections")
    for old_file in simple_files:
        new_num = old_to_new_num[old_file.name]
        text = old_file.read_text(encoding="utf-8")
        meta, body = parse_fm(text)

        meta.pop("chapter_number", None)
        meta["section_number"] = new_num

        title = strip_num_prefix(meta.get("title", ""))
        meta["title"] = title

        body = chapter_to_section(body)
        body = strip_h1_prefix(body)

        slug = slug_from_filename(old_file.name)
        new_fname = f"{new_num:02d}_{slug}.md"
        new_path = OUTPUT_DIR / new_fname

        new_path.write_text(make_file(meta, body), encoding="utf-8")
        if new_path != old_file:
            old_file.unlink()

        console.print(f"  [green]+[/green] {old_file.name} -> {new_fname}")
        report_rows.append({"num": str(new_num), "file": new_fname, "title": title,
                             "parent": "", "children": "", "note": ""})

    # ── Step 4: Split Section 3 and Section 4 ────────────────────────────────
    console.print("\n[bold cyan]Step 4:[/bold cyan] Splitting Sections 3 and 4")
    for old_fname, new_num in SPLIT_OLD.items():
        old_path = OUTPUT_DIR / old_fname
        text = old_path.read_text(encoding="utf-8")
        meta, body = parse_fm(text)

        meta.pop("chapter_number", None)
        meta["section_number"] = new_num

        title = strip_num_prefix(meta.get("title", ""))
        meta["title"] = title

        body = chapter_to_section(body)
        body = strip_h1_prefix(body)

        intro, sections = split_by_h2(body)
        parent_slug_body = slug_from_filename(old_fname)
        parent_slug = f"{new_num:02d}_{parent_slug_body}"
        parent_fname = f"{parent_slug}.md"
        parent_path = OUTPUT_DIR / parent_fname

        # Build sub-page slugs
        sub_slugs = []
        for i, (h2_title, _) in enumerate(sections, 1):
            clean = strip_num_prefix(h2_title)
            sub_slugs.append(make_sub_slug(new_num, i, clean))

        # Parent page
        parent_meta = dict(meta)
        parent_meta["sub_pages"] = sub_slugs
        parent_meta["has_tables"] = "|" in intro

        link_list = "\n".join(
            f"- [{strip_num_prefix(h2_title)}]({sub_slugs[i-1]}.md)"
            for i, (h2_title, _) in enumerate(sections, 1)
        )
        parent_body = intro + "\n\n## Sub-sections\n\n" + link_list + "\n"
        parent_path.write_text(make_file(parent_meta, parent_body), encoding="utf-8")
        console.print(f"  [green]+[/green] {old_fname} -> {parent_fname} (parent, {len(sections)} sub-pages)")
        report_rows.append({"num": str(new_num), "file": parent_fname, "title": title,
                             "parent": "", "children": str(len(sections)), "note": "parent"})

        # Sub-pages
        for i, (h2_title, h2_body) in enumerate(sections, 1):
            clean_title = strip_num_prefix(h2_title)
            sub_slug = sub_slugs[i - 1]
            sub_fname = f"{sub_slug}.md"
            sub_path = OUTPUT_DIR / sub_fname

            sub_meta = {
                "section_number": new_num,
                "title": clean_title,
                "parent": parent_slug,
                "sub_section": h2_title,
                "has_tables": "|" in h2_body,
                "summary": meta.get("summary", ""),
            }

            sub_body = f"# {clean_title}\n\n{demote_headings(h2_body)}\n"
            sub_path.write_text(make_file(sub_meta, sub_body), encoding="utf-8")
            console.print(f"    [dim]+[/dim] {sub_fname}")
            report_rows.append({"num": f"{new_num}.{i}", "file": sub_fname, "title": clean_title,
                                 "parent": parent_slug, "children": "", "note": "sub-page"})

        old_path.unlink()

    # ── Step 5: Consolidate Annexes ───────────────────────────────────────────
    console.print("\n[bold cyan]Step 5:[/bold cyan] Consolidating Annexes")
    roman_re = re.compile(r"\d+_annex_([ivxlcdm]+)\.md$")

    annex_entries = []
    for afile in annex_files:
        m = roman_re.match(afile.name)
        if not m:
            continue
        roman = m.group(1)
        text = afile.read_text(encoding="utf-8")
        ameta, abody = parse_fm(text)
        annex_entries.append((roman, ameta, abody, afile))

    annex_sub_slugs = [f"annexes_{roman}" for roman, _, _, _ in annex_entries]

    # Record annexes parent row first so it appears before the sub-pages
    report_rows.append({"num": "13", "file": "annexes.md", "title": "Annexes",
                         "parent": "", "children": str(len(annex_entries)), "note": "parent"})

    # Write individual annex files
    for roman, ameta, abody, old_afile in annex_entries:
        new_fname = f"annexes_{roman}.md"
        new_path = OUTPUT_DIR / new_fname

        ameta.pop("chapter_number", None)
        ameta["section_number"] = 13
        ameta["parent"] = "annexes"
        abody = chapter_to_section(abody)

        new_path.write_text(make_file(ameta, abody), encoding="utf-8")
        if new_path != old_afile:
            old_afile.unlink()

        console.print(f"  [dim]+[/dim] {old_afile.name} -> {new_fname}")
        report_rows.append({"num": "13.*", "file": new_fname, "title": ameta.get("title", ""),
                             "parent": "annexes", "children": "", "note": "annex sub-page"})

    # Write annexes parent
    annex_link_list = "\n".join(
        f"- [{ameta.get('title', f'Annex {roman.upper()}')}](annexes_{roman}.md)"
        for roman, ameta, _, _ in annex_entries
    )
    annexes_meta = {
        "section_number": 13,
        "title": "Annexes",
        "has_tables": False,
        "summary": "Reference annexes to the EU Wildlife Trade Regulations.",
        "sub_pages": annex_sub_slugs,
    }
    annexes_body = (
        "# Annexes\n\n"
        "This section contains the reference annexes to the EU Wildlife Trade Regulations Guide.\n\n"
        + annex_link_list + "\n"
    )
    annexes_path = OUTPUT_DIR / "annexes.md"
    annexes_path.write_text(make_file(annexes_meta, annexes_body), encoding="utf-8")
    console.print(f"  [green]+[/green] Created annexes.md (parent, {len(annex_entries)} annexes)")

    # ── Final report ──────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold]Final structure report[/bold]")

    table = Table(show_header=True, header_style="bold white on black",
                  show_lines=True, width=110)
    table.add_column("Sec", width=6, justify="right")
    table.add_column("File", width=52)
    table.add_column("Title", width=36)
    table.add_column("Parent", width=10)
    table.add_column("Note", width=14)

    for r in report_rows:
        is_parent = r["note"] == "parent"
        is_sub    = r["note"] in ("sub-page", "annex sub-page")
        sec_style = "bold cyan" if is_parent else ("dim" if is_sub else "")
        file_style = "bold" if is_parent else ("dim" if is_sub else "")
        indent = "  " if is_sub else ""
        table.add_row(
            f"[{sec_style}]{r['num']}[/{sec_style}]" if sec_style else r["num"],
            f"[{file_style}]{indent}{r['file']}[/{file_style}]" if file_style else f"{indent}{r['file']}",
            r["title"][:35],
            r["parent"][:10] if r["parent"] else "",
            r["note"],
        )

    console.print(table)

    total_files = len(list(OUTPUT_DIR.glob("*.md")))
    console.print(f"\n[bold green]Done.[/bold green] {total_files} .md files in output/")


if __name__ == "__main__":
    main()
