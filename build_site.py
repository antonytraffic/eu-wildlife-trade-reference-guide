"""
build_site.py -- GOV.UK-styled static site generator.

Reads:  output/*.md  (YAML frontmatter + markdown body)
Writes: docs/        (flat HTML/CSS/JS for GitHub Pages)

Run:
    python build_site.py
"""

import html as html_mod
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import markdown2
import yaml
from rich.console import Console
from rich.table import Table as RichTable
from rich import box

INPUT_DIR      = Path("output")
SITE_DIR       = Path("docs")
SUMMARIES_FILE = INPUT_DIR / "_summaries.json"

console = Console()
FOOTER_TEXT: str = ""   # set at build time from _footer_content.md
_GUIDE_NAV_ITEMS: list[tuple[str, str]] = []  # (title, slug) for Reference Guide dropdown, set at build time
_ANNEXES_SLUG: str | None = None               # slug of the Annexes landing page, set at build time


# ==============================================================================
# SECTION 1 -- Markdown parsing helpers
# ==============================================================================

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^\s*#\s+[^\n]+\n*", re.MULTILINE)


def parse_md_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    fm_match = _FM_RE.match(raw)
    if fm_match:
        frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        body = raw[fm_match.end():]
    else:
        frontmatter = {}
        body = raw
    body = _H1_RE.sub("", body, count=1).lstrip("\n")
    snum = frontmatter.get("section_number", frontmatter.get("chapter_number", 0))
    return {
        "slug":             path.stem,
        "path":             path,
        "mtime":            path.stat().st_mtime,
        "title":            str(frontmatter.get("title", path.stem)),
        "section_number":   int(snum) if snum else 0,
        "page_start":       frontmatter.get("page_start"),
        "page_end":         frontmatter.get("page_end"),
        "has_tables":       bool(frontmatter.get("has_tables", False)),
        "summary":          str(frontmatter.get("summary", "")),
        "exclude_from_nav": bool(frontmatter.get("exclude_from_nav", False)),
        "parent":           str(frontmatter.get("parent") or ""),
        "sub_pages":        list(frontmatter.get("sub_pages") or []),
        "sub_section":      str(frontmatter.get("sub_section") or ""),
        "body":             body,
    }


_LP_RE = re.compile(r"<p>([a-z])\.\s+(.*?)</p>", re.DOTALL)


def _postprocess_lettered_lists(html: str) -> str:
    """Convert consecutive <p>a. …</p><p>b. …</p> runs into <ol class='lettered-list'>."""
    out: list[str] = []
    pos = 0

    while pos < len(html):
        m = _LP_RE.search(html, pos)
        if m is None:
            out.append(html[pos:])
            break

        if m.group(1) != "a":
            out.append(html[pos:m.end()])
            pos = m.end()
            continue

        # Found a paragraph starting with "a." — try to build a run
        out.append(html[pos:m.start()])
        items = [m.group(2).strip()]
        run_end = m.end()
        expected = ord("b")

        while expected <= ord("z"):
            gap = re.match(r"\s*", html[run_end:]).end()
            next_m = _LP_RE.match(html[run_end + gap:])
            if next_m and ord(next_m.group(1)) == expected:
                items.append(next_m.group(2).strip())
                run_end += gap + next_m.end()
                expected += 1
            else:
                break

        if len(items) >= 2:
            lis = "".join(f"<li>{it}</li>" for it in items)
            out.append(f'<ol class="lettered-list">{lis}</ol>\n')
        else:
            out.append(f"<p>a. {items[0]}</p>")

        pos = run_end

    return "".join(out)


_TABLE_RE   = re.compile(r"<table>(.*?)</table>", re.DOTALL)
_CELL_RE    = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_ROW_RE     = re.compile(r"<tr>.*?</tr>", re.DOTALL)
# Matches a <p> paragraph starting with Source:/Note: (after any HTML tags)
_SOURCE_FULL_RE = re.compile(
    r"^\s*(<p[^>]*>(?:<[^>]+>)*(?:Source|Note)s?:.*?</p>)",
    re.DOTALL | re.IGNORECASE,
)
# Matches table/figure label paragraphs: <p><em>Table N: ...</em></p>
_LABEL_RE = re.compile(
    r"<p><em>((Table|Figure)\s+(\d+)[^<]*)</em></p>",
    re.IGNORECASE,
)


def _check_row(row_m: re.Match, n_cols: int) -> str:
    row = row_m.group(0)
    cells = _CELL_RE.findall(row)
    if not cells:
        return row
    non_empty = [(i, c.strip()) for i, c in enumerate(cells) if c.strip()]
    if len(non_empty) == 1:
        _, content = non_empty[0]
        return (f'<tr><td colspan="{n_cols}" class="table-subheader">'
                f"{content}</td></tr>")
    return row


def _make_colgroup(inner: str, n_cols: int) -> str:
    """Return <colgroup> HTML for tables whose 3rd header is 'Documents Required'."""
    if n_cols != 4:
        return ""
    headers = re.findall(r"<th[^>]*>(.*?)</th>", inner, re.DOTALL)
    texts = [re.sub(r"<[^>]+>", "", h).lower() for h in headers]
    if any("documents required" in t for t in texts):
        return (
            '<colgroup>'
            '<col style="width:12%">'
            '<col style="width:8%">'
            '<col style="width:60%">'
            '<col style="width:20%">'
            '</colgroup>'
        )
    return ""


def _postprocess_tables(html: str) -> str:
    """
    Linear scan of html: wrap each <table> in .table-wrap, handle subheader rows,
    add colgroup for wide-column tables, and move Source:/Note: paragraphs
    immediately following a table into a <caption> element.
    """
    result: list[str] = []
    pos = 0

    for m in _TABLE_RE.finditer(html):
        result.append(html[pos:m.start()])
        inner = m.group(1)

        # Count columns
        n_cols = len(re.findall(r"<th[\s>]", inner))
        if n_cols == 0:
            first_tr = re.search(r"<tr>(.*?)</tr>", inner, re.DOTALL)
            if first_tr:
                n_cols = len(re.findall(r"<td[\s>]", first_tr.group(1)))
        n_cols = max(n_cols, 1)

        # Process tbody: single-content-cell rows → colspan sub-headers
        tbody_m = re.search(r"<tbody>(.*?)</tbody>", inner, re.DOTALL)
        if tbody_m:
            new_tbody = _ROW_RE.sub(lambda r: _check_row(r, n_cols), tbody_m.group(0))
            inner = inner[: tbody_m.start()] + new_tbody + inner[tbody_m.end():]

        colgroup = _make_colgroup(inner, n_cols)

        # Look for a Source:/Note: paragraph immediately after this table
        rest = html[m.end():]
        cap_m = _SOURCE_FULL_RE.match(rest)
        caption_html = ""
        skip = 0
        if cap_m:
            cap_para = cap_m.group(1).strip()
            # Extract inner HTML from the <p> wrapper
            cap_content = re.sub(r"^<p[^>]*>(.*)</p>$", r"\1", cap_para, flags=re.DOTALL)
            caption_html = f"<caption>{cap_content}</caption>"
            skip = cap_m.end()

        result.append(
            f'<div class="table-wrap">'
            f"<table>{caption_html}{colgroup}{inner}</table>"
            f"</div>"
        )
        pos = m.end() + skip

    result.append(html[pos:])
    return "".join(result)


def _postprocess_table_labels(html: str) -> str:
    """Convert <p><em>Table/Figure N: text</em></p> to <p class="table-label" id="...">...</p>."""
    def _sub(m: re.Match) -> str:
        text, kind, num = m.group(1), m.group(2).lower(), m.group(3)
        return f'<p class="table-label" id="{kind}-{num}">{text}</p>'
    return _LABEL_RE.sub(_sub, html)


_FOOTNOTES_RE = re.compile(
    r'(<div class="footnotes">.*?<ol[^>]*>)(.*?)(</ol>.*?</div>)',
    re.DOTALL,
)


def _postprocess_footnotes(html: str, threshold: int = 10) -> str:
    """When a footnotes block has more than `threshold` items, collapse the extras."""
    def _replace(m: re.Match) -> str:
        pre_ol  = m.group(1)
        ol_body = m.group(2)
        post_ol = m.group(3)

        parts = re.split(r'(?=<li\b)', ol_body)
        items = [p for p in parts if "<li" in p]

        if len(items) <= threshold:
            return m.group(0)

        n_more = len(items) - threshold
        visible = "".join(items[:threshold])
        hidden  = "".join(items[threshold:])
        label   = f"Show {n_more} more footnote{'s' if n_more != 1 else ''}"
        return (
            f"{pre_ol}{visible}</ol>"
            f'<ol class="footnotes-overflow" start="{threshold + 1}" hidden>'
            f"{hidden}</ol>"
            f'<button class="footnotes-show-more" type="button">{label}</button>'
            f"</div>"
        )

    return _FOOTNOTES_RE.sub(_replace, html)


# Caption paragraph immediately before placeholder: *Figure N: ...*\n[Insert Figure N]
_FIG_WITH_CAP_RE = re.compile(
    r'<p><em>(Figure\s+(\d+):[^<]*)</em></p>\s*\n\s*<p>\[Insert Figure \2\]</p>',
    re.IGNORECASE,
)
_FIG_BARE_RE = re.compile(r'<p>\[Insert Figure (\d+)\]</p>', re.IGNORECASE)


def _replace_figures(html: str, depth: int) -> str:
    """Convert [Insert Figure N] placeholders to <figure> elements."""
    root = "../" * depth

    def _sub_with_cap(m: re.Match) -> str:
        caption_text, num = m.group(1), m.group(2)
        return (
            f'<figure class="figure-block" id="figure-{num}">'
            f'<img src="{root}assets/images/Figure-{num}.png" alt="{h(caption_text)}">'
            f'<figcaption>{caption_text}</figcaption>'
            f'</figure>'
        )

    def _sub_bare(m: re.Match) -> str:
        num = m.group(1)
        return (
            f'<figure class="figure-block" id="figure-{num}">'
            f'<img src="{root}assets/images/Figure-{num}.png" alt="Figure {num}">'
            f'</figure>'
        )

    html = _FIG_WITH_CAP_RE.sub(_sub_with_cap, html)
    html = _FIG_BARE_RE.sub(_sub_bare, html)
    return html


# Matches bold Figure/Table references for in-text linking
_FIG_BOLD_REF_RE = re.compile(r'<strong>(Figure\s+(\d+))</strong>', re.IGNORECASE)
_TAB_BOLD_REF_RE = re.compile(r'<strong>(Table\s+(\d+))</strong>', re.IGNORECASE)

# Page-level lookups: figure/table number → docs/-relative URL with anchor
_FIGURE_PAGE_LOOKUP: dict[str, str] = {}
_TABLE_PAGE_LOOKUP: dict[str, str] = {}

# Matches "Summary of key instructions" bold paragraph + optional paras + ol
_SUMMARY_RE = re.compile(
    r'(<p>\s*<strong>Summary of key instructions[^<]*</strong>\s*</p>'
    r'(?:\s*<p>.*?</p>)*\s*'
    r'<ol>.*?</ol>)',
    re.DOTALL | re.IGNORECASE,
)


def build_figure_table_lookup(nav_sections: list[dict], all_sub: list[dict]) -> None:
    """Build page-level lookups: figure/table number → docs/-relative URL with anchor."""
    global _FIGURE_PAGE_LOOKUP, _TABLE_PAGE_LOOKUP
    _FIGURE_PAGE_LOOKUP = {}
    _TABLE_PAGE_LOOKUP = {}
    for ch in nav_sections + all_sub:
        slug = ch.get("slug", "")
        body = ch.get("body", "")
        for fm in re.finditer(r'\[Insert Figure (\d+)\]', body, re.IGNORECASE):
            _FIGURE_PAGE_LOOKUP[fm.group(1)] = f"chapters/{slug}.html#figure-{fm.group(1)}"
        for tm in re.finditer(r'\*Table\s+(\d+):', body, re.IGNORECASE):
            _TABLE_PAGE_LOOKUP[tm.group(1)] = f"chapters/{slug}.html#table-{tm.group(1)}"


def _link_figure_table_refs(html: str, depth: int) -> str:
    """Link bold Figure N / Table N references; use cross-page URLs when needed."""
    def _scan(pat: re.Pattern, anchor_prefix: str, page_lookup: dict) -> None:
        nonlocal html
        result: list[str] = []
        pos = 0
        for m in pat.finditer(html):
            before = html[max(0, m.start() - 400) : m.start()]
            result.append(html[pos : m.start()])
            pos = m.end()
            # Already inside a link
            if before.count("<a ") > before.count("</a>"):
                result.append(m.group(0))
                continue
            text, num = m.group(1), m.group(2)
            # Skip if the target element appears immediately after (reference is directly above it)
            after = html[m.end() : m.end() + 1200]
            if f'id="{anchor_prefix}-{num}"' in after:
                result.append(m.group(0))
                continue
            # Build URL — cross-page if on a different page, same-page anchor otherwise
            page_url = page_lookup.get(num)
            if page_url:
                href = _xref_url(page_url, depth)
            else:
                href = f"#{anchor_prefix}-{num}"
            result.append(f'<strong><a href="{href}">{text}</a></strong>')
        result.append(html[pos:])
        html = "".join(result)

    _scan(_FIG_BOLD_REF_RE, "figure", _FIGURE_PAGE_LOOKUP)
    _scan(_TAB_BOLD_REF_RE, "table", _TABLE_PAGE_LOOKUP)
    return html


def _postprocess_summary_sections(html: str) -> str:
    """Wrap 'Summary of key instructions' + following list in a small-print div."""
    return _SUMMARY_RE.sub(r'<div class="summary-smallprint">\1</div>', html)


def render_markdown(content: str) -> str:
    html = markdown2.markdown(
        content,
        extras=["tables", "fenced-code-blocks", "header-ids", "smarty-pants", "footnotes"],
    )
    html = _postprocess_lettered_lists(html)
    html = _postprocess_tables(html)
    html = _postprocess_table_labels(html)
    html = _postprocess_footnotes(html)
    html = _postprocess_summary_sections(html)
    return html


# ==============================================================================
# Cross-reference auto-linker
# ==============================================================================

# Module-level lookup: section/annex string → URL relative to docs/
_XREF_LOOKUP: dict[str, str] = {}

# Match parenthetical cross-refs: (see... Section N.N.N...) or (see... Annex X...)
# Trailing [^()]*(?:\([^()]*\)[^()]*)* allows one level of nested parens e.g. "(re-)"
_XREF_SEC_RE = re.compile(
    r'\(([^()]*?(?:see|see\s+also)[^()]*?Sections?\s+(\d[\d.]*)[^()]*(?:\([^()]*\)[^()]*)*)\)',
    re.IGNORECASE,
)
_XREF_ANN_RE = re.compile(
    r'\(([^()]*?(?:see|see\s+also)[^()]*?Annex\s+([IVXLivxl]+)[^()]*(?:\([^()]*\)[^()]*)*)\)',
    re.IGNORECASE,
)
# Match ALL bold Section/Annex references (not just those after "see")
_SEC_BOLD_ALL_RE = re.compile(r'<strong>(Sections?\s+(\d[\d.]*)[^<]*)</strong>', re.IGNORECASE)
_ANN_BOLD_ALL_RE = re.compile(r'<strong>(Annex(?:es)?\s+([IVXLivxl]+)[^<]*)</strong>', re.IGNORECASE)


def _predict_anchor(num: str, heading_text: str) -> str:
    """Predict the markdown2 header-id for a heading with section number."""
    full = (num + " " + heading_text).lower()
    anchor = re.sub(r"[^\w\s-]", "", full)   # strip non-word/space/hyphen
    anchor = re.sub(r"\s+", "-", anchor.strip())
    anchor = re.sub(r"-+", "-", anchor).strip("-")
    return anchor


def build_section_lookup(nav_sections: list[dict], all_sub: list[dict]) -> None:
    """Populate _XREF_LOOKUP with section/annex number → docs/-relative URL."""
    global _XREF_LOOKUP
    _XREF_LOOKUP = {}

    # Top-level sections: integer → parent page
    for ch in nav_sections:
        snum = ch["section_number"]
        if snum and 0 < snum:
            _XREF_LOOKUP[str(snum)] = f"chapters/{ch['slug']}.html"

    # Sub-pages: extract leading N.N from sub_section field
    for ch in all_sub:
        ss = ch.get("sub_section", "")
        if ss:
            m = re.match(r"^(\d+(?:\.\d+)+)", ss)
            if m:
                _XREF_LOOKUP[m.group(1)] = f"chapters/{ch['slug']}.html"

        # Also scan headings within each sub-page for deeper section numbers (3+ components)
        for hm in re.finditer(
            r"^#{2,}\s+((\d+(?:\.\d+){2,})\s+(.+))$", ch["body"], re.MULTILINE
        ):
            full_heading, num, text = hm.group(1), hm.group(2), hm.group(3)
            if num not in _XREF_LOOKUP:
                anchor = _predict_anchor(num, text.strip())
                _XREF_LOOKUP[num] = f"chapters/{ch['slug']}.html#{anchor}"

    # Scan simple top-level section bodies (5, 8, 11, etc.) for sub-headings like 5.1, 8.2, 11.2.1
    for ch in nav_sections:
        for hm in re.finditer(
            r"^#{2,}\s+((\d+(?:\.\d+)+)\s+(.+))$", ch["body"], re.MULTILINE
        ):
            num, text = hm.group(2), hm.group(3).strip()
            if num not in _XREF_LOOKUP:
                anchor = _predict_anchor(num, text)
                _XREF_LOOKUP[num] = f"chapters/{ch['slug']}.html#{anchor}"

    # Annex sub-pages: map Roman numeral → annex slug
    for ch in all_sub:
        if ch.get("parent") == "annexes":
            title = ch.get("title", "")
            am = re.match(r"Annex\s+([IVXLivxl]+)", title, re.IGNORECASE)
            if am:
                _XREF_LOOKUP[f"annex_{am.group(1).upper()}"] = (
                    f"chapters/{ch['slug']}.html"
                )


def _xref_url(doc_url: str, depth: int) -> str:
    """Convert a docs/-relative URL to a URL relative to the current page depth."""
    if depth == 1:
        return doc_url.replace("chapters/", "", 1)  # same directory
    return doc_url  # depth 0: docs/ root


def autolink_xrefs(html: str, depth: int = 1) -> str:
    """Replace (see Section X.X) / (see Annex X) with hyperlinks."""
    if not _XREF_LOOKUP:
        return html

    def _linkify_sections(text: str) -> str:
        """Link Section(s) N.N tokens. Single ref: include 'Section' in link. Multiple: just numbers."""
        sec_ms = list(re.finditer(r'(Sections?\s+)(\d[\d.]*)', text, re.IGNORECASE))
        bare_ms = list(re.finditer(r'(\s+and\s+|,\s*)(\d[\d.]*)', text, re.IGNORECASE))
        multiple = len(sec_ms) + len(bare_ms) > 1

        def _link_sec(mm: re.Match) -> str:
            n = mm.group(2).rstrip(".")
            u = _XREF_LOOKUP.get(n)
            if u is None:
                return mm.group(0)
            href = _xref_url(u, depth)
            if multiple:
                return f'{mm.group(1)}<a href="{href}">{mm.group(2)}</a>'
            return f'<a href="{href}">{mm.group(1)}{mm.group(2)}</a>'

        result = re.sub(r'(Sections?\s+)(\d[\d.]*)', _link_sec, text, flags=re.IGNORECASE)

        def _link_bare(mm: re.Match) -> str:
            sep, n_str = mm.group(1), mm.group(2)
            u = _XREF_LOOKUP.get(n_str.rstrip("."))
            if u is None:
                return mm.group(0)
            return f'{sep}<a href="{_xref_url(u, depth)}">{n_str}</a>'

        return re.sub(r'(\s+and\s+|,\s*)(\d[\d.]*)', _link_bare, result, flags=re.IGNORECASE)

    def _linkify_annexes(text: str, after_ctx: str = "") -> str:
        """Link Annex(es) X tokens. Single ref: include 'Annex' in link. Multiple: just roman nums."""
        # Skip linking when the annex belongs to a Regulation (e.g. "Annex X to Regulation")
        # but NOT when the text merely mentions a Regulation elsewhere (e.g. "Annex XVI lists … Regulation").
        if re.search(r'\bRegulation\b', text, re.IGNORECASE):
            return text
        if re.search(r'^\s*(?:to|of)\s+Regulation\b', after_ctx, re.IGNORECASE):
            return text
        ann_ms = list(re.finditer(r'(Annex(?:es)?\s+)([IVXLivxl]+)', text, re.IGNORECASE))
        bare_ms = list(re.finditer(r'(\s+and\s+|,\s*)([IVXLivxl]+)', text, re.IGNORECASE))
        # Only count bare roman numeral groups that are valid annexes
        valid_bare = [mm for mm in bare_ms if _XREF_LOOKUP.get(f"annex_{mm.group(2).upper()}")]
        multiple = len(ann_ms) + len(valid_bare) > 1

        def _link_ann(mm: re.Match) -> str:
            an = mm.group(2).strip().upper()
            u = _XREF_LOOKUP.get(f"annex_{an}")
            if u is None:
                return mm.group(0)
            href = _xref_url(u, depth)
            if multiple:
                return f'{mm.group(1)}<a href="{href}">{mm.group(2)}</a>'
            return f'<a href="{href}">{mm.group(1)}{mm.group(2)}</a>'

        result = re.sub(r'(Annex(?:es)?\s+)([IVXLivxl]+)', _link_ann, text, flags=re.IGNORECASE)

        def _link_bare_ann(mm: re.Match) -> str:
            sep, rom = mm.group(1), mm.group(2)
            u = _XREF_LOOKUP.get(f"annex_{rom.upper()}")
            if u is None:
                return mm.group(0)
            return f'{sep}<a href="{_xref_url(u, depth)}">{rom}</a>'

        return re.sub(r'(\s+and\s+|,\s*)([IVXLivxl]+)', _link_bare_ann, result, flags=re.IGNORECASE)

    def _sub_section(m: re.Match) -> str:
        return f'({_linkify_sections(m.group(1))})'

    def _sub_annex(m: re.Match) -> str:
        return f'({_linkify_annexes(m.group(1))})'

    def _apply_bold_all(pat: re.Pattern, kind: str) -> None:
        """Link ALL bold Section/Annex refs (with or without 'see' context)."""
        nonlocal html
        result: list[str] = []
        pos = 0
        for m in pat.finditer(html):
            before = html[max(0, m.start() - 400) : m.start()]
            result.append(html[pos : m.start()])
            pos = m.end()
            inner_text = m.group(1)
            # Skip if already inside a link or already contains a link
            if before.count("<a ") > before.count("</a>") or "<a " in inner_text:
                result.append(m.group(0))
                continue
            if kind == "section":
                linked = _linkify_sections(inner_text)
                result.append(f'<strong>{linked}</strong>')
            else:
                after_ctx = html[m.end() : m.end() + 150]
                linked = _linkify_annexes(inner_text, after_ctx)
                result.append(f'<strong>{linked}</strong>')
        result.append(html[pos:])
        html = "".join(result)

    html = _XREF_SEC_RE.sub(_sub_section, html)
    html = _XREF_ANN_RE.sub(_sub_annex, html)
    _apply_bold_all(_SEC_BOLD_ALL_RE, "section")
    _apply_bold_all(_ANN_BOLD_ALL_RE, "annex")

    def _link_bare_bold_secs() -> None:
        """Link standalone bold section numbers e.g. <strong>4.5</strong> or <strong>3.5.7</strong>
        that appear without a 'Section' prefix (typically the second in a paired reference)."""
        nonlocal html
        _BARE_BOLD_SEC_RE = re.compile(r'<strong>(\d+\.\d[\d.]*)</strong>')
        result: list[str] = []
        pos = 0
        for m in _BARE_BOLD_SEC_RE.finditer(html):
            before = html[max(0, m.start() - 200) : m.start()]
            result.append(html[pos : m.start()])
            pos = m.end()
            n_str = m.group(1).rstrip('.')
            # Skip if already inside a link or already contains a link
            if before.count('<a ') > before.count('</a>') or '<a ' in m.group(0):
                result.append(m.group(0))
                continue
            u = _XREF_LOOKUP.get(n_str)
            if u is None:
                result.append(m.group(0))
                continue
            href = _xref_url(u, depth)
            result.append(f'<strong><a href="{href}">{m.group(1)}</a></strong>')
        result.append(html[pos:])
        html = ''.join(result)

    _link_bare_bold_secs()
    return html


# ==============================================================================
# Section label helper
# ==============================================================================

def _section_ref_number(ch: dict) -> str:
    """Return the bare section/sub-section number, e.g. '7' or '3.1', or ''."""
    snum = ch.get("section_number", 0)
    ss   = ch.get("sub_section", "")

    if ss:
        # Sub-page: extract leading N.N from sub_section "3.1 Overview"
        m = re.match(r"^(\d+(?:\.\d+)+)", ss)
        if m:
            return m.group(1)
    if snum and 2 <= snum <= 10:
        return str(snum)

    return ""


def _section_label_text(ch: dict) -> str:
    """Return 'Section N' (or 'Section N.N') for a chapter or sub-page, or ''."""
    ref = _section_ref_number(ch)
    return f"Section {ref}" if ref else ""


def extract_headings(html: str) -> list[dict]:
    pat = re.compile(
        r'<(h[234])[^>]*\bid="([^"]*)"[^>]*>(.*?)</\1>',
        re.DOTALL | re.IGNORECASE,
    )
    results = []
    for m in pat.finditer(html):
        tag, anchor_id, inner = m.groups()
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if text:
            results.append({"level": int(tag[1]), "id": anchor_id, "text": text})
    return results


def strip_markdown(content: str) -> str:
    s = content
    s = re.sub(r"^---.*?---\s*", "", s, flags=re.DOTALL)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"```.*?```", " ", s, flags=re.DOTALL)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*\d+\.\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"\|", " ", s)
    s = re.sub(r"[-*_]{3,}", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def h(text) -> str:
    return html_mod.escape(str(text))


def first_sentences(text: str, n: int = 2, max_chars: int = 280) -> str:
    text = text.strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    result = " ".join(parts[:n])
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0] + "..."
    return result


# ==============================================================================
# SECTION 2 -- Stylesheet
# ==============================================================================

CSS = """\
/* ================================================
   EU Wildlife Trade Reference Guide
   EC-branded stylesheet (Europa Component Library tokens)
   ================================================ */

@font-face {
  font-family: "Inter";
  src: url("fonts/InterVariable.woff2") format("woff2");
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}

:root {
  --green:        #0046ff;   /* EC primary-600 */
  --dark-green:   #0035bf;   /* EC primary-700, hover state */
  --black:        #00002e;   /* EC brand navy (grey-950) */
  --text:         #00002e;
  --secondary:    #696984;   /* EC grey-600 */
  --border:       #d4d4dc;   /* EC grey-200 */
  --light-grey:   #f6f6f8;   /* EC grey-50 */
  --mid-grey:     #ededf0;   /* EC grey-75 */
  --white:        #ffffff;
  --focus:        #ffce00;   /* EC yellow-gold-500 */
  --visited:      #66439a;   /* EC purple-700 */
  --radius:       4px;       /* EC border-radius 's' */
  --shadow-1:     0 0 0.5px 0.5px rgba(24,39,75,.08), 0 6px 12px 0 rgba(24,39,75,.08);
  --shadow-2:     0 0 0.5px 0.5px rgba(24,39,75,.08), 0 10px 22px 0 rgba(24,39,75,.1);
  --icon-bg:      #d9e3ff;   /* EC primary-200, teardrop icon badge background */
  --max-width:    1368px;   /* measured from food.ec.europa.eu .ecl-container */
  --gutter:       32px;     /* measured container side padding */
  --font:         "Inter", Arial, sans-serif;
}

*, *::before, *::after { box-sizing: border-box; }
html { font-size: 16px; scroll-behavior: smooth; overflow-x: clip; }

body {
  font-family: var(--font);
  font-size: 1.125rem;
  line-height: 1.5556;
  color: var(--text);
  background: var(--white);
  margin: 0;
  overflow-x: clip;
  -webkit-font-smoothing: antialiased;
}

/* -- Skip link ---------------------------------------- */
.skip-link {
  position: absolute; left: -999em; top: 0; z-index: 9999;
  padding: 8px 14px; background: var(--focus); color: var(--black);
  font-weight: 700; text-decoration: none;
}
.skip-link:focus-visible { left: 0; }

/* -- Screen-reader only -------------------------------- */
.sr-only {
  position: absolute; width: 1px; height: 1px;
  padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}

/* -- Container ---------------------------------------- */
.container {
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 0 var(--gutter);
}
/* A .container nested inside another .container (e.g. the hero band, which
   sits inside #main-content's container) shouldn't double the width cap/padding. */
.container .container { max-width: none; padding: 0; }

/* -- Links -------------------------------------------- */
a                { color: var(--green); }
a:hover          { color: var(--dark-green); }
a:visited        { color: var(--visited); }
a:visited:hover  { color: var(--dark-green); }
a:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
  border-radius: 1px;
}

/* -- Official strip ------------------------------------ */
.official-strip {
  background: var(--light-grey);
}
.official-strip__inner {
  display: flex; align-items: center; gap: 8px;
  max-width: none; margin: 0;
  padding: 4px 16px; line-height: 20px;
  font-size: .875rem; color: var(--secondary);
}
.official-strip__flag { width: 26px; height: auto; flex-shrink: 0; }

/* -- Site header -------------------------------------- */
.site-header { background: var(--white); }
.site-header__top-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 32px var(--gutter) 24px;
  max-width: var(--max-width);
  margin: 0 auto;
  flex-wrap: wrap;
}
.site-header__logo-link { display: block; flex-shrink: 0; }
.site-header__logo { height: 5rem; width: auto; display: block; }
.site-header__banner {
  background: var(--mid-grey);
}
.site-header__banner .container {
  display: flex; align-items: center; height: 44px;
  padding: 0 var(--gutter);
  max-width: var(--max-width);
  margin: 0 auto;
}
.site-header__site-name {
  color: var(--black);
  font-size: 1.25rem; font-weight: 400; letter-spacing: .01em;
}

/* -- Header search (standard EC Component Library search-form) ----- */
.header-search {
  display: flex; align-items: stretch; flex-shrink: 0;
  border: 1px solid var(--secondary); border-radius: var(--radius) 0 0 var(--radius);
  background: var(--white); overflow: hidden;
}
.header-search input[type="search"] {
  border: none; padding: 12px 16px 12px 12px;
  font: inherit; font-size: 1rem; line-height: 1.375; min-width: 360px; background: transparent;
  color: var(--black);
}
.header-search input[type="search"]:focus { outline: 3px solid var(--focus); outline-offset: -3px; }
.header-search button {
  position: relative; border: none; background: transparent; color: var(--black);
  padding: 0 14px;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
}
.header-search button::before {
  content: ""; position: absolute; left: 0; top: 25%; bottom: 25%; width: 1px;
  background: var(--border);
}
.header-search button svg { width: 20px; height: 20px; }
.header-search button:hover { color: var(--green); }
.header-search button:focus-visible { outline: 3px solid var(--focus); outline-offset: -3px; }

/* -- Top navigation ----------------------------------- */
.top-nav {
  background: var(--black);
  border-bottom: 1px solid #333;
  position: relative;
}
.top-nav__list {
  list-style: none; margin: 0 0 0 -20px; padding: 0;
  display: flex; align-items: center; gap: 0; height: 55px;
}
.top-nav__list > li { display: flex; align-items: center; align-self: stretch; margin: 0; }
.top-nav__list > li::after {
  content: ""; display: block; flex-shrink: 0;
  width: 1px; height: 40px;
  background: rgba(209, 217, 241, .6);
}
.top-nav__link {
  display: flex; align-items: center; padding: 8px 16px 8px 20px;
  color: var(--white); text-decoration: none;
  font-size: 1rem; font-weight: 400; line-height: 1.5;
  transition: background-color .1s, color .1s;
}
.top-nav__link:hover {
  background: var(--icon-bg); color: var(--black); text-decoration: none;
}
.top-nav__link:visited { color: var(--white); }

/* -- Top navigation: "Reference Guide" mega-menu ------- */
.top-nav__dropdown-toggle {
  display: flex; align-items: center; gap: 6px;
  appearance: none; background: none; border: none;
  font-family: inherit; margin: 0; cursor: pointer;
}
.top-nav__caret { width: 10px; height: 7px; flex-shrink: 0; transition: transform .15s; }
.top-nav__item--dropdown.is-open .top-nav__caret { transform: rotate(180deg); }
.top-nav__item--dropdown.is-open .top-nav__dropdown-toggle {
  background: var(--white); color: var(--black);
}
.top-nav__dropdown {
  display: none;
  position: absolute; top: 100%; left: 0; right: 0; z-index: 20;
  background: var(--white); border-top: 1px solid #333;
  box-shadow: 0 12px 20px rgba(0,0,0,.15);
}
.top-nav__item--dropdown.is-open .top-nav__dropdown { display: block; }
.top-nav__dropdown-inner {
  max-width: var(--max-width); margin: 0 auto; padding: 32px var(--gutter);
  display: flex; gap: 48px;
}
.top-nav__dropdown-intro {
  flex: 0 0 240px;
  padding-right: 48px;
  border-right: 1px solid var(--mid-grey);
}
.top-nav__dropdown-intro h2 {
  margin: 0 0 8px; padding: 0; border: none;
  font-size: 1.5rem;
}
.top-nav__dropdown-intro p {
  margin: 0; color: var(--secondary); font-size: 1rem; line-height: 1.5;
}
.top-nav__dropdown-links {
  flex: 1; list-style: none; margin: 0; padding: 0;
}
.top-nav__dropdown-link {
  display: block; padding: 10px 0;
  color: var(--black); font-size: 1.0625rem; text-decoration: none;
  border-bottom: 1px solid var(--light-grey);
}
.top-nav__dropdown-links li:last-child .top-nav__dropdown-link { border-bottom: none; }
.top-nav__dropdown-link:hover { color: var(--green); text-decoration: underline; }
.top-nav__dropdown-link:visited { color: var(--black); }

/* -- Breadcrumbs -------------------------------------- */
.breadcrumbs {
  display: flex; align-items: center; height: 68px;
  border-bottom: 1px solid var(--mid-grey);
  background: var(--white);
}
.breadcrumbs ol {
  list-style: none; margin: 0 auto; padding: 0 var(--gutter);
  display: flex; flex-wrap: wrap; gap: 0; font-size: .875rem;
  max-width: var(--max-width);
}
.breadcrumbs li { display: flex; align-items: center; }
.breadcrumbs li + li::before {
  content: "";
  display: inline-block; flex-shrink: 0;
  width: 14px; height: 14px; margin: 0 8px;
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M5 3l4 4-4 4' stroke='%23696984' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat center / contain;
}
.breadcrumbs a   { color: var(--black); font-size: .875rem; }
.breadcrumbs a:hover { color: var(--green); }
.breadcrumbs [aria-current="page"] { color: var(--black); }

/* -- Page header (ECL page-header component) ---------- */
.page-header { background: var(--white); }
.page-header .container { padding-bottom: 0; max-width: var(--max-width); margin: 0 auto; padding-left: var(--gutter); padding-right: var(--gutter); }
.page-header + .main-content { padding-top: 28px; }
.page-header .breadcrumbs { border-bottom: none; background: transparent; }
.page-header .breadcrumbs ol { max-width: none; margin: 0; padding: 0; }
.page-header__meta {
  list-style: none; margin: 8px 0 0; padding: 0;
}
.page-header__title { padding-top: 20px; margin: 0; }
.page-header__meta-item {
  display: inline;
  font-size: .8125rem; color: var(--secondary);
}
.page-header__meta-item:first-child {
  display: block; font-weight: 700; text-transform: uppercase;
  letter-spacing: .05em; color: var(--secondary); margin-bottom: 2px;
}

/* -- Main wrapper ------------------------------------- */
.main-content { padding: 30px 0 70px; }

/* -- Page grid ---------------------------------------- */
.page-grid { display: flex; gap: 40px; align-items: flex-start; }

/* -- Sidebar ------------------------------------------ */
.sidebar {
  flex: 0 0 300px; max-width: 300px;
  position: sticky; top: 24px;
  max-height: calc(100vh - 48px); overflow-y: auto;
}
.sidebar__label {
  font-size: 1rem; font-weight: 600;
  color: var(--black); margin: 0 0 16px;
}
.sidebar__nav { list-style: none; padding: 0; margin: 0; border-left: 3px solid var(--border); }
.sidebar__nav li { margin: 0; }
.sidebar__nav a {
  display: block; padding: 8px 0 8px 16px; margin-left: -3px;
  border-left: 3px solid transparent;
  color: var(--text);
  text-decoration: none; font-size: 1.125rem; line-height: 1.35;
}
.sidebar__nav a:hover { text-decoration: underline; }
.sidebar__nav a.is-active {
  color: var(--black);
  background: var(--icon-bg);
  border-left-color: var(--green);
}
.sidebar__nav .sidebar-h3 a { padding-left: 28px; color: var(--secondary); font-size: 1rem; }
.sidebar__nav .sidebar-h3 a.is-active { color: var(--black); }

/* -- Article area ------------------------------------- */
.article { flex: 1 1 auto; min-width: 0; }

/* -- Contents list (used by mobile contents dropdown) - */
.contents-list { list-style: none; margin: 0; padding: 0; }
.contents-list li {
  margin: 6px 0; font-size: 1rem;
  display: flex; align-items: baseline; gap: .5em;
}
.contents-ref {
  color: var(--black); flex-shrink: 0;
  font-variant-numeric: tabular-nums; min-width: 2.4em;
}

/* -- Mobile contents dropdown ------------------------- */
.mobile-contents { display: none; margin-bottom: 20px; }
.mobile-contents summary {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; background: var(--light-grey); border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer; font-weight: 700; font-size: .9375rem; list-style: none;
}
.mobile-contents summary::-webkit-details-marker { display: none; }
.mobile-contents[open] summary { border-bottom: none; border-radius: var(--radius) var(--radius) 0 0; }
.mobile-contents__body {
  border: 1px solid var(--border); border-top: none;
  border-radius: 0 0 var(--radius) var(--radius);
  padding: 12px 16px 16px; background: var(--white);
}
.mobile-contents__body .contents-list { padding: 0; }
.mobile-contents__body li { margin: 7px 0; font-size: .9375rem; }

/* -- Small list (Table 12 footnotes) ------------------ */
.small-list, .small-list li { font-size: 0.8rem; }

/* -- Typography --------------------------------------- */
h1 { font-size: 4rem;     font-weight: 600; line-height: 1;    margin: 0; }
h2 {
  font-size: 2.75rem;  font-weight: 600; line-height: 1.09;
  margin: 40px 0 16px; padding-top: 12px; border-top: 1px solid var(--border);
}
.article-body > h2:first-child, .article-body > h3:first-child {
  padding-top: 0; border-top: none;
}
h3 { font-size: 1.75rem;  font-weight: 600; line-height: 1.14; margin: 32px 0 16px; }
h4 { font-size: 1.375rem; font-weight: 600; line-height: 1.3;  margin: 26px 0 14px; }
p  { margin: 0 0 18px; }

ul, ol { margin: 0 0 18px 1.5em; }
li { margin-bottom: 5px; }
li > ul, li > ol { margin-top: 5px; margin-bottom: 5px; }

blockquote {
  margin: 20px 0; padding: 12px 20px;
  border-left: 5px solid var(--border); color: var(--secondary);
}
code {
  font-family: "Courier New", Courier, monospace;
  font-size: .875em; background: var(--light-grey); padding: 2px 5px;
}
pre {
  background: var(--light-grey); padding: 16px; overflow-x: auto;
  margin: 0 0 20px; font-size: .875rem;
}
pre code { background: none; padding: 0; }

/* -- Tables ------------------------------------------- */
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 20px 0 30px; }
.table-label {
  font-weight: bold; font-style: normal; font-size: 0.9rem; margin-bottom: 0.25rem;
}
.article table, .article-body table {
  border-collapse: collapse; width: 100%;
  table-layout: fixed; font-size: 0.85rem;
}
.article table th, .article-body table th {
  background: var(--black); color: var(--white); padding: 10px 14px;
  text-align: left; font-weight: normal; font-style: normal; border: 1px solid #333;
  overflow-wrap: break-word; word-wrap: break-word;
}
.article table td, .article-body table td {
  padding: 9px 14px; border: 1px solid var(--border); vertical-align: top;
  overflow-wrap: break-word; word-wrap: break-word;
}
.article table tr:nth-child(even) td, .article-body table tr:nth-child(even) td { background: var(--light-grey); }
.article table tr:hover td, .article-body table tr:hover td { background: var(--mid-grey); }
.table-subheader {
  background: #f3f2f1; font-weight: 700; text-align: left; padding: 6px 8px;
}
table caption {
  caption-side: bottom; font-size: 0.8rem; color: #505a5f;
  text-align: left; padding-top: 0.4rem; font-style: normal;
}

/* -- Section meta line -------------------------------- */
.chapter-meta {
  display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
  font-size: .875rem; color: var(--secondary); margin-bottom: 24px;
}
.badge {
  display: inline-block; padding: 2px 8px; background: var(--green);
  color: var(--white); font-size: .75rem; font-weight: 700; border-radius: var(--radius);
}

/* -- Prev / Next nav ---------------------------------- */
.chapter-nav {
  display: flex; justify-content: space-between; gap: 20px;
  margin: 50px 0 20px; padding-top: 20px; border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.chapter-nav a {
  display: flex; flex-direction: column; max-width: 46%;
  color: var(--green); text-decoration: none;
}
.chapter-nav a:hover .chapter-nav__title { text-decoration: underline; }
.chapter-nav__label { font-size: .8125rem; color: var(--secondary); margin-bottom: 2px; }
.chapter-nav__title { font-weight: 700; font-size: .9375rem; line-height: 1.3; }
.chapter-nav__next  { text-align: right; margin-left: auto; }

/* -- Back to top -------------------------------------- */
.back-to-top {
  display: block; text-align: right; font-size: .875rem;
  margin-top: 10px; color: var(--green);
}

/* -- Homepage hero ------------------------------------ */
.main-content:has(.hero) { padding-top: 0; }
.hero {
  width: 100vw;
  margin-left: calc(50% - 50vw); margin-right: calc(50% - 50vw);
  background-color: var(--black);
  background-image: url("images/hero-elephants-line.jpg");
  background-position: right center;
  background-size: cover;
  background-repeat: no-repeat;
  border-bottom: 1px solid var(--border);
  padding: 72px 0;
  color: var(--white);
}
.hero .container {
  text-align: left; max-width: var(--max-width); margin: 0 auto; padding: 0 var(--gutter);
}
.hero h1 { margin-bottom: 12px; font-size: 3.25rem; max-width: 820px; color: var(--white); }
.hero__lead {
  font-size: 1.25rem; max-width: 600px; line-height: 1.6; margin-bottom: 24px;
  color: var(--white);
}

/* -- Homepage search (EC Component Library search-form pattern) ---- */
.search-form {
  display: flex; align-items: stretch; max-width: 580px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--white); overflow: hidden;
}
.search-form input[type="search"] {
  flex: 1; padding: 12px 16px; font: inherit; font-size: 1rem;
  border: none; color: var(--black); background: transparent;
}
.search-form input[type="search"]:focus { outline: 3px solid var(--focus); outline-offset: -3px; }
.search-form button {
  position: relative; display: flex; align-items: center; justify-content: center;
  padding: 0 16px; background: transparent; color: var(--black);
  border: none; cursor: pointer;
}
.search-form button::before {
  content: ""; position: absolute; left: 0; top: 25%; bottom: 25%; width: 1px;
  background: var(--border);
}
.search-form button svg { width: 18px; height: 18px; flex-shrink: 0; }
.search-form button:hover { color: var(--green); }
.search-form button:focus-visible { outline: 3px solid var(--focus); outline-offset: -3px; }

/* -- Hero search: amber CTA button variant ------------ */
.hero .search-form { border: none; background: none; gap: 8px; }
.hero .search-form input[type="search"] {
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--white);
}
.hero .search-form button {
  border-radius: var(--radius); background: #fea439; color: var(--black);
}
.hero .search-form button::before { display: none; }
.hero .search-form button:hover { background: #fc8713; color: var(--black); }

/* -- Section card grid -------------------------------- */
.chapter-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px; margin-top: 30px;
}
.chapter-card {
  display: flex; flex-direction: column;
  position: relative;
  background: var(--white); border: none; border-radius: var(--radius); padding: 20px;
  box-shadow: var(--shadow-1);
  text-decoration: none; color: inherit;
  transition: box-shadow .15s;
}
.chapter-card:hover {
  box-shadow: var(--shadow-2);
  text-decoration: none;
}
.chapter-card:visited { color: inherit; }

/* Teardrop icon badge (EC "navigation list -- illustration" pattern) */
.chapter-card__head { display: flow-root; }
.chapter-card__icon {
  float: right;
  margin: -12px -12px 8px 12px;
  width: 64px; height: 64px;
  border-radius: 100px 0 100px 100px;
  background: var(--icon-bg);
  display: flex; align-items: center; justify-content: center;
  color: var(--green);
  shape-outside: margin-box;
}
.chapter-card__icon svg { width: 32px; height: 32px; }

.chapter-card__num {
  font-size: .75rem; color: var(--secondary);
  text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px;
}
.chapter-card__title {
  font-size: 22px; font-weight: 400; color: var(--green);
  margin-bottom: 8px; line-height: 1.3;
}
.chapter-card:hover .chapter-card__title { color: var(--dark-green); text-decoration: underline; }
.chapter-card__summary {
  font-size: 18px; color: #00002E; flex-grow: 1;
  margin-bottom: 12px; line-height: 1.45;
}
.chapter-card__foot {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: .8125rem; color: var(--secondary);
  padding-top: 10px; border-top: 1px solid var(--light-grey); margin-top: auto;
}

/* -- Sub-page card grid (parent landing pages) -------- */
.subpage-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px; margin-top: 24px;
}
.subpage-card {
  display: flex; flex-direction: column;
  background: var(--white); border: none; border-radius: var(--radius); padding: 20px;
  box-shadow: var(--shadow-1);
  text-decoration: none; color: inherit;
  transition: box-shadow .15s;
}
.subpage-card:hover {
  box-shadow: var(--shadow-2);
}
.subpage-card:visited { color: inherit; }
.subpage-card__title {
  font-size: 22px; font-weight: 400; color: var(--green);
  margin-bottom: 8px; line-height: 1.3;
}
.subpage-card:hover .subpage-card__title { color: var(--dark-green); text-decoration: underline; }
.subpage-card__num {
  font-size: .75rem; color: var(--secondary);
  text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px;
}
.subpage-card__excerpt {
  font-size: 18px; color: #00002E; line-height: 1.45;
}

/* -- Search results ----------------------------------- */
.search-header { margin-bottom: 24px; }
.search-header h1 { margin-bottom: 6px; }
.search-count { font-size: .9375rem; color: var(--secondary); }
.search-result { border-bottom: 1px solid var(--border); padding: 20px 0; }
.search-result:first-child { border-top: 1px solid var(--border); }
.search-result__title { font-size: 1.1875rem; font-weight: 700; margin-bottom: 4px; }
.search-result__title a { color: var(--green); }
.search-result__meta { font-size: .875rem; color: var(--secondary); margin-bottom: 8px; }
.search-result__snippet { font-size: .9375rem; }
.search-result__snippet mark { background: var(--focus); color: var(--black); padding: 0 2px; }
.no-results { padding: 40px 0; text-align: center; color: var(--secondary); }

/* -- Footnotes ---------------------------------------- */
sup.footnote-ref {
  font-size: 0.65rem; vertical-align: super; line-height: 0;
}
sup.footnote-ref a { color: var(--green); text-decoration: none; }
sup.footnote-ref a:hover { text-decoration: underline; }
.footnotes {
  border-top: 1px solid var(--border);
  margin-top: 40px; padding-top: 16px;
  font-size: 0.8rem; color: var(--secondary);
}
.footnotes hr { display: none; }
.footnotes ol { margin-left: 1.25em; }
.footnotes li { margin-bottom: 4px; line-height: 1.5; }
.footnotes a { color: var(--secondary); }

/* -- Figures ------------------------------------------ */
.figure-block {
  margin: 14px auto 18px;
  text-align: center;
}
.figure-block img {
  max-width: 100%; height: auto;
  display: block; margin: 0 auto;
  border: none;
}
.figure-block figcaption {
  margin-top: 10px;
  font-size: 0.875rem; font-style: italic;
  color: var(--secondary); text-align: center;
}

/* -- Summary of key instructions (small print) -------- */
.summary-smallprint { font-size: 0.8rem; color: #505a5f; }
.summary-smallprint p { margin-top: 6px; }
.summary-smallprint strong { color: #505a5f; font-weight: 600; }
.summary-smallprint ol { margin-top: 6px; }
.summary-smallprint li { margin-bottom: 2px; }

/* -- Footnote expand button --------------------------- */
.footnotes-overflow { list-style: decimal; }
.footnotes-show-more {
  display: inline-block; margin-top: 6px;
  background: none; border: none; padding: 0;
  color: var(--green); font-size: 0.8rem; cursor: pointer;
  text-decoration: underline; font-family: var(--font);
}
.footnotes-show-more:hover { color: var(--dark-green); }

/* -- Lettered lists ----------------------------------- */
ol.lettered-list {
  list-style-type: lower-alpha;
  margin: 0 0 18px 1.5em;
}
ol.lettered-list li { margin-bottom: 6px; }

/* -- Footer ------------------------------------------- */
.site-footer {
  background: var(--black); color: #fff;
  padding: 0 0 40px; margin-top: 70px;
}
.site-footer p { font-size: 1rem; color: #c1ccec; margin-bottom: 6px; }
.site-footer a { color: #fff; font-size: 1rem; }
.site-footer a:hover { color: #b0c6ff; }
.site-footer p.footer-smallprint {
  font-size: 0.875rem; color: #9eaee1;
  margin-bottom: 6px; line-height: 1.5;
}
.site-footer__cta { padding: 28px 0; border-bottom: 1px solid rgba(255,255,255,.15); margin-bottom: 32px; }
.site-footer__cta-inner {
  display: flex; align-items: center; justify-content: flex-start;
  flex-wrap: wrap; gap: 24px;
}
.site-footer__cta-inner span { font-size: 1.125rem; }
.site-footer__cta-btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: #fea439; color: var(--black) !important;
  font-weight: 700; font-size: 1rem;
  padding: 12px 22px; border-radius: var(--radius); text-decoration: none !important;
}
.site-footer__cta-btn:hover { background: #fc8713; }
.site-footer__columns {
  display: flex; flex-wrap: wrap; gap: 32px;
  padding-bottom: 24px;
}
.site-footer__col { flex: 1 1 180px; min-width: 160px; }
.site-footer__col--brand { flex: 1 1 220px; max-width: 260px; }
.site-footer__tagline { font-size: .9375rem; margin-top: 12px; }
.site-footer .site-footer__heading {
  font-size: .8125rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: #fff; margin-bottom: 10px;
}
.site-footer__links { list-style: none; margin: 0; padding: 0; }
.site-footer__links li { margin-bottom: 6px; }
.site-footer__logo { height: 4.5rem; width: auto; }
.site-footer__divider { border: none; border-top: 1px solid rgba(255,255,255,.15); margin: 0 0 16px; }

/* -- Homepage section dividers ------------------------ */
.home-sections > hr { margin: 2rem 0; border: none; border-top: 1px solid var(--border); }

/* -- Responsive --------------------------------------- */
@media screen and (max-width: 768px) {
  :root { --gutter: 20px; }
  .site-header__top-inner { flex-wrap: wrap; gap: 12px; padding: 8px var(--gutter); }
  .site-header__logo { height: 3.25rem; }
  .site-header__site-name { font-size: 1.125rem; }
  .header-search { width: 100%; }
  .header-search input[type="search"] { flex: 1; min-width: 0; }

  .page-grid { flex-direction: column; }
  .sidebar { display: none; }
  .mobile-contents { display: block; }

  .top-nav__list { height: auto; }

  h1 { font-size: 3.25rem; }
  h2 { font-size: 2.375rem; }
  h3 { font-size: 1.5rem; }
  h4 { font-size: 1.25rem; }
  .hero h1 { font-size: 1.5rem; }

  .chapter-grid { grid-template-columns: 1fr; }
  .subpage-grid { grid-template-columns: 1fr; }
  .chapter-nav a { max-width: 100%; }
  .search-form { flex-wrap: wrap; }
  .search-form input[type="search"] { width: 100%; }
  .search-form button { width: 100%; border-top: 1px solid var(--border); }
  .search-form button::before { display: none; }
  .hero .search-form { flex-wrap: nowrap; }
  .hero .search-form input[type="search"] { width: auto; }
  .hero .search-form button { width: auto; border-top: none; }
  .hero { background-image: none; }

  .top-nav__dropdown-inner { flex-direction: column; gap: 20px; padding: 20px; max-height: calc(100vh - 100px); overflow-y: auto; }
  .top-nav__dropdown-intro {
    flex: none; padding-right: 0; padding-bottom: 20px;
    border-right: none; border-bottom: 1px solid var(--mid-grey);
  }
}
@media screen and (min-width: 769px) {
  .mobile-contents { display: none; }
}

/* -- Print -------------------------------------------- */
@media print {
  .official-strip, .site-header, .top-nav, .breadcrumbs, .sidebar,
  .mobile-contents, .chapter-nav, .back-to-top,
  .site-footer, .hero .search-form { display: none !important; }
  .page-grid { display: block; }
  .article { width: 100%; }
  body { font-size: 11pt; line-height: 1.45; color: #000; }
  h1 { font-size: 18pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; }
  h2, h3 { page-break-after: avoid; }
  p, li { orphans: 3; widows: 3; }
  a { color: inherit; }
  a[href^="http"]::after { content: " (" attr(href) ")"; font-size: .8em; color: #555; }
  table { page-break-inside: avoid; }
  thead { display: table-header-group; }
  .table-wrap { overflow-x: visible; }
  .article table th, .article-body table th {
    background: #222 !important; color: #fff !important;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
  .article table tr:nth-child(even) td, .article-body table tr:nth-child(even) td {
    background: #f5f5f5 !important;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
}
"""


# ==============================================================================
# SECTION 3 -- JavaScript
# ==============================================================================

MAIN_JS = """\
/* main.js -- sidebar highlight + scroll utilities */
(function () {
  'use strict';

  function initSidebarHighlight() {
    var nav = document.querySelector('.sidebar__nav');
    if (!nav) return;
    var links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    if (links.length <= 1) return; // nothing to distinguish -- leave unselected
    var targets = links.map(function (l) {
      return document.getElementById(l.getAttribute('href').slice(1));
    }).filter(Boolean);
    if (!targets.length) return;

    var current = 0;

    var ignoreUntil = 0;

    function setActive(idx) {
      current = idx;
      links.forEach(function (l) { l.classList.remove('is-active'); });
      if (links[current]) links[current].classList.add('is-active');
    }

    links.forEach(function (l, idx) {
      l.addEventListener('click', function () {
        ignoreUntil = Date.now() + 1000;
        setActive(idx);
      });
    });

    var io = new IntersectionObserver(function (entries) {
      if (Date.now() < ignoreUntil) return;
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var idx = targets.indexOf(e.target);
          if (idx !== -1) setActive(idx);
        }
      });
    }, { rootMargin: '-10% 0px -80% 0px', threshold: 0 });

    targets.forEach(function (t) { io.observe(t); });
  }

  function initBackToTop() {
    document.querySelectorAll('a[href="#top"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        var el = document.getElementById('top');
        if (el) el.focus({ preventScroll: true });
      });
    });
  }

  function initFootnotesExpand() {
    document.querySelectorAll('.footnotes-show-more').forEach(function (btn) {
      var overflow = btn.parentElement.querySelector('.footnotes-overflow');
      if (!overflow) return;
      var moreText = btn.textContent;
      btn.addEventListener('click', function () {
        if (overflow.hidden) {
          overflow.hidden = false;
          btn.textContent = 'Show fewer footnotes';
        } else {
          overflow.hidden = true;
          btn.textContent = moreText;
        }
      });
    });
  }

  function initTopNavDropdown() {
    var item = document.querySelector('.top-nav__item--dropdown');
    if (!item) return;
    var toggle = item.querySelector('.top-nav__dropdown-toggle');
    if (!toggle) return;

    function close() {
      item.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
    }
    function open() {
      item.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
    }

    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      if (item.classList.contains('is-open')) close(); else open();
    });
    document.addEventListener('click', function (e) {
      if (!item.contains(e.target)) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        close();
        toggle.focus();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSidebarHighlight();
    initBackToTop();
    initFootnotesExpand();
    initTopNavDropdown();
  });
}());
"""

SEARCH_JS = """\
/* search.js -- client-side full-text search */
(function () {
  'use strict';

  var INDEX_URL = 'search_index.json';
  var index = null;

  function qs(name) {
    return new URLSearchParams(window.location.search).get(name) || '';
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function score(item, q) {
    if (!q) return 0;
    var ql = q.toLowerCase();
    var tl = (item.title || '').toLowerCase();
    var sl = (item.summary || '').toLowerCase();
    var bl = (item.body || '').toLowerCase();
    var s = 0;
    if (tl === ql)             s += 100;
    else if (tl.startsWith(ql)) s += 75;
    else if (tl.includes(ql))   s += 50;
    if (sl.includes(ql)) s += 20;
    var hits = (bl.match(new RegExp(ql.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'g')) || []).length;
    s += Math.min(hits * 2, 30);
    return s;
  }

  function excerpt(body, q, max) {
    max = max || 220;
    if (!body) return '';
    var ql = q.toLowerCase();
    var idx = body.toLowerCase().indexOf(ql);
    if (idx === -1) return esc(body.slice(0, max)) + (body.length > max ? '&hellip;' : '');
    var s = Math.max(0, idx - 80);
    var e = Math.min(body.length, idx + q.length + 120);
    var out = (s > 0 ? '&hellip;' : '') + esc(body.slice(s, e)) + (e < body.length ? '&hellip;' : '');
    var safe = esc(q).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    return out.replace(new RegExp('(' + safe + ')', 'gi'), '<mark>$1</mark>');
  }

  function urlForItem(item) {
    if (item.parent) return 'chapters/' + esc(item.slug) + '.html';
    return 'chapters/' + esc(item.slug) + '.html';
  }

  function render(results, q) {
    var container = document.getElementById('search-results');
    var countEl   = document.getElementById('search-count');
    if (!container) return;
    if (!q) { container.innerHTML = ''; if (countEl) countEl.textContent = ''; return; }
    if (!results.length) {
      if (countEl) countEl.textContent = '0 results';
      container.innerHTML = '<div class="no-results"><p>No results found for <strong>' +
        esc(q) + '</strong>.</p><p>Try different terms or <a href="index.html">browse sections</a>.</p></div>';
      return;
    }
    if (countEl) countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '');
    container.innerHTML = results.map(function (item) {
      var meta = item.section_number ? 'Section ' + esc(item.section_number) : '';
      if (item.parent) meta = 'Sub-section';
      return '<div class="search-result">' +
        '<div class="search-result__title"><a href="' + urlForItem(item) + '">' + esc(item.title) + '</a></div>' +
        (meta ? '<div class="search-result__meta">' + meta + '</div>' : '') +
        (item.summary ? '<p class="search-result__snippet">' + esc(item.summary) + '</p>' : '') +
        '<p class="search-result__snippet">' + excerpt(item.body, q) + '</p>' +
        '</div>';
    }).join('');
  }

  function search(q) {
    if (!index) return [];
    return index.map(function (item) {
      return Object.assign({}, item, { _score: score(item, q) });
    }).filter(function (i) { return i._score > 0; })
      .sort(function (a, b) { return b._score - a._score; });
  }

  function init() {
    var input     = document.getElementById('search-input');
    var container = document.getElementById('search-results');
    if (!input || !container) return;

    var initial = qs('q');
    if (initial) input.value = initial;

    fetch(INDEX_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        index = data;
        if (initial) render(search(initial), initial);
      })
      .catch(function () {
        container.innerHTML = '<p>Search is temporarily unavailable.</p>';
      });

    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = input.value.trim();
        var url = new URL(window.location);
        if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
        window.history.replaceState({}, '', url);
        render(search(q), q);
      }, 200);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
}());
"""


# ==============================================================================
# SECTION 4 -- HTML base template
# ==============================================================================

def base_html(
    *,
    title: str,
    content: str,
    breadcrumbs: list[tuple[str, str | None]],
    depth: int = 0,
    sidebar_html: str = "",
    extra_js: str = "",
    page_header_html: str = "",
) -> str:
    root       = "../" * depth
    page_title = f"{h(title)} -- EU Wildlife Trade Reference Guide"

    bc_items = ""
    for i, (label, url) in enumerate(breadcrumbs):
        is_last = i == len(breadcrumbs) - 1
        if is_last:
            bc_items += f'<li><span aria-current="page">{h(label)}</span></li>\n'
        else:
            bc_items += f'<li><a href="{h(url)}">{h(label)}</a></li>\n'

    guide_dropdown_items = "".join(
        f'<li><a href="{root}chapters/{h(slug)}.html" class="top-nav__dropdown-link">{h(item_title)}</a></li>'
        for item_title, slug in _GUIDE_NAV_ITEMS
    )
    annexes_href = f'{root}chapters/{h(_ANNEXES_SLUG)}.html' if _ANNEXES_SLUG else f'{root}index.html'

    if sidebar_html:
        grid_open     = '<div class="page-grid">'
        sidebar_col   = f'<aside class="sidebar" aria-label="Page contents">{sidebar_html}</aside>'
        grid_close    = '</div>'
        article_open  = '<div class="article">'
        article_close = '</div>'
    else:
        grid_open = grid_close = sidebar_col = ""
        article_open = article_close = ""

    footer_extra = ""
    if FOOTER_TEXT:
        paras = [p.strip() for p in FOOTER_TEXT.strip().split("\n\n") if p.strip()]
        footer_extra = "\n".join(
            f'<p class="footer-smallprint">{h(p)}</p>' for p in paras
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <meta name="description" content="EU Wildlife Trade Regulations Reference Guide -- {h(title)}">
  <link rel="stylesheet" href="{root}assets/style.css">
  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg">
  <link rel="icon" type="image/x-icon" href="{root}favicon.ico">
  <link rel="apple-touch-icon" href="{root}apple-touch-icon.png">
  <meta name="theme-color" content="#00002e">
</head>
<body id="top">
<a href="#main-content" class="skip-link">Skip to main content</a>

<div class="official-strip">
  <div class="container official-strip__inner">
    <svg class="official-strip__flag" viewBox="0 0 24 16" aria-hidden="true" focusable="false">
      <rect width="24" height="16" fill="#003399"/>
      <g fill="#FFCC00">
        <circle cx="12"   cy="3"    r="0.6"/>
        <circle cx="14.5" cy="3.67" r="0.6"/>
        <circle cx="16.33" cy="5.5" r="0.6"/>
        <circle cx="17"   cy="8"    r="0.6"/>
        <circle cx="16.33" cy="10.5" r="0.6"/>
        <circle cx="14.5" cy="12.33" r="0.6"/>
        <circle cx="12"   cy="13"   r="0.6"/>
        <circle cx="9.5"  cy="12.33" r="0.6"/>
        <circle cx="7.67" cy="10.5" r="0.6"/>
        <circle cx="7"    cy="8"    r="0.6"/>
        <circle cx="7.67" cy="5.5"  r="0.6"/>
        <circle cx="9.5"  cy="3.67" r="0.6"/>
      </g>
    </svg>
    <span>A reference guide developed by the European Commission and TRAFFIC</span>
  </div>
</div>

<header class="site-header" role="banner">
  <div class="site-header__top">
    <div class="container site-header__top-inner">
      <a class="site-header__logo-link" href="https://commission.europa.eu/index_en" target="_blank" rel="noopener">
        <img class="site-header__logo" src="{root}assets/images/logo-ec-positive.svg" alt="European Commission">
      </a>
      <form class="header-search" action="{root}search.html" method="get" role="search">
        <label for="header-search-input" class="skip-link">Search</label>
        <input type="search" id="header-search-input" name="q"
               aria-label="Search the guide">
        <button type="submit">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M13.6 12.2h-.7l-.3-.3c1-1.1 1.6-2.6 1.6-4.2C14.2 4.1 11.6 1.5 8.1 1.5S2 4.1 2 7.6s2.6 6.1 6.1 6.1c1.6 0 3.1-.6 4.2-1.6l.3.3v.7l4.4 4.4 1.3-1.3-4.4-4.4zm-5.5 0c-2.5 0-4.5-2-4.5-4.5s2-4.5 4.5-4.5 4.5 2 4.5 4.5-2 4.5-4.5 4.5z" fill="currentColor"/></svg>
          <span class="sr-only">Search</span>
        </button>
      </form>
    </div>
  </div>
  <div class="site-header__banner">
    <div class="container">
      <span class="site-header__site-name">EU Wildlife Trade Regulations &ndash; Reference Guide</span>
    </div>
  </div>
</header>

<nav class="top-nav" aria-label="Main navigation">
  <div class="container">
    <ul class="top-nav__list">
      <li><a href="{root}index.html" class="top-nav__link">Home</a></li>
      <li><a href="{root}about.html" class="top-nav__link">About</a></li>
      <li class="top-nav__item--dropdown">
        <button type="button" class="top-nav__link top-nav__dropdown-toggle" aria-expanded="false">
          Reference Guide
          <svg class="top-nav__caret" viewBox="0 0 12 8" aria-hidden="true" focusable="false"><path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <div class="top-nav__dropdown">
          <div class="top-nav__dropdown-inner">
            <div class="top-nav__dropdown-intro">
              <h2>Reference Guide</h2>
              <p>Browse the sections of the EU Wildlife Trade Regulations Reference Guide.</p>
            </div>
            <ul class="top-nav__dropdown-links">
              {guide_dropdown_items}
            </ul>
          </div>
        </div>
      </li>
      <li><a href="{annexes_href}" class="top-nav__link">Annexes</a></li>
    </ul>
  </div>
</nav>

{page_header_html if page_header_html else (f'<nav class="breadcrumbs" aria-label="Breadcrumb"><ol>{bc_items}</ol></nav>' if bc_items else '')}

<div class="main-content">
  <div class="container" id="main-content">
    {grid_open}
      {sidebar_col}
      {article_open}
        {content}
      {article_close}
    {grid_close}
  </div>
</div>

<footer class="site-footer" role="contentinfo">
  <div class="site-footer__cta">
    <div class="container site-footer__cta-inner">
      <span>Help us improve this guide</span>
      <a class="site-footer__cta-btn" href="mailto:antony.bagott@traffic.org">Send feedback <span aria-hidden="true">&#8599;</span></a>
    </div>
  </div>
  <div class="container">
    <div class="site-footer__columns">
      <div class="site-footer__col site-footer__col--brand">
        <img class="site-footer__logo" src="{root}assets/images/logo-ec.svg" alt="European Commission">
        <p class="site-footer__tagline">A reference guide developed by the European Commission and TRAFFIC.</p>
      </div>
      <div class="site-footer__col">
        <p class="site-footer__heading">Reference Guide</p>
        <ul class="site-footer__links">
          <li><a href="{root}index.html">Home</a></li>
          <li><a href="{root}about.html">About</a></li>
          <li><a href="{root}search.html">Search</a></li>
          <li><a href="{root}{PDF_FILENAME}">Download PDF</a></li>
        </ul>
      </div>
      <div class="site-footer__col">
        <p class="site-footer__heading">Contact</p>
        <ul class="site-footer__links">
          <li><a href="mailto:antony.bagott@traffic.org">Send feedback</a></li>
        </ul>
      </div>
    </div>
    <hr class="site-footer__divider">
    {footer_extra}
  </div>
</footer>

<script src="{root}assets/main.js"></script>
{extra_js}
<script src="{root}assets/chatbot.js"></script>
<script>
  ChatbotWidget.init({{
    apiUrl: 'https://chatbot-api-khaki.vercel.app/api/chat',
    title: 'Wildlife Trade Regulations Assistant',
    placeholder: 'Ask about permits, forms, species listings…'
  }});
</script>
</body>
</html>"""


# ==============================================================================
# SECTION 5 -- Summary generation (Claude Haiku + cache + fallback)
# ==============================================================================

def load_summaries_cache() -> dict:
    if SUMMARIES_FILE.exists():
        try:
            return json.loads(SUMMARIES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def generate_summaries(pages: list[dict]) -> dict[str, str]:
    """
    Return {slug: summary_text} for all pages.
    Reads _summaries.json as a static file — never writes to it.
    Falls back to first two sentences of body text when a key is missing.
    """
    cache = load_summaries_cache()
    summaries: dict[str, str] = {}

    for ch in pages:
        slug  = ch["slug"]
        entry = cache.get(slug, {})
        summary = entry.get("summary") if isinstance(entry, dict) else entry
        if not summary:
            summary = first_sentences(strip_markdown(ch["body"]), 2)
        summaries[slug] = summary

    return summaries


# ==============================================================================
# SECTION 6 -- Content-box and sidebar helpers
# ==============================================================================

def split_heading_ref(text: str) -> tuple[str, str]:
    """'5.1 What are the rules' -> ('5.1', 'What are the rules')."""
    m = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$", text.strip())
    if m:
        return m.group(1).rstrip("."), m.group(2)
    return "", text.strip()


def heading_items(headings: list[dict]) -> list[tuple[str, str, str]]:
    """H2 headings -> (ref, title, href) triples."""
    h2s = [hd for hd in headings if hd["level"] == 2]
    return [(*split_heading_ref(hd["text"]), f'#{hd["id"]}') for hd in h2s]


def _contents_li(ref: str, title: str, href: str) -> str:
    ref_html = f'<span class="contents-ref">{h(ref)}</span>' if ref else ""
    return f'<li>{ref_html}<a href="{h(href)}">{h(title)}</a></li>\n'


def make_mobile_contents(items: list[tuple[str, str, str]]) -> str:
    if not items:
        return ""
    li_html = "".join(_contents_li(*item) for item in items)
    return (
        '<details class="mobile-contents">'
        '<summary>Contents <span aria-hidden="true">&#9662;</span></summary>'
        f'<div class="mobile-contents__body"><ul class="contents-list">{li_html}</ul></div>'
        '</details>'
    )


def make_sidebar(headings: list[dict]) -> str:
    if not headings:
        return ""
    nav_items = ""
    for hd in headings:
        level_class = "sidebar-h3" if hd["level"] >= 3 else ""
        nav_items += (
            f'<li class="{level_class}">'
            f'<a href="#{h(hd["id"])}">{h(hd["text"])}</a>'
            f'</li>\n'
        )
    return (
        '<p class="sidebar__label">Page contents</p>'
        f'<ul class="sidebar__nav">{nav_items}</ul>'
    )


def make_page_header(
    *,
    title: str,
    breadcrumbs: list[tuple[str, str | None]],
    meta: list[str] | None = None,
) -> str:
    """ECL page-header component: breadcrumb + h1 + meta, as one full-width block."""
    bc_items = ""
    for i, (label, url) in enumerate(breadcrumbs):
        is_last = i == len(breadcrumbs) - 1
        if is_last:
            bc_items += f'<li><span aria-current="page">{h(label)}</span></li>\n'
        else:
            bc_items += f'<li><a href="{h(url)}">{h(label)}</a></li>\n'
    breadcrumb_html = (
        f'<nav class="breadcrumbs page-header__breadcrumb" aria-label="Breadcrumb"><ol>{bc_items}</ol></nav>'
        if bc_items else ""
    )

    meta_html = ""
    if meta:
        items = "".join(f'<li class="page-header__meta-item">{h(m)}</li>' for m in meta)
        meta_html = f'<ul class="page-header__meta">{items}</ul>'

    return f"""
<div class="page-header">
  <div class="container">
    {breadcrumb_html}
    {meta_html}
    <h1 class="page-header__title">{h(title)}</h1>
  </div>
</div>
"""


def make_prev_next(prev: dict | None, next: dict | None) -> str:
    nav = '<div class="chapter-nav">'
    if prev:
        nav += (
            f'<a href="{h(prev["slug"])}.html" class="chapter-nav__prev">'
            f'<span class="chapter-nav__label">Previous</span>'
            f'<span class="chapter-nav__title">{h(prev["title"])}</span>'
            f'</a>'
        )
    if next:
        nav += (
            f'<a href="{h(next["slug"])}.html" class="chapter-nav__next">'
            f'<span class="chapter-nav__label">Next</span>'
            f'<span class="chapter-nav__title">{h(next["title"])}</span>'
            f'</a>'
        )
    nav += '</div>'
    return nav


# ==============================================================================
# SECTION 7 -- Page builders
# ==============================================================================

def build_simple_section(ch: dict, nav_sections: list[dict]) -> str:
    """Sections 2, 5-12: full article with sidebar, contents box, prev/next."""
    rendered = _link_figure_table_refs(
        _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=1), depth=1),
        depth=1,
    )
    headings = extract_headings(rendered)
    items    = heading_items(headings)

    mobile_contents = make_mobile_contents(items)
    sidebar_html    = make_sidebar(headings)

    idx  = next((i for i, s in enumerate(nav_sections) if s["slug"] == ch["slug"]), -1)
    prev = nav_sections[idx - 1] if idx > 0 else None
    nxt  = nav_sections[idx + 1] if idx < len(nav_sections) - 1 else None
    nav_html = make_prev_next(prev, nxt)

    label_text = _section_label_text(ch)
    page_header_html = make_page_header(
        title=ch["title"],
        breadcrumbs=[("Home", "../index.html"), (ch["title"], None)],
        meta=[label_text] if label_text else None,
    )
    content = f"""
{mobile_contents}
<article class="article-body">
  {rendered}
</article>
{nav_html}
<a href="#top" class="back-to-top">Back to top</a>
"""
    return base_html(
        title=ch["title"],
        content=content,
        breadcrumbs=[],
        depth=1,
        sidebar_html=sidebar_html,
        page_header_html=page_header_html,
    )


def annex_first_heading(sub: dict) -> str:
    """Return the first H2 heading text from an annex sub-page body."""
    m = re.search(r'^## (.+)$', sub["body"], re.MULTILINE)
    return m.group(1).strip() if m else ""


def build_parent_landing(ch: dict, sub_chapters: list[dict], nav_sections: list[dict], summaries: dict) -> str:
    """Landing page for parent sections (3, 4, Annexes): intro + sub-page card grid."""
    # Strip sub-section block from body. Handles three cases:
    #   - "## Sub-sections" at start of body (Section 3: no intro text)
    #   - "\n## Sub-sections" mid-body (Section 4)
    #   - Markdown link list with no ## heading (Annexes parent)
    body = ch["body"]
    m = re.search(r'(?:^|\n)## ', body)
    if m:
        body = body[:m.start()].strip()
    else:
        m2 = re.search(r'\n- \[', body)
        if m2:
            body = body[:m2.start()].strip()
    intro_html = render_markdown(body) if body else ""

    is_annexes = ch["slug"] == "annexes"

    # Sub-page cards. Landing pages are a hub of cards, not "content" --
    # they don't get a contents box or sidebar.
    cards = ""
    for sub in sub_chapters:
        href = f'{sub["slug"]}.html'
        if is_annexes:
            heading     = sub["title"]          # "Annex I", "Annex II", …
            description = annex_first_heading(sub)
            num_html    = ""
        else:
            heading     = sub["title"]
            description = summaries.get(sub["slug"]) or first_sentences(strip_markdown(sub["body"]), 2)
            ss    = sub.get("sub_section", "")
            nm    = re.match(r"^(\d+(?:\.\d+)+)", ss) if ss else None
            num_html = f'<div class="subpage-card__num">Section {h(nm.group(1))}</div>' if nm else ""
        cards += (
            f'<a class="subpage-card" href="{h(href)}">'
            f'{num_html}'
            f'<div class="subpage-card__title">{h(heading)}</div>'
            f'<div class="subpage-card__excerpt">{h(description)}</div>'
            f'</a>'
        )

    idx  = next((i for i, s in enumerate(nav_sections) if s["slug"] == ch["slug"]), -1)
    prev = nav_sections[idx - 1] if idx > 0 else None
    nxt  = nav_sections[idx + 1] if idx < len(nav_sections) - 1 else None
    nav_html = make_prev_next(prev, nxt)

    label_text = _section_label_text(ch)
    page_header_html = make_page_header(
        title=ch["title"],
        breadcrumbs=[("Home", "../index.html"), (ch["title"], None)],
        meta=[label_text] if label_text else None,
    )
    content = f"""
<article class="article-body">
  {intro_html}
</article>
<div class="subpage-grid">{cards}</div>
{nav_html}
<a href="#top" class="back-to-top">Back to top</a>
"""
    return base_html(
        title=ch["title"],
        content=content,
        breadcrumbs=[],
        depth=1,
        page_header_html=page_header_html,
    )


def build_sub_page(ch: dict, parent: dict, siblings: list[dict]) -> str:
    """Individual sub-page within Section 3, 4, or Annexes."""
    rendered = _link_figure_table_refs(
        _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=1), depth=1),
        depth=1,
    )
    headings = extract_headings(rendered)
    items    = heading_items(headings)

    mobile_contents = make_mobile_contents(items)
    sidebar_html = make_sidebar(headings)

    idx  = next((i for i, s in enumerate(siblings) if s["slug"] == ch["slug"]), -1)
    prev = siblings[idx - 1] if idx > 0 else None
    nxt  = siblings[idx + 1] if idx < len(siblings) - 1 else None
    nav_html = make_prev_next(prev, nxt)

    label_text = _section_label_text(ch)
    page_header_html = make_page_header(
        title=ch["title"],
        breadcrumbs=[
            ("Home", "../index.html"),
            (parent["title"], f"{h(parent['slug'])}.html"),
            (ch["title"], None),
        ],
        meta=[label_text] if label_text else None,
    )
    content = f"""
{mobile_contents}
<article class="article-body">
  {rendered}
</article>
{nav_html}
<a href="#top" class="back-to-top">Back to top</a>
"""
    return base_html(
        title=ch["title"],
        content=content,
        breadcrumbs=[],
        depth=1,
        sidebar_html=sidebar_html,
        page_header_html=page_header_html,
    )


def build_about_page(ch: dict) -> str:
    """About page (Section 1 content) at site root."""
    rendered = _link_figure_table_refs(
        _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=0), depth=0),
        depth=0,
    )
    headings = extract_headings(rendered)
    items    = heading_items(headings)
    sidebar_html = make_sidebar(headings)

    page_header_html = make_page_header(
        title="About",
        breadcrumbs=[("Home", "index.html"), ("About", None)],
    )
    content = f"""
{make_mobile_contents(items)}
<article class="article-body">
  {rendered}
</article>
<a href="#top" class="back-to-top">Back to top</a>
"""
    return base_html(
        title="About",
        content=content,
        breadcrumbs=[],
        depth=0,
        sidebar_html=sidebar_html,
        page_header_html=page_header_html,
    )


# Homepage card icons -- Phosphor Icons (phosphor-icons/core, MIT licence),
# "regular" weight, viewBox 0 0 256 256. Keyed by section_number (0 = Annexes).
_CARD_ICON_PATHS: dict[int, str] = {
    1: "M232,48H160a40,40,0,0,0-32,16A40,40,0,0,0,96,48H24a8,8,0,0,0-8,8V200a8,8,0,0,0,8,8H96a24,24,0,0,1,24,24,8,8,0,0,0,16,0,24,24,0,0,1,24-24h72a8,8,0,0,0,8-8V56A8,8,0,0,0,232,48ZM96,192H32V64H96a24,24,0,0,1,24,24V200A39.81,39.81,0,0,0,96,192Zm128,0H160a39.81,39.81,0,0,0-24,8V88a24,24,0,0,1,24-24h64Z",  # book-open (About)
    2: "M212,80a28,28,0,1,0,28,28A28,28,0,0,0,212,80Zm0,40a12,12,0,1,1,12-12A12,12,0,0,1,212,120ZM72,108a28,28,0,1,0-28,28A28,28,0,0,0,72,108ZM44,120a12,12,0,1,1,12-12A12,12,0,0,1,44,120ZM92,88A28,28,0,1,0,64,60,28,28,0,0,0,92,88Zm0-40A12,12,0,1,1,80,60,12,12,0,0,1,92,48Zm72,40a28,28,0,1,0-28-28A28,28,0,0,0,164,88Zm0-40a12,12,0,1,1-12,12A12,12,0,0,1,164,48Zm23.12,100.86a35.3,35.3,0,0,1-16.87-21.14,44,44,0,0,0-84.5,0A35.25,35.25,0,0,1,69,148.82,40,40,0,0,0,88,224a39.48,39.48,0,0,0,15.52-3.13,64.09,64.09,0,0,1,48.87,0,40,40,0,0,0,34.73-72ZM168,208a24,24,0,0,1-9.45-1.93,80.14,80.14,0,0,0-61.19,0,24,24,0,0,1-20.71-43.26,51.22,51.22,0,0,0,24.46-30.67,28,28,0,0,1,53.78,0,51.27,51.27,0,0,0,24.53,30.71A24,24,0,0,1,168,208Z",  # paw-print
    3: "M216,48V96a8,8,0,0,1-16,0V67.31l-50.34,50.35a8,8,0,0,1-11.32-11.32L188.69,56H160a8,8,0,0,1,0-16h48A8,8,0,0,1,216,48ZM106.34,138.34,56,188.69V160a8,8,0,0,0-16,0v48a8,8,0,0,0,8,8H96a8,8,0,0,0,0-16H67.31l50.35-50.34a8,8,0,0,0-11.32-11.32Z",  # arrows-out-simple
    4: "M232,216H208V40a16,16,0,0,0-16-16H64A16,16,0,0,0,48,40V216H24a8,8,0,0,0,0,16H232a8,8,0,0,0,0-16Zm-64,0H64V40H168Zm-40-84a12,12,0,1,1,12,12A12,12,0,0,1,128,132Z",  # door-open-fill (Derogations)
    5: "M213.66,53.66,163.31,104H192a8,8,0,0,1,0,16H144a8,8,0,0,1-8-8V64a8,8,0,0,1,16,0V92.69l50.34-50.35a8,8,0,0,1,11.32,11.32ZM112,136H64a8,8,0,0,0,0,16H92.69L42.34,202.34a8,8,0,0,0,11.32,11.32L104,163.31V192a8,8,0,0,0,16,0V144A8,8,0,0,0,112,136Z",  # arrows-in-simple
    6: "M223.45,40.07a8,8,0,0,0-7.52-7.52C139.8,28.08,78.82,51,52.82,94a87.09,87.09,0,0,0-12.76,49c.57,15.92,5.21,32,13.79,47.85l-19.51,19.5a8,8,0,0,0,11.32,11.32l19.5-19.51C81,210.73,97.09,215.37,113,215.94q1.67.06,3.33.06A86.93,86.93,0,0,0,162,203.18C205,177.18,227.93,116.21,223.45,40.07ZM153.75,189.5c-22.75,13.78-49.68,14-76.71.77l88.63-88.62a8,8,0,0,0-11.32-11.32L65.73,179c-13.19-27-13-54,.77-76.71,22.09-36.47,74.6-56.44,141.31-54.06C210.2,114.89,190.22,167.41,153.75,189.5Z",  # leaf
    7: "M243.31,136,144,36.69A15.86,15.86,0,0,0,132.69,32H40a8,8,0,0,0-8,8v92.69A15.86,15.86,0,0,0,36.69,144L136,243.31a16,16,0,0,0,22.63,0l84.68-84.68a16,16,0,0,0,0-22.63Zm-96,96L48,132.69V48h84.69L232,147.31ZM96,84A12,12,0,1,1,84,72,12,12,0,0,1,96,84Z",  # tag
    8: "M227.31,73.37,182.63,28.68a16,16,0,0,0-22.63,0L36.69,152A15.86,15.86,0,0,0,32,163.31V208a16,16,0,0,0,16,16H92.69A15.86,15.86,0,0,0,104,219.31L227.31,96a16,16,0,0,0,0-22.63ZM92.69,208H48V163.31l88-88L180.69,120ZM192,108.68,147.31,64l24-24L216,84.68Z",  # pencil-simple (merged permits/certificates)
    9: "M160,112h48a16,16,0,0,0,16-16V48a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16V64H128a24,24,0,0,0-24,24v32H72v-8A16,16,0,0,0,56,96H24A16,16,0,0,0,8,112v32a16,16,0,0,0,16,16H56a16,16,0,0,0,16-16v-8h32v32a24,24,0,0,0,24,24h16v16a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V160a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16v16H128a8,8,0,0,1-8-8V88a8,8,0,0,1,8-8h16V96A16,16,0,0,0,160,112ZM56,144H24V112H56v32Zm104,16h48v48H160Zm0-112h48V96H160Z",  # tree-structure (merged CITES duties + enforcement)
    10: "M168,152a8,8,0,0,1-8,8H96a8,8,0,0,1,0-16h64A8,8,0,0,1,168,152Zm-8-40H96a8,8,0,0,0,0,16h64a8,8,0,0,0,0-16Zm56-64V216a16,16,0,0,1-16,16H56a16,16,0,0,1-16-16V48A16,16,0,0,1,56,32H92.26a47.92,47.92,0,0,1,71.48,0H200A16,16,0,0,1,216,48ZM96,64h64a32,32,0,0,0-64,0ZM200,48H173.25A47.93,47.93,0,0,1,176,64v8a8,8,0,0,1-8,8H88a8,8,0,0,1-8-8V64a47.93,47.93,0,0,1,2.75-16H56V216H200Z",  # clipboard-text
    0: "M224,128a8,8,0,0,1-8,8H128a8,8,0,0,1,0-16h88A8,8,0,0,1,224,128ZM128,72h88a8,8,0,0,0,0-16H128a8,8,0,0,0,0,16Zm88,112H128a8,8,0,0,0,0,16h88a8,8,0,0,0,0-16ZM82.34,42.34,56,68.69,45.66,58.34A8,8,0,0,0,34.34,69.66l16,16a8,8,0,0,0,11.32,0l32-32A8,8,0,0,0,82.34,42.34Zm0,64L56,132.69,45.66,122.34a8,8,0,0,0-11.32,11.32l16,16a8,8,0,0,0,11.32,0l32-32a8,8,0,0,0-11.32-11.32Zm0,64L56,196.69,45.66,186.34a8,8,0,0,0-11.32,11.32l16,16a8,8,0,0,0,11.32,0l32-32a8,8,0,0,0-11.32-11.32Z",  # list-checks (Annexes, section_number 0)
}

# Icon colour (the teardrop badge background always stays --icon-bg blue;
# only the icon glyph colour varies, drawn from the EC brand palette).
_CARD_ICON_COLORS: dict[int, str] = {
    1:  "#0046FF",  # primary-600 blue -- book-open (About)
    2:  "#A63F82",  # pink (was purple-700) -- paw-print
    3:  "#696984",  # grey-600 -- arrows-out-simple
    4:  "#FC8713",  # secondary-600 orange -- door-open-fill (Derogations)
    5:  "#0046FF",  # primary-600 blue -- arrows-in-simple
    6:  "#049E62",  # success-700 green -- leaf
    7:  "#696984",  # grey-600 -- tag
    8:  "#A63F82",  # pink (was purple-700) -- pencil-simple (merged permits/certificates)
    9:  "#FC8713",  # secondary-600 orange -- tree-structure (merged CITES duties + enforcement)
    10: "#0046FF",  # primary-600 blue -- clipboard-text
    0:  "#66439A",  # purple-700 -- list-checks (Annexes, section_number 0)
}

# Compass -- used for the "1.1 Overview" homepage sub-card (About section).
_ICON_COMPASS_PATH = (
    "M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,"
    "88.1,0,0,1,128,216ZM172.42,72.84l-64,32a8.05,8.05,0,0,0-3.58,3.58l-32,64A8,8,0,0,0,80,184a"
    "8.1,8.1,0,0,0,3.58-.84l64-32a8.05,8.05,0,0,0,3.58-3.58l32-64a8,8,0,0,0-10.74-10.74ZM138,138,"
    "97.89,158.11,118,118l40.15-20.07Z"
)


def _make_card(ch: dict, summaries: dict, href: str | None = None) -> str:
    snum = ch["section_number"]
    label = f"Section {h(snum)}" if snum and snum <= 10 else "Annexes"
    summary = summaries.get(ch["slug"]) or ""
    actual_href = href if href is not None else f"chapters/{h(ch['slug'])}.html"

    icon_path = _CARD_ICON_PATHS.get(snum)
    icon_color = _CARD_ICON_COLORS.get(snum, "var(--green)")
    icon_html = (
        f'<div class="chapter-card__icon" aria-hidden="true" style="color:{icon_color}">'
        f'<svg viewBox="0 0 256 256" fill="currentColor"><path d="{icon_path}"/></svg>'
        f'</div>'
    ) if icon_path else ""
    card_class = "chapter-card chapter-card--icon" if icon_path else "chapter-card"

    return (
        f'<a class="{card_class}" href="{actual_href}">'
        f'<div class="chapter-card__head">'
        f'{icon_html}'
        f'<div class="chapter-card__num">{label}</div>'
        f'<div class="chapter-card__title">{h(ch["title"])}</div>'
        f'</div>'
        f'<div class="chapter-card__summary">{h(summary)}</div>'
        f'</a>'
    )


def _make_about_subcard(ref: str, title: str, href: str, summary: str,
                         icon_path: str, icon_color: str) -> str:
    """Homepage card for an About sub-section (1.1, 1.2, ...) anchored on about.html."""
    icon_html = (
        f'<div class="chapter-card__icon" aria-hidden="true" style="color:{icon_color}">'
        f'<svg viewBox="0 0 256 256" fill="currentColor"><path d="{icon_path}"/></svg>'
        f'</div>'
    ) if icon_path else ""
    card_class = "chapter-card chapter-card--icon" if icon_path else "chapter-card"

    return (
        f'<a class="{card_class}" href="{h(href)}">'
        f'<div class="chapter-card__head">'
        f'{icon_html}'
        f'<div class="chapter-card__num">Section {h(ref)}</div>'
        f'<div class="chapter-card__title">{h(title)}</div>'
        f'</div>'
        f'<div class="chapter-card__summary">{h(summary)}</div>'
        f'</a>'
    )


def build_index_page(nav_sections: list[dict], summaries: dict,
                     about_ch: dict | None = None) -> str:
    """Homepage: hero + three grouped card sections (About / Reference Guide / Annexes)."""

    # Sections 2–10 go to Reference Guide; section 11+ go to Annexes
    guide_sections = [c for c in nav_sections if 0 < c["section_number"] <= 10]
    annex_sections = [c for c in nav_sections if c["section_number"] == 0 or c["section_number"] > 10]

    about_cards  = (
        _make_about_subcard(
            "1.1", "Overview", "about.html#11-overview",
            "The EU's implementation of CITES, the Regulations currently in force, "
            "and the EU action plan against wildlife trafficking.",
            icon_path=_ICON_COMPASS_PATH, icon_color="#696984",  # grey-600 -- compass
        )
        + _make_about_subcard(
            "1.2", "How do I use this guide?", "about.html#12-how-do-i-use-this-guide",
            "Who this guide is for, how its sections and annexes are organised, "
            "and tips for finding what you need.",
            icon_path=_CARD_ICON_PATHS[1], icon_color=_CARD_ICON_COLORS[1],  # book-open
        )
    ) if about_ch else ""
    guide_cards  = "".join(_make_card(c, summaries) for c in guide_sections)
    annex_cards  = "".join(_make_card(c, summaries) for c in annex_sections)

    def section_block(heading: str, cards: str) -> str:
        return (
            f'<h2 style="margin-top:0;border-top:none;padding-top:0">{heading}</h2>'
            f'<div class="chapter-grid">{cards}</div>'
        )

    content = f"""
<div class="hero">
  <div class="container">
    <h1>EU Wildlife Trade Regulations<br>Reference Guide</h1>
    <p class="hero__lead">
      A comprehensive reference guide on the rules governing the trade of wildlife
      into, out of, and within the European Union.
    </p>
    <form class="search-form" action="search.html" method="get" role="search">
      <label for="home-search" class="skip-link">Search</label>
      <input type="search" id="home-search" name="q"
             placeholder="Search the guide&hellip;" aria-label="Search the guide">
      <button type="submit">
        <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M13.6 12.2h-.7l-.3-.3c1-1.1 1.6-2.6 1.6-4.2C14.2 4.1 11.6 1.5 8.1 1.5S2 4.1 2 7.6s2.6 6.1 6.1 6.1c1.6 0 3.1-.6 4.2-1.6l.3.3v.7l4.4 4.4 1.3-1.3-4.4-4.4zm-5.5 0c-2.5 0-4.5-2-4.5-4.5s2-4.5 4.5-4.5 4.5 2 4.5 4.5-2 4.5-4.5 4.5z" fill="currentColor"/></svg>
        <span class="sr-only">Search</span>
      </button>
    </form>
  </div>
</div>

<div class="home-sections" style="padding-top:30px">
  {section_block("About", about_cards) if about_cards else ""}
  {"<hr>" if about_cards else ""}
  {section_block("Reference Guide", guide_cards)}
  <hr>
  {section_block("Annexes", annex_cards) if annex_cards else ""}
</div>
"""
    return base_html(
        title="Home",
        content=content,
        breadcrumbs=[],
        depth=0,
    )


def build_search_page() -> str:
    content = """
<h1>Search</h1>
<div class="search-header">
  <form class="search-form" action="search.html" method="get"
        role="search" style="margin-bottom:16px">
    <label for="search-input" class="skip-link">Search</label>
    <input type="search" id="search-input" name="q"
           placeholder="Search the guide&hellip;" aria-label="Search the guide" autofocus>
    <button type="submit">
      <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M13.6 12.2h-.7l-.3-.3c1-1.1 1.6-2.6 1.6-4.2C14.2 4.1 11.6 1.5 8.1 1.5S2 4.1 2 7.6s2.6 6.1 6.1 6.1c1.6 0 3.1-.6 4.2-1.6l.3.3v.7l4.4 4.4 1.3-1.3-4.4-4.4zm-5.5 0c-2.5 0-4.5-2-4.5-4.5s2-4.5 4.5-4.5 4.5 2 4.5 4.5-2 4.5-4.5 4.5z" fill="currentColor"/></svg>
      <span class="sr-only">Search</span>
    </button>
  </form>
  <p class="search-count" id="search-count" aria-live="polite"></p>
</div>
<div id="search-results" aria-live="polite"></div>
<noscript><p>JavaScript is required for search.</p></noscript>
"""
    return base_html(
        title="Search",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Search", None)],
        depth=0,
        extra_js='<script src="assets/search.js"></script>',
    )


def build_404_page() -> str:
    content = """
<div style="padding:40px 0">
  <h1>Page not found</h1>
  <p>If you typed the web address, check it is correct.</p>
  <p>If you pasted the web address, check you copied the entire address.</p>
  <p><a href="index.html">Go to the homepage</a> or <a href="search.html">search the guide</a>.</p>
</div>
"""
    return base_html(
        title="Page not found",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Page not found", None)],
        depth=0,
    )


# ==============================================================================
# SECTION 8 -- Search index builder
# ==============================================================================

def build_search_index(all_pages: list[dict], summaries: dict) -> list[dict]:
    return [
        {
            "slug":           ch["slug"],
            "title":          ch["title"],
            "section_number": ch["section_number"],
            "parent":         ch["parent"],
            "summary":        summaries.get(ch["slug"], ""),
            "body":           strip_markdown(ch["body"]),
        }
        for ch in all_pages
    ]


# ==============================================================================
# SECTION 8b -- PDF generation (whole guide, single downloadable file)
# ==============================================================================
#
# Reuses the exact same render pipeline as the HTML chapter pages (render_markdown
# -> autolink_xrefs -> _replace_figures -> _link_figure_table_refs) so the PDF's
# prose, cross-references, figures and tables are identical to the live site --
# it is simply the same content re-flowed into one paginated document instead of
# many linked pages. Regenerated every time build_site() runs.

PDF_CSS = """
@page {
  size: A4;
  margin: 25mm 20mm 20mm 20mm;
  @top-center {
    content: string(chapter);
    font-family: "Inter", Arial, sans-serif;
    font-size: 9pt;
    color: #696984;
  }
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-family: "Inter", Arial, sans-serif;
    font-size: 9pt;
    color: #696984;
  }
}
@page :first {
  @top-center { content: ""; }
  @bottom-center { content: ""; }
}

* { box-sizing: border-box; }

body {
  font-family: "Inter", Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.5;
  color: #00002e;
}

h1, h2, h3, h4 { color: #00002e; page-break-after: avoid; }
h1 { font-size: 20pt; margin: 0 0 10mm; }
h2 { font-size: 14pt; margin: 10mm 0 4mm; }
h3 { font-size: 12pt; margin: 8mm 0 3mm; }
p, li { orphans: 3; widows: 3; margin: 0 0 3mm; }
a { color: #0046ff; text-decoration: none; }

.pdf-cover { text-align: center; margin-top: 60mm; }
.pdf-cover__kicker {
  text-transform: uppercase; letter-spacing: 0.08em;
  font-size: 10pt; color: #696984; margin-bottom: 12mm;
}
.pdf-cover h1 { font-size: 30pt; margin-bottom: 4mm; }
.pdf-cover__subtitle { font-size: 16pt; color: #696984; margin-bottom: 20mm; }
.pdf-cover__date { font-size: 10pt; color: #696984; }

.pdf-toc { page-break-before: page; page-break-after: page; }
.pdf-toc h1 { font-size: 18pt; }
.pdf-toc ol { list-style: none; margin: 0; padding: 0; }
.pdf-toc > ol > li { margin: 3mm 0 0; font-weight: 600; }
.pdf-toc ol ol { margin-top: 1mm; }
.pdf-toc ol ol li { font-weight: 400; font-size: 9.5pt; margin: 1mm 0 0 6mm; }
.pdf-toc a { display: block; color: #00002e; }
.pdf-toc a::after {
  content: leader(".") target-counter(attr(href), page);
  color: #696984;
}

.pdf-chapter { page-break-before: page; }
.pdf-subchapter { page-break-before: auto; }

table { border-collapse: collapse; width: 100%; margin: 4mm 0 6mm; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 0.5pt solid #d4d4dc; padding: 2mm 3mm; text-align: left; vertical-align: top; }
th { background: #ededf0; font-weight: 600; }
tr:nth-child(even) td { background: #f6f6f8; }

.figure-block { margin: 6mm 0; page-break-inside: avoid; text-align: center; }
.figure-block img { max-width: 100%; }
.figure-block figcaption { font-size: 9pt; color: #696984; margin-top: 2mm; }

.summary-smallprint { font-size: 9pt; color: #696984; border-left: 2pt solid #d4d4dc; padding-left: 4mm; }
ol.lettered-list { padding-left: 6mm; }
.table-label { font-weight: 600; margin-top: 4mm; }
"""

_PDF_HREF_CHAPTER_RE = re.compile(r'href="chapters/([a-zA-Z0-9_\-]+)\.html(#[^"]*)?"')
_PDF_HREF_FRAG_RE    = re.compile(r'href="#([^"]*)"')
_PDF_ID_RE           = re.compile(r'\bid="([^"]*)"')


def _css_str(text: str) -> str:
    """Escape a string for use inside a single-quoted CSS string literal."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _pdf_namespace_refs(html: str, slug: str) -> str:
    """Namespace ids/hrefs so this page's anchors stay unique once merged into
    one PDF document, and turn cross-page site links into in-document jumps."""

    def _chapter_href(m: re.Match) -> str:
        target_slug, frag = m.group(1), m.group(2)
        anchor = f"--{frag[1:]}" if frag else ""
        return f'href="#pdf-{target_slug}{anchor}"'

    html = _PDF_HREF_CHAPTER_RE.sub(_chapter_href, html)
    html = _PDF_HREF_FRAG_RE.sub(lambda m: f'href="#pdf-{slug}--{m.group(1)}"', html)
    html = _PDF_ID_RE.sub(lambda m: f'id="pdf-{slug}--{m.group(1)}"', html)
    return html


def _wrap_pdf_section(ch: dict, body_html: str, *, top_level: bool) -> str:
    ref = _section_ref_number(ch)
    label = f"{ref} {ch['title']}" if ref else ch["title"]
    css_class = "pdf-chapter" if top_level else "pdf-subchapter"
    return (
        f'<section class="{css_class}" id="pdf-{h(ch["slug"])}">'
        f'<h1 style="string-set: chapter \'{_css_str(label)}\'">{h(label)}</h1>'
        f'{body_html}'
        f'</section>'
    )


def _prepare_pdf_chunk(ch: dict, *, top_level: bool = True) -> str:
    """Full-body page (simple section, sub-page, or the About section)."""
    rendered = _link_figure_table_refs(
        _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=0), depth=0),
        depth=0,
    )
    rendered = _pdf_namespace_refs(rendered, ch["slug"])
    return _wrap_pdf_section(ch, rendered, top_level=top_level)


def _prepare_pdf_parent_chunk(ch: dict) -> str:
    """Intro-only page for a parent section (3, 4, Annexes) -- its sub-pages
    follow immediately after as their own chunks, so no card grid is needed."""
    body = ch["body"]
    m = re.search(r'(?:^|\n)## ', body)
    if m:
        body = body[:m.start()].strip()
    else:
        m2 = re.search(r'\n- \[', body)
        if m2:
            body = body[:m2.start()].strip()
    intro_html = _pdf_namespace_refs(render_markdown(body), ch["slug"]) if body else ""
    return _wrap_pdf_section(ch, intro_html, top_level=True)


def build_pdf_toc(
    about_ch: dict | None,
    guide_sections: list[dict],
    annex_sections: list[dict],
    by_slug: dict,
) -> str:
    def _entry(ch: dict, sub_chs: list[dict] | None = None) -> str:
        ref = _section_ref_number(ch)
        label = f"{ref} {ch['title']}" if ref else ch["title"]
        li = f'<li><a href="#pdf-{h(ch["slug"])}">{h(label)}</a>'
        if sub_chs:
            li += f'<ol>{"".join(_entry(sub) for sub in sub_chs)}</ol>'
        return li + "</li>"

    items = _entry(about_ch) if about_ch else ""
    for ch in guide_sections + annex_sections:
        sub_chs = [by_slug[s] for s in (ch.get("sub_pages") or []) if s in by_slug]
        items += _entry(ch, sub_chs)

    return f'<nav class="pdf-toc"><h1>Contents</h1><ol>{items}</ol></nav>'


PDF_FILENAME = "EU-Wildlife-Trade-Reference-Guide.pdf"


def generate_pdf(nav_sections: list[dict], all_sub: list[dict], about_ch: dict | None) -> None:
    """Render the whole guide, in the same order as the homepage, to one PDF."""
    try:
        import weasyprint
    except (ImportError, OSError) as exc:
        console.print(
            "  [yellow]![/yellow] Skipping PDF -- WeasyPrint isn't usable here "
            f"({exc.__class__.__name__}: {exc})\n"
            "    Install with 'pip install weasyprint'. On Windows it also needs the "
            "GTK3 runtime (Pango/HarfBuzz/fontconfig) -- see "
            "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows"
        )
        return

    by_slug = {ch["slug"]: ch for ch in nav_sections + all_sub}
    # Same split the homepage uses (build_index_page): sections 2-10 in the
    # Reference Guide group, everything else (Annexes, section_number 0 or >10) last.
    guide_sections = [c for c in nav_sections if 0 < c["section_number"] <= 10]
    annex_sections = [c for c in nav_sections if c["section_number"] == 0 or c["section_number"] > 10]

    chunks: list[str] = []
    if about_ch:
        chunks.append(_prepare_pdf_chunk(about_ch))
    for ch in guide_sections + annex_sections:
        sub_slugs = ch.get("sub_pages") or []
        if sub_slugs:
            chunks.append(_prepare_pdf_parent_chunk(ch))
            for sub_slug in sub_slugs:
                sub = by_slug.get(sub_slug)
                if sub:
                    chunks.append(_prepare_pdf_chunk(sub, top_level=False))
        else:
            chunks.append(_prepare_pdf_chunk(ch))

    toc_html = build_pdf_toc(about_ch, guide_sections, annex_sections, by_slug)
    today = date.today()
    cover_html = f"""
<section class="pdf-cover">
  <p class="pdf-cover__kicker">European Commission &amp; TRAFFIC</p>
  <h1>EU Wildlife Trade Regulations</h1>
  <p class="pdf-cover__subtitle">Reference Guide</p>
  <p class="pdf-cover__date">Generated on {today.day} {today:%B %Y} from the online reference guide</p>
</section>
"""

    doc_html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>EU Wildlife Trade Regulations -- Reference Guide</title></head>
<body>
{cover_html}
{toc_html}
{"".join(chunks)}
</body>
</html>"""

    out_path = SITE_DIR / PDF_FILENAME
    base_url = SITE_DIR.resolve().as_uri() + "/"
    weasyprint.HTML(string=doc_html, base_url=base_url).write_pdf(
        out_path, stylesheets=[weasyprint.CSS(string=PDF_CSS)]
    )
    console.print(f"  [green]+[/green] {out_path.name} ({out_path.stat().st_size / 1024:.0f} KB)")


# ==============================================================================
# SECTION 9 -- Build orchestration
# ==============================================================================

def build_site() -> tuple[list[dict], list[dict], dict]:
    global FOOTER_TEXT, _GUIDE_NAV_ITEMS, _ANNEXES_SLUG
    console.rule("[bold blue]Building site[/bold blue]")

    # -- Read all markdown files --------------------------------------------------
    md_files = sorted(INPUT_DIR.glob("*.md"))
    if not md_files:
        console.print(f"[red]No .md files found in {INPUT_DIR}/[/red]")
        sys.exit(1)

    all_parsed = [parse_md_file(p) for p in md_files]

    # -- Load footer text ---------------------------------------------------------
    footer_path = INPUT_DIR / "_footer_content.md"
    if footer_path.exists():
        FOOTER_TEXT = footer_path.read_text(encoding="utf-8").strip()
        console.print("  [green]+[/green] Loaded _footer_content.md")

    # -- Categorise pages ---------------------------------------------------------
    by_slug: dict[str, dict] = {ch["slug"]: ch for ch in all_parsed}

    about_ch    = None
    nav_sections: list[dict] = []   # top-level sections shown on homepage (2-12 + Annexes)
    all_sub     : list[dict] = []   # all sub-pages (for building HTML + search)
    simple_pages: list[dict] = []   # sections rendered as plain article pages

    for ch in all_parsed:
        if ch["slug"].startswith("_"):
            continue
        if ch["exclude_from_nav"]:
            continue
        if ch["section_number"] == 1:
            about_ch = ch
            continue
        if ch["parent"]:
            all_sub.append(ch)
            continue
        nav_sections.append(ch)

    nav_sections.sort(key=lambda c: (c["section_number"], c["slug"]))

    # Separate nav_sections into parents and simple
    parent_pages = [c for c in nav_sections if c["sub_pages"]]
    simple_pages = [c for c in nav_sections if not c["sub_pages"]]

    # -- Top-nav "Reference Guide" dropdown / "Annexes" link ----------------------
    # Sections 2-10 populate the Reference Guide dropdown; the Annexes parent page
    # (section_number 0 or > 10) is linked to directly.
    _GUIDE_NAV_ITEMS = [
        (c["title"], c["slug"]) for c in nav_sections if 0 < c["section_number"] <= 10
    ]
    _annexes_ch = next(
        (c for c in nav_sections if c["section_number"] == 0 or c["section_number"] > 10),
        None,
    )
    _ANNEXES_SLUG = _annexes_ch["slug"] if _annexes_ch else None

    # All pages that appear as cards (summaries needed).
    # Individual annex sub-pages are excluded — their cards use title + first heading.
    # The annexes parent page IS included so its homepage card gets a generated summary.
    pages_for_summaries = [
        c for c in nav_sections + all_sub + ([about_ch] if about_ch else [])
        if c["parent"] != "annexes"
    ]

    # -- Generate summaries -------------------------------------------------------
    console.print("  Generating summaries (cached where possible)...")
    summaries = generate_summaries(pages_for_summaries)
    console.print(f"  [green]+[/green] Summaries ready for {len(summaries)} pages")

    # -- Build cross-reference lookup (section/annex number → URL) ----------------
    build_section_lookup(nav_sections, all_sub)
    console.print(f"  [green]+[/green] Cross-reference lookup built ({len(_XREF_LOOKUP)} entries)")
    build_figure_table_lookup(nav_sections, all_sub)
    console.print(f"  [green]+[/green] Figure/table lookup built ({len(_FIGURE_PAGE_LOOKUP)} figures, {len(_TABLE_PAGE_LOOKUP)} tables)")

    # -- Create output directories ------------------------------------------------
    (SITE_DIR / "assets").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "chapters").mkdir(parents=True, exist_ok=True)

    # -- Static assets ------------------------------------------------------------
    (SITE_DIR / "assets" / "style.css").write_text(CSS,       encoding="utf-8")
    (SITE_DIR / "assets" / "main.js" ).write_text(MAIN_JS,   encoding="utf-8")
    (SITE_DIR / "assets" / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    console.print("  [green]+[/green] assets/style.css, main.js, search.js")
    _chatbot_src = Path("static") / "chatbot.js"
    if _chatbot_src.exists():
        shutil.copy(_chatbot_src, SITE_DIR / "assets" / "chatbot.js")
        console.print("  [green]+[/green] assets/chatbot.js")

    _font_src = Path("static") / "fonts" / "InterVariable.woff2"
    if _font_src.exists():
        fonts_dst = SITE_DIR / "assets" / "fonts"
        fonts_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_font_src, fonts_dst / "InterVariable.woff2")
        console.print("  [green]+[/green] assets/fonts/InterVariable.woff2")

    for _favicon_name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png"):
        _favicon_src = Path("static") / _favicon_name
        if _favicon_src.exists():
            shutil.copy2(_favicon_src, SITE_DIR / _favicon_name)
            console.print(f"  [green]+[/green] {_favicon_name}")

    # -- Images -------------------------------------------------------------------
    images_src = Path("images")
    images_dst = SITE_DIR / "assets" / "images"
    images_dst.mkdir(parents=True, exist_ok=True)
    if images_src.exists():
        copied = 0
        for img_file in images_src.glob("*"):
            if img_file.is_file():
                shutil.copy2(img_file, images_dst / img_file.name)
                copied += 1
        console.print(f"  [green]+[/green] Copied {copied} image(s) to assets/images/")

    for _logo_name in ("logo-ec.svg", "logo-ec-positive.svg"):
        _logo_src = Path("static") / _logo_name
        if _logo_src.exists():
            shutil.copy2(_logo_src, images_dst / _logo_name)
            console.print(f"  [green]+[/green] assets/images/{_logo_name}")

    _static_images_src = Path("static") / "images"
    if _static_images_src.exists():
        for img_file in _static_images_src.glob("*"):
            if img_file.is_file():
                shutil.copy2(img_file, images_dst / img_file.name)
                console.print(f"  [green]+[/green] assets/images/{img_file.name}")

    # -- GitHub Pages config ------------------------------------------------------
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (SITE_DIR / "_config.yml").write_text(
        "# Disable Jekyll processing -- site is pre-built plain HTML\ntheme: null\n",
        encoding="utf-8",
    )
    console.print("  [green]+[/green] .nojekyll, _config.yml")

    # -- Search index (all non-excluded pages) ------------------------------------
    index = build_search_index(nav_sections + all_sub, summaries)
    (SITE_DIR / "search_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    console.print("  [green]+[/green] search_index.json")

    # -- Root pages ---------------------------------------------------------------
    (SITE_DIR / "index.html").write_text(
        build_index_page(nav_sections, summaries, about_ch=about_ch), encoding="utf-8"
    )
    (SITE_DIR / "search.html").write_text(build_search_page(),  encoding="utf-8")
    (SITE_DIR / "404.html"  ).write_text(build_404_page(),     encoding="utf-8")
    console.print("  [green]+[/green] index.html, search.html, 404.html")

    # -- About page ---------------------------------------------------------------
    if about_ch:
        (SITE_DIR / "about.html").write_text(
            build_about_page(about_ch), encoding="utf-8"
        )
        console.print("  [green]+[/green] about.html")

    # -- Chapter pages ------------------------------------------------------------
    console.print("\n  Generating chapter pages...")
    generated: list[Path] = []

    # Simple sections
    for ch in simple_pages:
        out = SITE_DIR / "chapters" / f"{ch['slug']}.html"
        out.write_text(build_simple_section(ch, nav_sections), encoding="utf-8")
        generated.append(out)
        console.print(f"  [green]+[/green] chapters/{ch['slug']}.html")

    # Parent landing pages
    for ch in parent_pages:
        sub_slugs = ch["sub_pages"]
        sub_chs   = [by_slug[s] for s in sub_slugs if s in by_slug]
        out = SITE_DIR / "chapters" / f"{ch['slug']}.html"
        out.write_text(
            build_parent_landing(ch, sub_chs, nav_sections, summaries),
            encoding="utf-8",
        )
        generated.append(out)
        console.print(f"  [green]+[/green] chapters/{ch['slug']}.html  [{len(sub_chs)} sub-pages]")

        # Sub-pages
        for sub in sub_chs:
            parent_ch = by_slug.get(sub["parent"])
            sibling_slugs = parent_ch["sub_pages"] if parent_ch else []
            siblings = [by_slug[s] for s in sibling_slugs if s in by_slug]
            sout = SITE_DIR / "chapters" / f"{sub['slug']}.html"
            sout.write_text(
                build_sub_page(sub, parent_ch or ch, siblings),
                encoding="utf-8",
            )
            generated.append(sout)
            console.print(f"    [dim]+[/dim] chapters/{sub['slug']}.html")

    # -- Whole-guide PDF -----------------------------------------------------
    console.print("\n  Generating PDF...")
    generate_pdf(nav_sections, all_sub, about_ch)

    return nav_sections, all_sub, summaries


# ==============================================================================
# SECTION 10 -- Rich report
# ==============================================================================

def print_report(nav_sections: list[dict], all_sub: list[dict], summaries: dict) -> None:
    console.print()
    console.rule("[bold green]Build Report[/bold green]")

    all_pages   = nav_sections + all_sub
    total_words = sum(len(ch["body"].split()) for ch in all_pages)
    html_files  = list((SITE_DIR / "chapters").glob("*.html")) + [
        SITE_DIR / "index.html",
        SITE_DIR / "about.html",
        SITE_DIR / "search.html",
        SITE_DIR / "404.html",
    ]

    console.print(f"  Pages generated : [bold]{len(html_files)}[/bold] HTML files")
    console.print(f"  Total word count: [bold]{total_words:,}[/bold] words")
    console.print(f"  Summaries cached: [bold]{len(summaries)}[/bold] pages")

    tbl = RichTable(box=box.SIMPLE_HEAD, show_lines=False, expand=False)
    tbl.add_column("Section",  style="cyan",    min_width=5,  max_width=10)
    tbl.add_column("File",     style="default", min_width=35, max_width=55)
    tbl.add_column("Title",    style="default", min_width=25, max_width=40)
    tbl.add_column("Words",    style="default", justify="right", min_width=7)

    for ch in nav_sections:
        label = f"{ch['section_number']}" if ch["section_number"] else "Ann."
        tbl.add_row(label, f"chapters/{ch['slug']}.html", ch["title"][:40], f"{len(ch['body'].split()):,}")
        if ch["sub_pages"]:
            for slug in ch["sub_pages"]:
                if slug in {s["slug"] for s in all_sub}:
                    sub = next(s for s in all_sub if s["slug"] == slug)
                    tbl.add_row("", f"  chapters/{sub['slug']}.html", f"  {sub['title'][:36]}", f"{len(sub['body'].split()):,}")

    console.print(tbl)
    console.print(f"\n[bold green]Done.[/bold green] Static site written to [cyan]{SITE_DIR}/[/cyan]")


# ==============================================================================
# SECTION 11 -- Entry point
# ==============================================================================

def main() -> None:
    console.rule("[bold blue]EU Wildlife Trade Reference Guide -- Static Site Builder[/bold blue]")

    if not INPUT_DIR.exists():
        console.print(f"[red]Error: {INPUT_DIR}/ not found.[/red]")
        sys.exit(1)

    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR, ignore_errors=True)
        console.print(f"  Removed previous [cyan]{SITE_DIR}/[/cyan]")

    nav_sections, all_sub, summaries = build_site()
    print_report(nav_sections, all_sub, summaries)


if __name__ == "__main__":
    main()
