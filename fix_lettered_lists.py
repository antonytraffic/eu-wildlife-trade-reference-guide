#!/usr/bin/env python3
"""
fix_lettered_lists.py — Restore lowerLetter list labels in output/ markdown files.

Reads the source .docx, groups all paragraphs whose numFmt is lowerLetter,
fuzzy-matches each group's first item against paragraphs in the output/ markdown
files, then prepends a., b., c. … labels.

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

DOCX_PATH  = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.docx")
OUTPUT_DIR = Path("output")
CONFIDENCE_MIN = 72   # token_set_ratio threshold

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

console = Console()


# ── Namespace helper ──────────────────────────────────────────────────────────

def wn(tag: str) -> str:
    return f"{{{W}}}{tag}"


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


# ── Docx parsing ──────────────────────────────────────────────────────────────

def build_num_map(zip_obj: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    """Return {numId: {ilvl: numFmt}} from numbering.xml."""
    xml = zip_obj.read("word/numbering.xml")
    root = etree.fromstring(xml)

    abstract: dict[str, dict[str, str]] = {}
    for an in root.findall(wn("abstractNum")):
        aid = an.get(wn("abstractNumId"))
        lvls: dict[str, str] = {}
        for lvl in an.findall(wn("lvl")):
            ilvl = lvl.get(wn("ilvl"))
            nf = lvl.find(wn("numFmt"))
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


def extract_lettered_groups(docx_path: Path) -> list[list[str]]:
    """Return [[item_text, …], …] — one sub-list per consecutive lowerLetter group."""
    with zipfile.ZipFile(str(docx_path)) as z:
        num_map = build_num_map(z)
        doc_xml = z.read("word/document.xml")

    doc_root = etree.fromstring(doc_xml)
    body = doc_root.find(f".//{wn('body')}")

    groups: list[list[str]] = []
    cur_group: list[str] = []
    cur_nid: str | None = None
    cur_il: str | None = None

    def flush():
        nonlocal cur_group, cur_nid, cur_il
        if cur_group:
            groups.append(cur_group)
        cur_group = []
        cur_nid = None
        cur_il = None

    for el in body:
        tag = etree.QName(el).localname
        if tag != "p":
            flush()
            continue

        np = el.find(f".//{wn('numPr')}")
        if np is None:
            # Empty paragraphs are common spacers between Word list items.
            # Allow them to pass without flushing an active group.
            if cur_group and not para_plain_text(el):
                continue
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

        if fmt == "lowerLetter":
            if cur_nid == nid and cur_il == il:
                cur_group.append(para_plain_text(el))
            else:
                flush()
                cur_group = [para_plain_text(el)]
                cur_nid = nid
                cur_il = il
        else:
            flush()

    flush()
    return [g for g in groups if len(g) >= 2]  # single-item "lists" are not real lists


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
    """Strip markdown markup for fuzzy comparison."""
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[\^[^\]]+\]", "", s)
    return s.strip()


def md_paragraphs(body: str) -> list[tuple[int, str]]:
    """Return [(block_index, stripped_text), …] — one entry per non-empty block."""
    blocks = re.split(r"\n\n+", body)
    result = []
    for i, block in enumerate(blocks):
        clean = strip_md(block.strip())
        # Strip leading list markers for comparison
        clean = re.sub(r"^[-*]\s+", "", clean)
        clean = re.sub(r"^\d+\.\s+", "", clean)
        clean = re.sub(r"^[a-z]\.\s+", "", clean)
        if clean:
            result.append((i, clean))
    return result


# ── Core matching and patching ────────────────────────────────────────────────

def find_best_file(group: list[str], md_files: list[dict]) -> tuple[dict | None, int, float]:
    """
    Find the markdown file and block index where group[0] best matches.
    Returns (md_file, block_idx, confidence).
    """
    first_clean = strip_md(group[0])
    if len(first_clean) < 15:
        return None, -1, 0.0

    best_file = None
    best_idx  = -1
    best_score = 0.0

    for mf in md_files:
        paras = md_paragraphs(mf["body"])
        texts = [t for _, t in paras]
        if not texts:
            continue
        result = rfprocess.extractOne(
            first_clean, texts, scorer=fuzz.token_set_ratio
        )
        if result is None:
            continue
        _, score, local_idx = result
        if score > best_score:
            best_score = score
            best_file  = mf
            best_idx   = paras[local_idx][0]   # block index in the body

    return best_file, best_idx, best_score


def patch_file(mf: dict, group: list[str], block_idx: int) -> int:
    """
    In mf["body"], replace N blocks starting at block_idx with a., b., c. …
    Returns number of items patched.
    """
    blocks = re.split(r"(\n\n+)", mf["body"])
    # Rebuild as a list of alternating content/separator blocks
    # A simpler approach: split only on blank lines
    raw_blocks = re.split(r"\n\n+", mf["body"])

    n = len(group)
    if block_idx + n > len(raw_blocks):
        n = len(raw_blocks) - block_idx

    letters = "abcdefghijklmnopqrstuvwxyz"
    for i in range(n):
        block = raw_blocks[block_idx + i].strip()
        # Strip any existing list prefix
        block = re.sub(r"^[-*]\s+", "", block)
        block = re.sub(r"^\d+\.\s+", "", block)
        block = re.sub(r"^[a-z]\.\s+", "", block)
        raw_blocks[block_idx + i] = f"{letters[i]}. {block}"

    mf["body"] = "\n\n".join(raw_blocks)
    return n


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold]fix_lettered_lists.py[/bold]")

    if not DOCX_PATH.exists():
        console.print(f"[red]DOCX not found: {DOCX_PATH}[/red]")
        return

    # Step 1: extract lowerLetter groups from docx
    console.print("\n[bold cyan]Step 1:[/bold cyan] Extracting lowerLetter groups from docx …")
    groups = extract_lettered_groups(DOCX_PATH)
    console.print(f"  Found [bold]{len(groups)}[/bold] lettered list groups "
                  f"({sum(len(g) for g in groups)} total items)")

    # Step 2: load markdown files
    console.print("\n[bold cyan]Step 2:[/bold cyan] Loading output/ markdown files …")
    md_files = load_md_files()
    console.print(f"  {len(md_files)} files to search")

    # Step 3: match and patch
    console.print("\n[bold cyan]Step 3:[/bold cyan] Matching and patching …\n")

    table = Table(show_header=True, header_style="bold white on black",
                  show_lines=True, width=110)
    table.add_column("File",          width=42)
    table.add_column("First item",    width=38)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Items", width=5, justify="right")
    table.add_column("Status", width=8)

    stats: dict[str, int] = {}   # path -> items_patched
    skipped = applied = 0

    for group in groups:
        mf, block_idx, conf = find_best_file(group, md_files)

        first_preview = group[0][:36] + ("…" if len(group[0]) > 36 else "")
        fname = mf["path"].name[:41] if mf else "—"

        if mf is None or conf < CONFIDENCE_MIN:
            table.add_row(
                fname, first_preview, str(int(conf)),
                str(len(group)), "[yellow]SKIP[/yellow]"
            )
            skipped += 1
            continue

        patched = patch_file(mf, group, block_idx)
        stats[str(mf["path"])] = stats.get(str(mf["path"]), 0) + patched
        applied += 1

        table.add_row(
            fname, first_preview, str(int(conf)),
            str(patched), "[green]OK[/green]"
        )

    console.print(table)

    # Step 4: write updated files
    written = 0
    for mf in md_files:
        path = str(mf["path"])
        if path in stats:
            mf["path"].write_text(
                make_file(mf["meta"], mf["body"]), encoding="utf-8"
            )
            written += 1

    console.print(f"\n[bold green]Done.[/bold green] "
                  f"{applied} groups applied · {skipped} skipped · "
                  f"{written} files updated")

    # Step 5: verify Section 10
    console.print("\n[bold cyan]Verification:[/bold cyan] Section 10 — Article 16 list")
    sec10 = OUTPUT_DIR / "10_how_are_the_regulations_enforced.md"
    if sec10.exists():
        _, body = parse_fm(sec10.read_text(encoding="utf-8"))
        idx = body.find("Article 16 is one of the most significant")
        if idx != -1:
            snippet = body[idx: idx + 600]
            # Find first a. item
            m = re.search(r'^a\. .+', snippet, re.MULTILINE)
            if m:
                console.print(f"  [green]+[/green] Found: {m.group()[:80]}")
            else:
                console.print("  [yellow]![/yellow] No 'a.' item found after Article 16 paragraph")
        else:
            console.print("  [yellow]![/yellow] Article 16 paragraph not found in Section 10")
    else:
        console.print(f"  [yellow]![/yellow] {sec10.name} not found")


if __name__ == "__main__":
    main()
