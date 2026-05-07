#!/usr/bin/env python3
"""
fix_lettered_lists.py — Restore list prefixes in output/ markdown files using
the source .docx as the authoritative list definition.

Handles three numFmt types extracted from word/numbering.xml:
  lowerLetter → a. b. c. …
  decimal     → 1. 2. 3. …
  bullet      → - (dash prefix, standardised)

Algorithm (two-pass):
  1. Extract all list groups from the docx and fuzzy-match each group's first
     item to a block in an output/ markdown file.
  2. Record exactly which block indices in each file should have list prefixes
     (only the N blocks belonging to the matched docx group).
  3. Apply the correct prefix to each matched block.
  4. Strip list prefixes from any block that is NOT part of a docx list —
     these are paragraphs incorrectly absorbed by a previous run.

Usage:
    python fix_lettered_lists.py
"""

import re
import zipfile
from pathlib import Path

import yaml
from lxml import etree
from rapidfuzz import fuzz, process as rfprocess
from rich.console import Console
from rich.table import Table

DOCX_PATH      = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.docx")
OUTPUT_DIR     = Path("output")
CONFIDENCE_MIN = 72

SUPPORTED_FMTS = {"lowerLetter", "bullet", "decimal"}

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
console = Console()

LETTERS = "abcdefghijklmnopqrstuvwxyz"
_LIST_PREFIX_RE = re.compile(r"^(?:[-*]\s+|\d+\.\s+|[a-z]\.\s+)")


# ── Namespace / YAML helpers ──────────────────────────────────────────────────

def wn(tag: str) -> str:
    return f"{{{W}}}{tag}"


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


# ── Docx parsing ──────────────────────────────────────────────────────────────

def build_num_map(zip_obj: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    xml  = zip_obj.read("word/numbering.xml")
    root = etree.fromstring(xml)

    abstract: dict[str, dict[str, str]] = {}
    for an in root.findall(wn("abstractNum")):
        aid  = an.get(wn("abstractNumId"))
        lvls: dict[str, str] = {}
        for lvl in an.findall(wn("lvl")):
            ilvl = lvl.get(wn("ilvl"))
            nf   = lvl.find(wn("numFmt"))
            if nf is not None:
                lvls[ilvl] = nf.get(wn("val"), "")
        abstract[aid] = lvls

    num_map: dict[str, dict[str, str]] = {}
    for num in root.findall(wn("num")):
        nid = num.get(wn("numId"))
        ref = num.find(wn("abstractNumId"))
        if ref is not None:
            aid = ref.get(wn("val"))
            num_map[nid] = abstract.get(aid, {})
    return num_map


def para_plain_text(p_el) -> str:
    return "".join(t.text or "" for t in p_el.findall(f".//{wn('t')}")).strip()


def extract_list_groups(docx_path: Path) -> list[tuple[str, list[str]]]:
    """Return [(numFmt, [item_text, …]), …] for groups with >= 2 items."""
    with zipfile.ZipFile(str(docx_path)) as z:
        num_map = build_num_map(z)
        doc_xml = z.read("word/document.xml")

    doc_root = etree.fromstring(doc_xml)
    body_el  = doc_root.find(f".//{wn('body')}")

    groups: list[tuple[str, list[str]]] = []
    cur_group: list[str] = []
    cur_nid: str | None  = None
    cur_il:  str | None  = None
    cur_fmt: str | None  = None

    def flush():
        nonlocal cur_group, cur_nid, cur_il, cur_fmt
        if cur_group and cur_fmt in SUPPORTED_FMTS:
            groups.append((cur_fmt, cur_group))
        cur_group = []
        cur_nid = cur_il = cur_fmt = None

    for el in body_el:
        tag = etree.QName(el).localname
        if tag != "p":
            flush()
            continue

        np = el.find(f".//{wn('numPr')}")
        if np is None:
            if cur_group and not para_plain_text(el):
                continue   # empty spacer between list items
            flush()
            continue

        numId_el = np.find(wn("numId"))
        ilvl_el  = np.find(wn("ilvl"))
        if numId_el is None or ilvl_el is None:
            flush()
            continue

        nid = numId_el.get(wn("val"))
        il  = ilvl_el.get(wn("val"))
        fmt = num_map.get(nid, {}).get(il, "")

        if fmt not in SUPPORTED_FMTS:
            flush()
            continue

        if cur_nid == nid and cur_il == il:
            cur_group.append(para_plain_text(el))
        else:
            flush()
            cur_group = [para_plain_text(el)]
            cur_nid, cur_il, cur_fmt = nid, il, fmt

    flush()
    return [(fmt, g) for fmt, g in groups if len(g) >= 2]


# ── Markdown file helpers ─────────────────────────────────────────────────────

SKIP_FILES = {"_footer_content.md", "annexes.md", "01_preliminary_content.md"}


def load_md_files() -> list[dict]:
    files = []
    for p in sorted(OUTPUT_DIR.glob("*.md")):
        if p.name in SKIP_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_fm(text)
        if not meta or "sub_pages" in meta:
            continue
        files.append({"path": p, "meta": meta, "body": body})
    return files


def strip_md(s: str) -> str:
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[\^[^\]]+\]", "", s)
    return s.strip()


def md_paragraphs(body: str) -> list[tuple[int, str]]:
    """Return [(block_index, stripped_text), …] for non-empty blocks."""
    blocks = re.split(r"\n\n+", body)
    result = []
    for i, block in enumerate(blocks):
        clean = strip_md(block.strip())
        clean = _LIST_PREFIX_RE.sub("", clean)
        if clean:
            result.append((i, clean))
    return result


# ── Matching ──────────────────────────────────────────────────────────────────

def find_best_file(group: list[str], md_files: list[dict]) -> tuple[dict | None, int, float]:
    first_clean = strip_md(group[0])
    if len(first_clean) < 15:
        return None, -1, 0.0

    best_file  = None
    best_idx   = -1
    best_score = 0.0

    for mf in md_files:
        paras = md_paragraphs(mf["body"])
        texts = [t for _, t in paras]
        if not texts:
            continue
        result = rfprocess.extractOne(first_clean, texts, scorer=fuzz.token_set_ratio)
        if result is None:
            continue
        _, score, local_idx = result
        if score > best_score:
            best_score = score
            best_file  = mf
            best_idx   = paras[local_idx][0]

    return best_file, best_idx, best_score


# ── Patching ──────────────────────────────────────────────────────────────────

def make_prefix(fmt: str, i: int) -> str:
    if fmt == "lowerLetter":
        return f"{LETTERS[i % 26]}. "
    if fmt == "decimal":
        return f"{i + 1}. "
    return "- "


def patch_file(mf: dict, block_idx: int, fmt: str, n: int) -> None:
    """Apply correct list prefix to exactly n blocks starting at block_idx."""
    raw_blocks = re.split(r"\n\n+", mf["body"])
    n = min(n, len(raw_blocks) - block_idx)
    for i in range(n):
        block = raw_blocks[block_idx + i].strip()
        block = _LIST_PREFIX_RE.sub("", block)
        raw_blocks[block_idx + i] = make_prefix(fmt, i) + block
    mf["body"] = "\n\n".join(raw_blocks)


def strip_excess_prefixes(mf: dict, valid: set[int]) -> int:
    """Strip list prefixes from blocks NOT in valid set. Returns count stripped."""
    raw_blocks = re.split(r"\n\n+", mf["body"])
    stripped = 0
    for i, block in enumerate(raw_blocks):
        s = block.strip()
        if i not in valid and _LIST_PREFIX_RE.match(s):
            raw_blocks[i] = _LIST_PREFIX_RE.sub("", s)
            stripped += 1
    if stripped:
        mf["body"] = "\n\n".join(raw_blocks)
    return stripped


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold]fix_lettered_lists.py[/bold]")

    if not DOCX_PATH.exists():
        console.print(f"[red]DOCX not found: {DOCX_PATH}[/red]")
        return

    console.print("\n[bold cyan]Step 1:[/bold cyan] Extracting list groups from docx …")
    groups = extract_list_groups(DOCX_PATH)
    by_fmt: dict[str, int] = {}
    for fmt, g in groups:
        by_fmt[fmt] = by_fmt.get(fmt, 0) + 1
    console.print(f"  Found [bold]{len(groups)}[/bold] groups: "
                  + ", ".join(f"{v} {k}" for k, v in sorted(by_fmt.items())))

    console.print("\n[bold cyan]Step 2:[/bold cyan] Loading output/ markdown files …")
    md_files = load_md_files()
    console.print(f"  {len(md_files)} files to search")

    # ── Phase 1: match all groups to file positions ───────────────────────────
    console.print("\n[bold cyan]Step 3:[/bold cyan] Matching groups to markdown files …\n")

    match_table = Table(show_header=True, header_style="bold white on black",
                        show_lines=True, width=120)
    match_table.add_column("File",       width=42)
    match_table.add_column("Type",       width=12)
    match_table.add_column("First item", width=40)
    match_table.add_column("Conf",       width=5, justify="right")
    match_table.add_column("Items",      width=5, justify="right")
    match_table.add_column("Status",     width=8)

    # valid_positions[file_path] = set of block indices that should have prefixes
    valid_positions: dict[str, set[int]] = {}
    confirmed: list[tuple[str, list[str], dict, int]] = []  # (fmt, group, mf, block_idx)
    skipped = applied = 0

    for fmt, group in groups:
        mf, block_idx, conf = find_best_file(group, md_files)
        first_preview = group[0][:38] + ("…" if len(group[0]) > 38 else "")
        fname = mf["path"].name[:41] if mf else "—"

        if mf is None or conf < CONFIDENCE_MIN:
            match_table.add_row(fname, fmt, first_preview, str(int(conf)),
                                str(len(group)), "[yellow]SKIP[/yellow]")
            skipped += 1
            continue

        n = min(len(group), len(re.split(r"\n\n+", mf["body"])) - block_idx)
        path = str(mf["path"])
        valid_positions.setdefault(path, set())
        for i in range(n):
            valid_positions[path].add(block_idx + i)

        confirmed.append((fmt, group, mf, block_idx))
        applied += 1
        match_table.add_row(fname, fmt, first_preview, str(int(conf)),
                             str(n), "[green]OK[/green]")

    console.print(match_table)

    # ── Phase 2: apply patches ────────────────────────────────────────────────
    stats: dict[str, int] = {}
    for fmt, group, mf, block_idx in confirmed:
        patch_file(mf, block_idx, fmt, len(group))
        stats[str(mf["path"])] = stats.get(str(mf["path"]), 0) + len(group)

    # ── Phase 3: strip excess prefixes ────────────────────────────────────────
    excess_table = Table(show_header=True, header_style="bold white on black",
                         show_lines=True, width=80)
    excess_table.add_column("File",      width=55)
    excess_table.add_column("Restored",  width=8,  justify="right")
    excess_table.add_column("Status",    width=8)

    total_restored = 0
    for mf in md_files:
        path = str(mf["path"])
        if path not in valid_positions:
            continue
        restored = strip_excess_prefixes(mf, valid_positions[path])
        if restored:
            total_restored += restored
            excess_table.add_row(mf["path"].name[:54], str(restored), "[cyan]FIXED[/cyan]")

    if total_restored:
        console.print("\n[bold cyan]Excess prefix cleanup:[/bold cyan]\n")
        console.print(excess_table)

    # ── Write updated files ───────────────────────────────────────────────────
    written = 0
    touched = set(stats.keys()) | {str(mf["path"]) for mf in md_files
                                    if str(mf["path"]) in valid_positions}
    for mf in md_files:
        if str(mf["path"]) in touched:
            mf["path"].write_text(make_file(mf["meta"], mf["body"]), encoding="utf-8")
            written += 1

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{applied} groups applied · {skipped} skipped · "
        f"{total_restored} excess prefixes stripped · {written} files updated"
    )


if __name__ == "__main__":
    main()
