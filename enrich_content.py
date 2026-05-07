#!/usr/bin/env python3
"""
enrich_content.py — Enrich output/ markdown files with bold/italic formatting,
hyperlinks, and footnotes extracted from the source .docx.

For each markdown file the script:
  1. Locates the corresponding section in the .docx by heading text
  2. Re-renders the prose content with bold/italic, hyperlinks, footnotes
  3. Tables already in the markdown are preserved (re-rendered from docx)
  4. Footnotes are renumbered per file and de-duplicated
  5. Writes the enriched file back to output/
  6. Prints a rich summary table
"""

import re
import zipfile
import yaml
from pathlib import Path
from lxml import etree
from rich.console import Console
from rich.table import Table

DOCX_PATH = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.docx")
OUTPUT_DIR = Path("output")
console = Console()

# ── XML namespace helpers ─────────────────────────────────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

def wn(tag: str) -> str:
    return f"{{{W}}}{tag}"

def rn(tag: str) -> str:
    return f"{{{R_NS}}}{tag}"

# ── YAML frontmatter helpers ──────────────────────────────────────────────────

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


# ══════════════════════════════════════════════════════════════════════════════
# DocxData: parse the .docx once into usable data structures
# ══════════════════════════════════════════════════════════════════════════════

class DocxData:
    def __init__(self, path: Path):
        with zipfile.ZipFile(path) as z:
            doc_xml  = z.read("word/document.xml")
            rels_xml = z.read("word/_rels/document.xml.rels")
            num_xml  = z.read("word/numbering.xml") if "word/numbering.xml" in z.namelist() else None
            fn_xml   = z.read("word/footnotes.xml") if "word/footnotes.xml" in z.namelist() else None

        doc_tree  = etree.fromstring(doc_xml)
        rels_tree = etree.fromstring(rels_xml)

        body = doc_tree.find(wn("body"))
        # All direct children of body: p, tbl, bookmarkStart, bookmarkEnd, sectPr
        self.body: list = list(body)

        # External hyperlink rels: rid -> url
        self.rels: dict[str, str] = {}
        for rel in rels_tree:
            if "hyperlink" in rel.get("Type", "").lower():
                self.rels[rel.get("Id", "")] = rel.get("Target", "")

        # Numbering: numId -> {ilvl_str: numFmt_str}
        self.numbering: dict[str, dict[str, str]] = {}
        if num_xml:
            self._parse_numbering(num_xml)

        # Footnotes: fn_id -> rendered_text (with basic formatting)
        self.footnotes: dict[str, str] = {}
        if fn_xml:
            self._parse_footnotes(fn_xml)

        # Heading index: list of (body_idx, heading_level, plain_text)
        self.headings: list[tuple[int, int, str]] = []
        self._build_heading_index()

    # ── Numbering ─────────────────────────────────────────────────────────────

    def _parse_numbering(self, xml_bytes: bytes):
        tree = etree.fromstring(xml_bytes)
        abstracts: dict[str, dict[str, str]] = {}
        for an in tree.findall(wn("abstractNum")):
            anid = an.get(wn("abstractNumId"), "")
            lvls: dict[str, str] = {}
            for lvl in an.findall(wn("lvl")):
                ilvl = lvl.get(wn("ilvl"), "0")
                fmt_el = lvl.find(wn("numFmt"))
                fmt = fmt_el.get(wn("val"), "bullet") if fmt_el is not None else "bullet"
                lvls[ilvl] = fmt
            abstracts[anid] = lvls
        for nm in tree.findall(wn("num")):
            nid = nm.get(wn("numId"), "")
            ref = nm.find(wn("abstractNumId"))
            if ref is not None:
                self.numbering[nid] = abstracts.get(ref.get(wn("val"), ""), {})

    # ── Footnotes ─────────────────────────────────────────────────────────────

    def _parse_footnotes(self, xml_bytes: bytes):
        tree = etree.fromstring(xml_bytes)
        for fn in tree.findall(wn("footnote")):
            fid = fn.get(wn("id"), "")
            if fid in ("-1", "0"):
                continue
            parts: list[str] = []
            skip_first_run = True  # first run contains footnoteRef marker
            for p_el in fn.findall(wn("p")):
                for child in p_el:
                    tag = etree.QName(child).localname
                    if tag == "r":
                        # Skip the footnoteRef run itself
                        if skip_first_run:
                            has_ref = child.find(wn("footnoteRef")) is not None
                            if has_ref:
                                skip_first_run = False
                                continue
                        # Skip pure tab or space runs at start
                        texts_here = [t.text or "" for t in child.findall(wn("t"))]
                        raw = "".join(texts_here)
                        if parts or raw.strip():
                            rpr = child.find(wn("rPr"))
                            is_bold = _run_is_bold(rpr)
                            is_italic = _run_is_italic(rpr)
                            text = raw
                            if is_bold and is_italic:
                                text = f"***{text}***" if text.strip() else text
                            elif is_bold:
                                text = f"**{text}**" if text.strip() else text
                            elif is_italic:
                                text = f"*{text}*" if text.strip() else text
                            parts.append(text)
                        # check for tab
                        if child.find(wn("tab")) is not None and not parts:
                            pass  # leading tab – skip
                    elif tag == "hyperlink":
                        rid = child.get(rn("id"), "")
                        url = self.rels.get(rid, "")
                        inner_texts = []
                        for r in child.findall(wn("r")):
                            for t in r.findall(wn("t")):
                                inner_texts.append(t.text or "")
                        link_text = "".join(inner_texts).strip()
                        if url and link_text:
                            parts.append(f"[{link_text}]({url})")
                        elif link_text:
                            parts.append(link_text)
            text = "".join(parts).strip()
            if text:
                self.footnotes[fid] = text

    # ── Heading index ─────────────────────────────────────────────────────────

    def _build_heading_index(self):
        heading_styles = {
            "Heading1": 1, "Heading2": 2, "Heading3": 3, "Heading4": 4,
        }
        for i, el in enumerate(self.body):
            if etree.QName(el).localname != "p":
                continue
            ppr = el.find(wn("pPr"))
            if ppr is None:
                continue
            pstyle = ppr.find(wn("pStyle"))
            if pstyle is None:
                continue
            sval = pstyle.get(wn("val"), "").replace(" ", "")
            level = heading_styles.get(sval)
            if level is None:
                continue
            text = _plain_text(el)
            self.headings.append((i, level, text))

    # ── Section range lookup ──────────────────────────────────────────────────

    def find_h1_range(self, section_num: int, title_hint: str = "") -> tuple[int, int] | None:
        """Find (start, end) body indices for H1 section with given number."""
        norm_hint = _norm(title_hint)

        for idx, (bidx, level, text) in enumerate(self.headings):
            if level != 1:
                continue
            norm_text = _norm(text)
            # Match by leading number (e.g. "2." or "2\t")
            m = re.match(r"^(\d+)[.\t\s]", text.strip())
            if m and int(m.group(1)) == section_num:
                return self._h1_end(idx, bidx)
            # Match by title hint (for unnumbered sections like 9 and 11)
            if norm_hint and _jaccard(norm_hint, norm_text) > 0.5:
                return self._h1_end(idx, bidx)
            # Match by section number anywhere at start
            if re.match(rf"^{section_num}[.\t\s]", text.strip()):
                return self._h1_end(idx, bidx)
        return None

    def find_annex_range(self, roman: str) -> tuple[int, int] | None:
        """Find (start, end) body indices for 'Annex {ROMAN}' heading."""
        target_lower = f"annex {roman.lower()}"
        for idx, (bidx, level, text) in enumerate(self.headings):
            if level != 1:
                continue
            if text.strip().lower().rstrip(".") == target_lower:
                return self._h1_end(idx, bidx)
        return None

    def find_h2_range(self, sub_section: str) -> tuple[int, int] | None:
        """Find (start, end) body indices for H2 matching sub_section text.
        sub_section format: '3.1 Overview' or '3.1. Overview'
        """
        m = re.match(r"^(\d+\.\d+)", sub_section.strip())
        if not m:
            return None
        prefix = m.group(1)

        for idx, (bidx, level, text) in enumerate(self.headings):
            if level != 2:
                continue
            text_stripped = re.sub(r"\s+", " ", text.strip())
            if text_stripped.startswith(prefix):
                # End = next H2 or H1
                end = len(self.body)
                for j, (nbidx, nlevel, _) in enumerate(self.headings[idx + 1:], idx + 1):
                    if nlevel <= 2:
                        end = nbidx
                        break
                return bidx, end
        return None

    def _h1_end(self, heading_list_idx: int, bidx: int) -> tuple[int, int]:
        end = len(self.body)
        for j, (nbidx, nlevel, _) in enumerate(self.headings[heading_list_idx + 1:]):
            if nlevel == 1:
                end = nbidx
                break
        return bidx, end


# ══════════════════════════════════════════════════════════════════════════════
# Text helpers
# ══════════════════════════════════════════════════════════════════════════════

def _plain_text(el) -> str:
    """Extract plain text from a paragraph element (no formatting)."""
    parts = []
    for r in el.iter(wn("r")):
        for t in r.findall(wn("t")):
            parts.append(t.text or "")
        if r.find(wn("tab")) is not None:
            parts.append(" ")
    return "".join(parts).strip()


def _run_is_bold(rpr) -> bool:
    if rpr is None:
        return False
    b = rpr.find(wn("b"))
    if b is not None:
        return b.get(wn("val"), "true") not in ("false", "0", "off")
    return False


def _run_is_italic(rpr) -> bool:
    if rpr is None:
        return False
    i = rpr.find(wn("i"))
    if i is not None:
        return i.get(wn("val"), "true") not in ("false", "0", "off")
    return False


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().strip())


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _strip_num_prefix(title: str) -> str:
    title = title.strip()
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title).strip()


# ══════════════════════════════════════════════════════════════════════════════
# Inline content renderer (runs, bold, italic, hyperlinks, footnote refs)
# ══════════════════════════════════════════════════════════════════════════════

def render_inline(
    p_el,
    dd: DocxData,
    used_fn: dict[str, int],
    fn_texts: dict[str, str],
    fn_counter: list[int],
) -> str:
    """Render the inline content of a paragraph to markdown.

    used_fn:    {docx_fn_id -> local_ref_number}  — mutated in place
    fn_texts:   {docx_fn_id -> footnote_text}      — collected for dedup
    fn_counter: [current_max_ref]                  — mutated in place
    """
    tokens: list[tuple[str, bool, bool]] = []  # (text, bold, italic)

    def add_run(r_el, override_url: str = ""):
        rpr = r_el.find(wn("rPr"))
        bold   = _run_is_bold(rpr)
        italic = _run_is_italic(rpr)

        for child in r_el:
            tag = etree.QName(child).localname
            if tag == "t":
                text = child.text or ""
                if text:
                    if override_url:
                        # Inside a hyperlink - don't double-format
                        tokens.append((text, False, False))
                    else:
                        tokens.append((text, bold, italic))
            elif tag == "footnoteReference":
                fn_id = child.get(wn("id"), "")
                if fn_id and fn_id not in ("-1", "0"):
                    if fn_id not in used_fn:
                        # Check dedup by text
                        fn_text = dd.footnotes.get(fn_id, "")
                        # Look for existing ref with same text
                        existing = next(
                            (ref for fid, ref in used_fn.items()
                             if fn_texts.get(fid) == fn_text),
                            None
                        )
                        if existing is not None:
                            used_fn[fn_id] = existing
                        else:
                            fn_counter[0] += 1
                            used_fn[fn_id] = fn_counter[0]
                        fn_texts[fn_id] = fn_text
                    tokens.append((f"[^{used_fn[fn_id]}]", False, False))
            elif tag == "tab":
                tokens.append((" ", False, False))
            elif tag == "br":
                tokens.append(("  \n", False, False))

    def process_hyperlink(hl_el):
        rid    = hl_el.get(rn("id"), "")
        anchor = hl_el.get(wn("anchor"), "")
        url    = dd.rels.get(rid, "")

        inner_parts: list[str] = []
        for child in hl_el:
            tag = etree.QName(child).localname
            if tag == "r":
                for t in child.findall(wn("t")):
                    inner_parts.append(t.text or "")
        link_text = "".join(inner_parts).strip()

        if not link_text:
            return
        if url:
            tokens.append((f"[{link_text}]({url})", False, False))
        elif anchor and not anchor.startswith("_Toc"):
            # Non-ToC internal anchor
            tokens.append((link_text, False, False))
        else:
            # ToC entry or empty anchor - output plain text
            tokens.append((link_text, False, False))

    # Pre-scan: build map of run-idx -> True if the run's text is a
    # prefix-match of the very next hyperlink (Word artifact: bold run +
    # hyperlink for same text appearing sequentially).
    p_children = list(p_el)
    skip_runs: set[int] = set()
    for ci, child in enumerate(p_children):
        if etree.QName(child).localname != "r":
            continue
        run_parts = [t.text or "" for t in child.findall(wn("t"))]
        run_text = "".join(run_parts).strip()
        if not run_text or len(run_text) < 10:
            continue
        # Find the next meaningful sibling
        for nxt in p_children[ci + 1:]:
            ntag = etree.QName(nxt).localname
            if ntag in ("bookmarkStart", "bookmarkEnd", "proofErr"):
                continue
            if ntag == "r":
                sub_texts = [t.text or "" for t in nxt.findall(wn("t"))]
                if not "".join(sub_texts).strip():
                    continue  # skip blank runs
            if ntag == "hyperlink":
                hl_texts = [t.text or "" for t in nxt.findall(f".//{wn('t')}")]
                hl_text = "".join(hl_texts).strip()
                if hl_text and run_text.startswith(hl_text) and len(hl_text) > 10:
                    skip_runs.add(ci)
            break

    for ci, child in enumerate(p_children):
        tag = etree.QName(child).localname
        if tag == "r":
            if ci not in skip_runs:
                add_run(child)
        elif tag == "hyperlink":
            process_hyperlink(child)
        elif tag in ("ins", "del"):
            for sub in child:
                if etree.QName(sub).localname == "r":
                    if tag == "ins":
                        add_run(sub)
        # Skip: pPr, bookmarkStart, bookmarkEnd, proofErr, etc.

    # Merge consecutive tokens with same formatting
    if not tokens:
        return ""
    merged: list[list] = [list(tokens[0])]
    for text, bold, italic in tokens[1:]:
        if merged[-1][1] == bold and merged[-1][2] == italic and not text.startswith("[^") and not merged[-1][0].startswith("[^"):
            merged[-1][0] += text
        else:
            merged.append([text, bold, italic])

    result: list[str] = []
    for text, bold, italic in merged:
        if text.startswith("[^") or text.startswith("[") and "](" in text:
            result.append(text)
            continue
        stripped = text.strip()
        if not stripped:
            result.append(text)
            continue
        # Wrap leading/trailing spaces outside the markup
        leading  = text[: len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        inner    = stripped
        if bold and italic:
            result.append(f"{leading}***{inner}***{trailing}")
        elif bold:
            result.append(f"{leading}**{inner}**{trailing}")
        elif italic:
            result.append(f"{leading}*{inner}*{trailing}")
        else:
            result.append(text)

    return "".join(result)


# ══════════════════════════════════════════════════════════════════════════════
# Table renderer
# ══════════════════════════════════════════════════════════════════════════════

def render_table(tbl_el, dd: DocxData) -> str:
    """Render a <w:tbl> element to a GFM markdown table."""
    rows_md: list[list[str]] = []

    for tr in tbl_el.findall(wn("tr")):
        cells: list[str] = []
        for tc in tr.findall(wn("tc")):
            # Check for vertical merge (continuation)
            tcp = tc.find(wn("tcPr"))
            vmerge = tcp.find(wn("vMerge")) if tcp is not None else None
            if vmerge is not None and vmerge.get(wn("val"), "") == "":
                # Continuation of vertical merge - use empty cell
                cells.append("")
                continue
            # Collect all paragraph text in cell
            parts = []
            for p_el in tc.findall(wn("p")):
                dummy_fn: dict[str, int] = {}
                dummy_texts: dict[str, str] = {}
                dummy_cnt: list[int] = [0]
                inline = render_inline(p_el, dd, dummy_fn, dummy_texts, dummy_cnt)
                if inline.strip():
                    parts.append(inline.strip())
            cells.append(" ".join(parts))
        if cells:
            rows_md.append(cells)

    if not rows_md:
        return ""

    # Normalise column count
    n_cols = max(len(r) for r in rows_md)
    for r in rows_md:
        while len(r) < n_cols:
            r.append("")

    # Escape pipe chars in cells
    def esc(s: str) -> str:
        return s.replace("|", "\\|")

    lines: list[str] = []
    header = rows_md[0]
    lines.append("| " + " | ".join(esc(c) for c in header) + " |")
    lines.append("|" + "|".join(" --- " for _ in header) + "|")
    for row in rows_md[1:]:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Section renderer
# ══════════════════════════════════════════════════════════════════════════════

# Heading styles (without spaces, for matching)
_H_STYLES = {"Heading1": 1, "Heading2": 2, "Heading3": 3, "Heading4": 4}

# Styles to skip entirely (ToC, table-of-figures, cover page, etc.)
_SKIP_STYLES = {
    "toc1", "toc2", "toc3", "toc4",
    "TableofFigures", "tableoffigures",
    "SectionSubtitle", "sectionsubtitle",
    "index2",
}

# Styles that produce blockquote
_QUOTE_STYLES = {"BlockText", "blocktext", "Quote", "quote", "NormalIndent", "normalindent"}

# Styles that produce indented text
_INDENT_STYLES = {"BodyTextIndent", "bodytextindent", "BodyText2", "bodytext2",
                  "BodyText3", "bodytext3", "NormalRight", "normalright"}


def render_section(
    dd: DocxData,
    start: int,
    end: int,
    heading_base_level: int,
    strip_first_heading: bool = True,
) -> tuple[str, list[tuple[int, str]]]:
    """Render body children [start, end) to markdown.

    heading_base_level: docx heading level that maps to `#` in output.
        1 for normal sections (H1 -> #, H2 -> ##, ...)
        2 for sub-pages    (H2 -> #, H3 -> ##, ...)

    strip_first_heading: if True, output the first heading as '# Title'
        without the numeric prefix (already handled in frontmatter title).
        Subsequent headings keep their full text.

    Returns (markdown_body, [(local_ref_num, fn_text), ...])
    """
    used_fn:   dict[str, int] = {}   # docx fn_id -> local ref num (1-based)
    fn_texts:  dict[str, str] = {}   # docx fn_id -> footnote text
    fn_counter: list[int]      = [0]

    parts: list[str]  = []
    first_heading_seen = False
    prev_was_list      = False
    prev_num_id        = ""

    # Track ordered list counter per (numId, ilvl)
    ordered_counters: dict[tuple[str, str], int] = {}

    for i in range(start, min(end, len(dd.body))):
        el   = dd.body[i]
        tag  = etree.QName(el).localname

        if tag == "tbl":
            table_md = render_table(el, dd)
            if table_md:
                if prev_was_list:
                    parts.append("\n")
                parts.append(f"\n{table_md}\n\n")
                prev_was_list = False
            continue

        if tag != "p":
            continue  # skip bookmarkStart, bookmarkEnd, sectPr

        # ── Paragraph ──────────────────────────────────────────────────────

        ppr = el.find(wn("pPr"))
        style_val = ""
        num_id    = None
        ilvl_val  = "0"

        if ppr is not None:
            pstyle = ppr.find(wn("pStyle"))
            if pstyle is not None:
                style_val = pstyle.get(wn("val"), "")
            nump = ppr.find(wn("numPr"))
            if nump is not None:
                ilvl_el  = nump.find(wn("ilvl"))
                numid_el = nump.find(wn("numId"))
                if ilvl_el is not None:
                    ilvl_val = ilvl_el.get(wn("val"), "0")
                if numid_el is not None:
                    num_id = numid_el.get(wn("val"))
                    if num_id == "0":
                        num_id = None

        style_ns = style_val.replace(" ", "")

        # Skip ToC / table-of-figures / empty heading styles
        if style_ns in _SKIP_STYLES:
            continue

        # Heading?
        h_level = _H_STYLES.get(style_ns)

        # Get inline content
        inline = render_inline(el, dd, used_fn, fn_texts, fn_counter)

        # Skip completely empty paragraphs
        if not inline.strip():
            # Preserve at most one blank line between content
            if parts and parts[-1] != "\n":
                parts.append("\n")
            continue

        # ── Heading rendering ──────────────────────────────────────────────

        if h_level is not None:
            output_level = h_level - heading_base_level + 1
            if output_level < 1:
                output_level = 1

            # Use plain text for headings to avoid br/formatting artifacts
            heading_plain = _plain_text(el)

            if not first_heading_seen:
                first_heading_seen = True
                if strip_first_heading:
                    title = _strip_num_prefix(heading_plain)
                    parts.append(f"{'#' * output_level} {title}\n\n")
                    prev_was_list = False
                    continue
                # keep as-is (e.g. "Annex I")
                parts.append(f"{'#' * output_level} {heading_plain.strip()}\n\n")
                prev_was_list = False
                continue

            if prev_was_list:
                parts.append("\n")
            heading_text = _strip_num_prefix(heading_plain) if output_level == 1 else heading_plain.strip()
            hashes = "#" * max(1, output_level)
            parts.append(f"\n{hashes} {heading_text}\n\n")
            prev_was_list = False
            continue

        # ── List paragraph ─────────────────────────────────────────────────

        if num_id is not None and style_ns == "ListParagraph":
            fmt = dd.numbering.get(num_id, {}).get(ilvl_val, "bullet")
            indent = "  " * int(ilvl_val)

            if fmt == "bullet":
                marker = "-"
            else:
                # Ordered: track counter per (numId, ilvl)
                key = (num_id, ilvl_val)
                # Reset counter if numId changed at this level
                if prev_num_id != num_id:
                    ordered_counters[key] = 0
                ordered_counters[key] = ordered_counters.get(key, 0) + 1
                marker = f"{ordered_counters[key]}."

            if not prev_was_list and parts:
                # Ensure blank line before list starts
                if parts[-1] not in ("\n", "\n\n"):
                    parts.append("\n")

            parts.append(f"{indent}{marker} {inline}\n")
            prev_was_list = True
            prev_num_id   = num_id
            continue

        # ── Special / block styles ─────────────────────────────────────────

        if style_ns in _QUOTE_STYLES:
            if prev_was_list:
                parts.append("\n")
            parts.append(f"\n> {inline}\n\n")
            prev_was_list = False
            continue

        if style_ns in _INDENT_STYLES:
            if prev_was_list:
                parts.append("\n")
            parts.append(f"\n  {inline}\n\n")
            prev_was_list = False
            continue

        if style_ns == "Point1":
            if not prev_was_list and parts and parts[-1] not in ("\n", "\n\n"):
                parts.append("\n")
            parts.append(f"- {inline}\n")
            prev_was_list = True
            continue

        if style_ns == "Point2":
            if not prev_was_list and parts and parts[-1] not in ("\n", "\n\n"):
                parts.append("\n")
            parts.append(f"  - {inline}\n")
            prev_was_list = True
            continue

        if style_ns == "Caption":
            if prev_was_list:
                parts.append("\n")
            parts.append(f"\n*{inline}*\n\n")
            prev_was_list = False
            continue

        # ── Default paragraph ──────────────────────────────────────────────

        if prev_was_list:
            parts.append("\n")
        parts.append(f"\n{inline}\n")
        prev_was_list = False
        prev_num_id   = ""

    # ── Footnote definitions ───────────────────────────────────────────────

    # Build ordered unique footnotes (by local ref number)
    ref_to_text: dict[int, str] = {}
    for fn_id, ref_num in used_fn.items():
        if ref_num not in ref_to_text:
            ref_to_text[ref_num] = fn_texts.get(fn_id, dd.footnotes.get(fn_id, ""))

    fn_defs: list[tuple[int, str]] = sorted(ref_to_text.items())

    # Build body text
    body = "".join(parts).strip()

    return body, fn_defs


# ══════════════════════════════════════════════════════════════════════════════
# File enrichment
# ══════════════════════════════════════════════════════════════════════════════

def enrich_file(
    md_path: Path,
    dd: DocxData,
) -> dict:
    """Enrich one markdown file. Returns stats dict."""
    text = md_path.read_text(encoding="utf-8")
    meta, old_body = parse_fm(text)

    section_num = int(meta.get("section_number", 0))
    has_sub     = bool(meta.get("sub_pages"))
    parent      = str(meta.get("parent") or "")
    sub_section = str(meta.get("sub_section") or "")
    slug        = md_path.stem

    # ── Skip parent pages (synthetic nav content) ──────────────────────────
    if has_sub:
        return {"file": md_path.name, "skipped": True, "reason": "parent page"}

    # ── Determine docx range ───────────────────────────────────────────────

    section_range = None
    heading_base  = 1    # default: H1 in docx = # in output
    strip_first   = True

    title_hint = str(meta.get("title", ""))

    if slug == "annexes":
        return {"file": md_path.name, "skipped": True, "reason": "annexes parent"}

    # Annex sub-pages: slug like 'annexes_i', 'annexes_xix'
    m_annex = re.match(r"^annexes_([ivxlcdm]+)$", slug, re.I)
    if m_annex:
        roman = m_annex.group(1)
        section_range = dd.find_annex_range(roman)
        heading_base  = 1
        strip_first   = False  # keep "Annex I" as the title

    # Sub-pages of sections 3 and 4
    elif parent and sub_section:
        section_range = dd.find_h2_range(sub_section)
        heading_base  = 2    # H2 in docx = # in output, H3 = ##, H4 = ###
        strip_first   = True

    # Simple sections (1, 2, 5-12)
    elif section_num and not parent:
        section_range = dd.find_h1_range(section_num, title_hint)
        heading_base  = 1
        strip_first   = True

    if section_range is None:
        return {"file": md_path.name, "skipped": True, "reason": "no matching docx section"}

    start, end = section_range

    # ── Render ────────────────────────────────────────────────────────────

    new_body, fn_defs = render_section(
        dd, start, end,
        heading_base_level=heading_base,
        strip_first_heading=strip_first,
    )

    # ── Append footnote definitions ────────────────────────────────────────

    if fn_defs:
        fn_block = "\n\n" + "\n".join(f"[^{n}]: {t}" for n, t in fn_defs)
        new_body = new_body.rstrip() + fn_block

    # ── Count hyperlinks and footnotes ────────────────────────────────────

    links_added = len(re.findall(r"\[.+?\]\(https?://", new_body))
    fn_refs     = len(re.findall(r"\[\^\d+\](?!:)", new_body))
    fn_defs_cnt = len(fn_defs)

    # Detect duplicate footnotes (same text, multiple docx IDs)
    texts_seen: dict[str, int] = {}
    dups_removed = 0
    for fn_id, ref_num in []:  # already handled in render
        pass

    # Count deduplication: if multiple docx fn_ids map to the same local ref
    # We can check by looking at unique ref counts vs total
    # (done inside render_inline via the existing-text lookup)

    # ── Write file ─────────────────────────────────────────────────────────

    new_text = make_file(meta, new_body)
    md_path.write_text(new_text, encoding="utf-8")

    return {
        "file":     md_path.name,
        "skipped":  False,
        "links":    links_added,
        "fn_refs":  fn_refs,
        "fn_defs":  fn_defs_cnt,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    console.rule("[bold]Enriching output/ markdown files from .docx[/bold]")

    # Parse docx once
    console.print(f"\n  Loading [cyan]{DOCX_PATH}[/cyan] ...")
    dd = DocxData(DOCX_PATH)
    console.print(
        f"  [green]+[/green] Loaded: {len(dd.body)} body elements, "
        f"{len(dd.headings)} headings, "
        f"{len(dd.footnotes)} footnotes, "
        f"{len(dd.rels)} hyperlink rels"
    )

    # Process all markdown files
    md_files = sorted(
        f for f in OUTPUT_DIR.glob("*.md")
        if not f.name.startswith("_")
    )
    console.print(f"  Processing {len(md_files)} markdown files...\n")

    results: list[dict] = []
    for md_path in md_files:
        r = enrich_file(md_path, dd)
        results.append(r)
        if r.get("skipped"):
            console.print(f"  [dim]skip[/dim] {r['file']:60s} ({r.get('reason', '')})")
        else:
            console.print(
                f"  [green]+[/green] {r['file']:60s} "
                f"links={r['links']:3d}  fn={r['fn_refs']:3d}"
            )

    # ── Summary table ──────────────────────────────────────────────────────

    console.print()
    console.rule("[bold]Enrichment Report[/bold]")

    tbl = Table(show_header=True, header_style="bold white on black",
                show_lines=True, width=110)
    tbl.add_column("File",       width=52)
    tbl.add_column("Status",     width=10)
    tbl.add_column("Links",      width=8,  justify="right")
    tbl.add_column("FN refs",    width=8,  justify="right")
    tbl.add_column("FN defs",    width=8,  justify="right")

    total_links = total_fns = 0
    for r in results:
        if r.get("skipped"):
            tbl.add_row(r["file"], "[dim]skipped[/dim]", "-", "-", "-")
        else:
            total_links += r["links"]
            total_fns   += r["fn_refs"]
            tbl.add_row(
                r["file"],
                "[green]done[/green]",
                str(r["links"]),
                str(r["fn_refs"]),
                str(r["fn_defs"]),
            )

    console.print(tbl)
    enriched = sum(1 for r in results if not r.get("skipped"))
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Enriched {enriched} files | "
        f"{total_links} hyperlinks added | "
        f"{total_fns} footnote references added"
    )


if __name__ == "__main__":
    main()
