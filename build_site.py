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

# Display title for the About page (section_number 1) -- deliberately not the
# frontmatter title ("How do I use this guide?"); used everywhere the site
# and PDF need to refer to this page by name, so both stay in sync.
ABOUT_TITLE = "About"

# -- Reversibility flags -- see brand/ECL-Migration-Spec.md section 5.1 --------
ECL_CHROME  = True    # False: Drupal / oe_theme supplies globan, header, footer
                      # markup and the webtools loader/cookie-consent JSON --
                      # NOT the underlying ECL JS/CSS asset stack or content-
                      # dependent fixes (chatbot, ECL.autoInit(), the digit-
                      # leading-heading-id shim), which are always emitted --
                      # see ecl_scripts() vs ecl_chrome_scripts().
ECL_CARDS   = False   # False: revert to the bespoke chapter/subpage card design
ECL_CONTENT = False   # True:  article body adopts ECL type/table classes

# Header banner text. ECL convention is the PARENT site name, so if this lands
# under environment.ec.europa.eu this becomes "Environment" -- spec section 6.3.
SITE_NAME_BANNER = "EU Wildlife Trade Regulations"

# Article-body element classes. With ECL_CONTENT False every value is "", so the
# generated HTML is byte-identical to the pre-migration output -- which is what
# makes the docs/chapters/ diff a meaningful gate rather than a formality.
CONTENT_CLASSES: dict[bool, dict[str, str]] = {
    False: {
        "h2": "", "h3": "", "h4": "", "p": "", "ul": "", "li": "",
        "table": "", "table_wrap": "table-wrap",
        "thead": "", "th": "", "td": "",
    },
    True: {
        "h2": "ecl-u-type-heading-2",
        "h3": "ecl-u-type-heading-3",
        "h4": "ecl-u-type-heading-4",
        "p":  "ecl-u-type-paragraph-m",
        "ul": "ecl-unordered-list",
        "li": "ecl-unordered-list__item",
        "table": "ecl-table", "table_wrap": "ecl-table-responsive",
        "thead": "ecl-table__head", "th": "ecl-table__header",
        "td": "ecl-table__cell",
    },
}


def cc(key: str) -> str:
    """Class name for an article-body element under the active ECL_CONTENT mode."""
    return CONTENT_CLASSES[ECL_CONTENT].get(key, "")


def cc_attr(key: str) -> str:
    """Ready-to-interpolate class attribute, or '' when the class is empty."""
    v = cc(key)
    return f' class="{v}"' if v else ""

# Note: cc_attr() has no caller while ECL_CONTENT is False -- that is deliberate.
# It is the interpolation point the heading/paragraph/list transforms will use when
# the flag is enabled. Do not delete it as dead code.

console = Console()
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
    # A caption may carry a footnote marker (<sup>…</sup>), e.g. Tables 14-15.
    r"<p><em>((Table|Figure)\s+(\d+)(?:[^<]|<sup\b[^>]*>.*?</sup>)*)</em></p>",
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


_THEAD_ROW_RE = re.compile(r"<thead>\s*<tr>(.*?)</tr>\s*</thead>", re.DOTALL)
_TH_CELL_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)


def _span_single_cell_header(row_m: re.Match, n_cols: int) -> str:
    row = row_m.group(1)
    cells = _TH_CELL_RE.findall(row)
    if len(cells) < 2:
        return row_m.group(0)
    non_empty = [c for c in cells if c.strip()]
    if len(non_empty) != 1:
        return row_m.group(0)
    content = non_empty[0].strip()
    return f'<thead><tr><th colspan="{n_cols}">{content}</th></tr></thead>'


_MIN_COL_WIDTH_PCT = 10.0


def _col_max_words(inner: str, n_cols: int) -> list:
    """Longest single word (in characters) found in each column's body
    cells, used to keep column widths from forcing mid-word wraps."""
    col_max_word = [0] * n_cols
    tbody_m = re.search(r"<tbody>(.*?)</tbody>", inner, re.DOTALL)
    if not tbody_m:
        return col_max_word
    for row_m in _ROW_RE.finditer(tbody_m.group(0)):
        cells = _CELL_RE.findall(row_m.group(0))
        if len(cells) != n_cols:
            continue
        for i, c in enumerate(cells):
            text = re.sub(r"<[^>]+>", "", c).strip()
            longest_word = max((len(w) for w in text.split()), default=0)
            col_max_word[i] = max(col_max_word[i], longest_word)
    return col_max_word


def _apply_word_floor(raw_pct: list, col_max_word: list) -> list:
    """Adjust a row of candidate column-width percentages so no column ends
    up narrower than its own longest single word needs. Each column is
    floored at whatever width that word needs, then only the *leftover*
    budget is handed out -- proportional to how far each column's raw share
    exceeds its own floor -- rather than uniformly rescaling every column
    back to 100%, which would shrink already-floored columns below the
    width their longest word needs whenever more than one column in the
    table has a floor."""
    word_floor = [min(30.0, max(_MIN_COL_WIDTH_PCT, w * 1.15 + 3)) for w in col_max_word]
    floor_total = sum(word_floor)
    if floor_total >= 100:
        return [f / floor_total * 100 for f in word_floor]
    remaining = 100 - floor_total
    excess = [max(r - f, 0) for r, f in zip(raw_pct, word_floor)]
    excess_total = sum(excess)
    if excess_total > 0:
        return [f + remaining * (e / excess_total) for f, e in zip(word_floor, excess)]
    return [f + remaining / len(word_floor) for f in word_floor]


def _make_colgroup(inner: str, n_cols: int) -> str:
    """Return <colgroup> HTML for a handful of known wide-text-column tables,
    keyed off header wording so new tables pick up the right widths automatically
    rather than needing a per-annex special case."""
    headers = re.findall(r"<th[^>]*>(.*?)</th>", inner, re.DOTALL)
    texts = [re.sub(r"<[^>]+>", "", h).lower().strip() for h in headers]
    if len(texts) != n_cols:
        return ""

    col_max_word = _col_max_words(inner, n_cols)

    if n_cols == 4 and any("documents required" in t for t in texts):
        pct = _apply_word_floor([12, 8, 60, 20], col_max_word)
        cols = "".join(f'<col style="width:{p:.1f}%">' for p in pct)
        return f"<colgroup>{cols}</colgroup>"

    # Last column is a long free-text explanation/reference -- give it most of
    # the width and split the rest evenly among the (usually short, code-like)
    # remaining columns.
    if texts and ("explanation" in texts[-1] or "taxonomic reference" in texts[-1]):
        last_width = 55
        other_width = (100 - last_width) / (n_cols - 1)
        raw_pct = [other_width] * (n_cols - 1) + [last_width]
        pct = _apply_word_floor(raw_pct, col_max_word)
        cols = "".join(f'<col style="width:{p:.1f}%">' for p in pct)
        return f"<colgroup>{cols}</colgroup>"

    return _content_based_colgroup(inner, n_cols, col_max_word)


def _content_based_colgroup(inner: str, n_cols: int, col_max_word: list = None) -> str:
    """Fallback for tables with no special-cased header wording: size each
    column proportionally to the average text length of its body cells, so
    a column that's mostly long free-text paragraphs gets noticeably more
    room than neighbouring short code/label columns, without needing a
    per-table rule. Rows that don't have exactly n_cols cells (e.g. a
    colspan sub-header row) are skipped since they carry no per-column
    signal. A floor keeps short-but-important columns legible."""
    if n_cols < 2:
        return ""
    tbody_m = re.search(r"<tbody>(.*?)</tbody>", inner, re.DOTALL)
    if not tbody_m:
        return ""
    col_totals = [0] * n_cols
    col_counts = [0] * n_cols
    for row_m in _ROW_RE.finditer(tbody_m.group(0)):
        cells = _CELL_RE.findall(row_m.group(0))
        if len(cells) != n_cols:
            continue
        for i, c in enumerate(cells):
            text = re.sub(r"<[^>]+>", "", c).strip()
            col_totals[i] += len(text)
            col_counts[i] += 1
    if not any(col_counts):
        return ""
    avgs = [
        (col_totals[i] / col_counts[i]) if col_counts[i] else 0
        for i in range(n_cols)
    ]
    if sum(avgs) == 0:
        return ""
    # If every column is roughly the same length, an even split already
    # looks right -- only worth a custom colgroup when there's real skew.
    if max(avgs) < 1.6 * (sum(avgs) / n_cols):
        return ""
    raw_pct = [max(a, 1) / sum(max(x, 1) for x in avgs) * 100 for a in avgs]
    if col_max_word is None:
        col_max_word = _col_max_words(inner, n_cols)
    pct = _apply_word_floor(raw_pct, col_max_word)
    cols = "".join(f'<col style="width:{p:.1f}%">' for p in pct)
    return f"<colgroup>{cols}</colgroup>"


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

        # Width detection reads the header text (e.g. "Documents Required"),
        # so it must run before the header row itself gets merged below.
        colgroup = _make_colgroup(inner, n_cols)

        # A header row with content in only one <th> (the rest empty) is a
        # table-wide title, not real per-column labels -- markdown tables can
        # only express one header row, so the actual column labels usually
        # live in the first tbody row instead. Merge it into one spanning
        # cell so it reads as a title bar rather than a lopsided header row.
        inner = _THEAD_ROW_RE.sub(lambda r: _span_single_cell_header(r, n_cols), inner, count=1)

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
            f'<div class="{cc("table_wrap")}">'
            f"<table>{caption_html}{colgroup}{inner}</table>"
            f"</div>"
        )
        pos = m.end() + skip

    result.append(html[pos:])
    html_result = "".join(result)
    # Add data-ecl-table-header="" to all <td> tags unconditionally
    html_result = re.sub(r'<td(\s|>)', r'<td data-ecl-table-header=""\1', html_result)
    return html_result


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


_FN_REF_RE = re.compile(
    r'<sup class="footnote-ref" id="fnref-([^"]+)"><a href="#fn-([^"]+)">(\d+)</a></sup>'
)
_FN_LI_ID_RE = re.compile(r'<li id="fn-([^"]+)">')
_FN_BACKLINK_RE = re.compile(r'(<a href="#fnref-([^"]+)" class="footnoteBackLink"[^>]*>&#8617;</a>)')


def _postprocess_footnote_refs(html: str) -> str:
    """markdown2 gives a footnote cited twice a fresh display number (the second
    [^2] shows as "7") but the same id="fnref-2" and a link to note 2. Show every
    marker as its note's position in the list, give repeat citations their own
    id, and add a back-link per citation so each one can be jumped back to."""
    position = {label: i for i, label in enumerate(_FN_LI_ID_RE.findall(html), 1)}
    if not position:
        return html
    seen: dict[str, int] = {}

    def _ref(m: re.Match) -> str:
        label = m.group(2)
        n = seen[label] = seen.get(label, 0) + 1
        ref_id = f"fnref-{label}" if n == 1 else f"fnref-{label}-{n}"
        shown = position.get(label, m.group(3))
        return (f'<sup class="footnote-ref" id="{ref_id}">'
                f'<a href="#fn-{label}">{shown}</a></sup>')

    html = _FN_REF_RE.sub(_ref, html)

    def _backlinks(m: re.Match) -> str:
        label = m.group(2)
        extra = "".join(
            f' <a href="#fnref-{label}-{n}" class="footnoteBackLink" '
            f'title="Jump back to footnote {position.get(label, label)} in the text (citation {n}).">'
            f'&#8617;<sup>{n}</sup></a>'
            for n in range(2, seen.get(label, 1) + 1)
        )
        return m.group(1) + extra

    return _FN_BACKLINK_RE.sub(_backlinks, html)


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
        # A caption converted by _postprocess_table_labels() already carries
        # id="figure-N"; a second copy here would leave the id duplicated.
        id_attr = "" if f'id="figure-{num}"' in html else f' id="figure-{num}"'
        return (
            f'<figure class="figure-block"{id_attr}>'
            f'<img src="{root}assets/images/Figure-{num}.png" alt="Figure {num}">'
            f'</figure>'
        )

    html = _FIG_WITH_CAP_RE.sub(_sub_with_cap, html)
    html = _FIG_BARE_RE.sub(_sub_bare, html)
    return html


# Matches bold Figure/Table references for in-text linking -- a single one
# ("Figure 2") or a compound run ("Figures 10 and 11", "Figure 2 and Table 7").
_FIGTAB_BOLD_REF_RE = re.compile(
    r'<strong>([^<]*?\b(?:Figure|Table)s?\s+\d[^<]*)</strong>',
    re.IGNORECASE,
)
# Within one bold run: "Figure(s)/Table(s) N" followed by any ", M" / "and M" / "or M".
_FIGTAB_GROUP_RE = re.compile(
    r'\b(Figure|Table)(s?\s+)(\d+)((?:\s*(?:,|and|or)\s*\d+\b)*)', re.IGNORECASE
)
_FIGTAB_MORE_NUM_RE = re.compile(r'(\s*(?:,|and|or)\s*)(\d+)', re.IGNORECASE)

# Page-level lookups: figure/table number → docs/-relative URL with anchor
_FIGURE_PAGE_LOOKUP: dict[str, str] = {}
_TABLE_PAGE_LOOKUP: dict[str, str] = {}

# Matches "Summary of key instructions" bold paragraph + optional paras + ol.
# The intro paragraphs must NOT include a figure caption/placeholder pair --
# those are captured separately (group 2) so a figure sitting between the
# note and the list doesn't get dragged into the small-print styling too
# (it was previously inheriting the box's grey/small text and left border).
_SUMMARY_RE = re.compile(
    r'(<p>\s*<strong>Summary of key instructions[^<]*</strong>\s*</p>'
    r'(?:\s*<p(?![^>]*class="table-label")(?!\s*>\[Insert Figure)[^>]*>.*?</p>)*)\s*'
    r'((?:<p[^>]*class="table-label"[^>]*>.*?</p>\s*<p>\[Insert Figure \d+\]</p>\s*)*)'
    r'(<ol>.*?</ol>)',
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
    """Link bold Figure N / Table N references, including compound ones such as
    "Figures 10 and 11"; use cross-page URLs when needed."""
    lookups = {"figure": _FIGURE_PAGE_LOOKUP, "table": _TABLE_PAGE_LOOKUP}

    def _href(kind: str, num: str) -> str:
        # Cross-page if on a different page, same-page anchor otherwise
        page_url = lookups[kind].get(num)
        return _xref_url(page_url, depth) if page_url else f"#{kind}-{num}"

    result: list[str] = []
    pos = 0
    for m in _FIGTAB_BOLD_REF_RE.finditer(html):
        before = html[max(0, m.start() - 400) : m.start()]
        result.append(html[pos : m.start()])
        pos = m.end()
        # Already inside a link
        if before.count("<a ") > before.count("</a>"):
            result.append(m.group(0))
            continue
        after = html[m.end() : m.end() + 1200]

        def _target_follows(kind: str, num: str) -> bool:
            # The reference sits directly above its target, so no link is needed
            return f'id="{kind}-{num}"' in after

        def _sub_group(g: re.Match) -> str:
            word, gap, num, more = g.groups()
            kind = word.lower()
            first = f"{word}{gap}{num}"
            if not _target_follows(kind, num):
                first = f'<a href="{_href(kind, num)}">{first}</a>'
            more = _FIGTAB_MORE_NUM_RE.sub(
                lambda n: n.group(0) if _target_follows(kind, n.group(2))
                else f'{n.group(1)}<a href="{_href(kind, n.group(2))}">{n.group(2)}</a>',
                more,
            )
            return first + more

        result.append(f"<strong>{_FIGTAB_GROUP_RE.sub(_sub_group, m.group(1))}</strong>")
    result.append(html[pos:])
    return "".join(result)


def _postprocess_summary_sections(html: str) -> str:
    """Wrap 'Summary of key instructions' + following list in a small-print div.
    A figure sitting between the intro and the list (see 3.3's Figure 2) is kept
    out of the box, in its own normally-styled paragraph, instead of inheriting
    the box's grey small-print text and border."""
    def _sub(m: re.Match) -> str:
        intro, figure, ol = m.group(1), m.group(2).strip(), m.group(3)
        if figure:
            return f'<div class="summary-smallprint">{intro}</div>{figure}<div class="summary-smallprint">{ol}</div>'
        return f'<div class="summary-smallprint">{intro}{ol}</div>'

    return _SUMMARY_RE.sub(_sub, html)


_CONTEXT_BOX_RE = re.compile(
    r'<p><strong>CONTEXT</strong></p>\s*<p>(.*?)</p>',
    re.DOTALL,
)


def _postprocess_context_boxes(html: str) -> str:
    """Wrap Annex XII's 'CONTEXT' call-outs (a bold CONTEXT label paragraph
    immediately followed by one content paragraph -- the source markdown is
    normalized to always have exactly this shape) in a bordered box, matching
    how the source PDF visually sets these apart from the surrounding text."""
    def _sub(m: re.Match) -> str:
        content = m.group(1)
        return (
            '<div class="context-box">'
            '<p class="context-box__label">CONTEXT</p>'
            f'<p>{content}</p>'
            '</div>'
        )
    return _CONTEXT_BOX_RE.sub(_sub, html)


_HTML_TABLE_BLOCK_RE = re.compile(r"<table\b.*?</table>", re.DOTALL)
_FOOTNOTE_MARK_RE = re.compile(r"\[\^([^\]\s]+)\]")


class _Markdown(markdown2.Markdown):
    """markdown2 passes raw HTML blocks through untouched, so a [^n] marker
    inside a hand-built <table> (the Annex tables) would print literally and
    its footnote would be dropped as unreferenced. Swap such markers for
    markdown2's own footnote-ref placeholder before block hashing; its
    footnote pass then numbers them in document order and emits their
    definitions like any other reference."""

    def preprocess(self, text: str) -> str:
        text = super().preprocess(text)
        defined = set(re.findall(r"^\[\^([^\]\s]+)\]:", text, re.MULTILINE))

        def ref(m: re.Match) -> str:
            label = m.group(1)
            if label not in defined:
                return m.group(0)
            return (f'<sup class="footnote-ref" id="fnref-{label}">'
                    f'<a href="#fn-{label}">{self._footnote_marker}-{label}</a></sup>')

        return _HTML_TABLE_BLOCK_RE.sub(lambda t: _FOOTNOTE_MARK_RE.sub(ref, t.group(0)), text)


def render_markdown(content: str) -> str:
    html = _Markdown(
        extras=["tables", "fenced-code-blocks", "header-ids", "smarty-pants", "footnotes"],
    ).convert(content)
    html = _postprocess_lettered_lists(html)
    html = _postprocess_tables(html)
    html = _postprocess_table_labels(html)
    html = _postprocess_footnote_refs(html)
    html = _postprocess_footnotes(html)
    html = _postprocess_summary_sections(html)
    html = _postprocess_context_boxes(html)
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
        if re.search(r'^\s*(?:to|of)\s+(?:<[^>]+>\s*)*Regulation\b', after_ctx, re.IGNORECASE):
            return text
        # Bare numerals ("Annexes III and IV") must be upper-case whole words,
        # or "… and its attachments" links the "i" of "its" to Annex I.
        bare_re = r'(\s+and\s+|,\s*)([IVXL]+)\b'
        ann_ms = list(re.finditer(r'(Annex(?:es)?\s+)([IVXLivxl]+)\b', text, re.IGNORECASE))
        bare_ms = list(re.finditer(bare_re, text))
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

        result = re.sub(r'(Annex(?:es)?\s+)([IVXLivxl]+)\b', _link_ann, text, flags=re.IGNORECASE)

        def _link_bare_ann(mm: re.Match) -> str:
            sep, rom = mm.group(1), mm.group(2)
            u = _XREF_LOOKUP.get(f"annex_{rom.upper()}")
            if u is None:
                return mm.group(0)
            return f'{sep}<a href="{_xref_url(u, depth)}">{rom}</a>'

        return re.sub(bare_re, _link_bare_ann, result)

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
    s = re.sub(r"\[\^[^\]]+\]:?", "", s)
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

/* Inter @font-face is NOT re-declared here -- ecl-ec.css already declares it
   (normal + italic, weight 100-900), with a local fallback at ../fonts/
   pointing at the same physical files we vendor. A second, duplicate
   declaration here created two independent FontFace objects for the exact
   same family/weight/style; as each resolved at a slightly different time,
   the browser would repaint using whichever one currently "won" the match,
   producing a visible flash between the two on every page load. */

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
html { font-size: 16px; overflow-x: clip; }

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
/* A .container nested inside another .container shouldn't double the width cap/padding. */
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

/* -- Page header (ECL page-header component) ---------- */
.page-header { background: var(--white); }
.page-header + .main-content { padding-top: 28px; }
.page-header__meta {
  list-style: none; margin: 8px 0 0; padding: 0;
}
.ecl-page-header__title { padding-top: 20px; margin: 0; }
.page-header__meta-item {
  display: inline;
  font-size: .8125rem; color: var(--secondary);
}
.page-header__meta-item:first-child {
  display: block; font-weight: 700; text-transform: uppercase;
  letter-spacing: .05em; color: var(--secondary); margin-bottom: 2px;
}

/* -- Main wrapper ------------------------------------- */
/* overflow-wrap: long unbroken URLs in the text (Annex XII's references, the
   About page's footnotes) otherwise run past the right edge on phones, where
   html/body's overflow-x: clip silently cuts them off. */
.main-content { padding: 30px 0 70px; overflow-wrap: break-word; }

/* -- Article area ------------------------------------- */
.article { flex: 1 1 auto; min-width: 0; }

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
/* .table-subheader also lands on <th> in a couple of hand-authored tables
   (a second "column label" header row under a spanning title row) -- needs
   its own selector since .article table th's black background otherwise
   outranks the plain .table-subheader rule above on specificity. */
.article table th.table-subheader, .article-body table th.table-subheader {
  background: #f3f2f1; color: var(--black); font-weight: 700;
}
table caption {
  caption-side: bottom; font-size: 0.8rem; color: #505a5f;
  text-align: left; padding-top: 0.4rem; font-style: normal;
}
/* Second-level numbered items are (i), (ii), ... in the source (Annex XII). */
.article ol ol, .article-body ol ol { list-style-type: lower-roman; }
/* Annex tables rebuilt from the Word source (fix_annex_tables.py) keep a
   cell's separate paragraphs and in-cell bullet lists. */
.article table td p, .article-body table td p { margin: 0 0 0.5em; }
.article table td ul, .article-body table td ul { margin: 0 0 0.5em; padding-left: 1.2em; }
.article table td li, .article-body table td li { margin-bottom: 0.25em; }
.article table td > :last-child, .article-body table td > :last-child { margin-bottom: 0; }
.article table td.cell-center, .article-body table td.cell-center { text-align: center; }
/* Footnote markers in header cells: the default link blue is unreadable on
   the dark header background. */
.article table th a, .article-body table th a { color: inherit; }
/* Many-column/long-reference tables: scroll inside .table-wrap on narrow
   screens rather than crushing columns until words break mid-word. */
.article table.table--wide, .article-body table.table--wide { min-width: 720px; }
/* Annex VII: its Order column must fit "ARHYNCHOBDELLIDA" on one line. */
.article table.table--xwide, .article-body table.table--xwide { min-width: 920px; }

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
/* The hero is rendered outside the width-capped .container (base_html's
   hero_html) so it can span the page. It used to bleed out of the container
   with width: 100vw, but 100vw includes the vertical scrollbar on Windows,
   which shifted the hero text half a scrollbar left of the header, the
   section headings and the footer. */
.main-content:has(.hero) { padding-top: 0; }
.hero {
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
.hero__download { margin: 16px 0 0; font-size: 1rem; color: var(--white); }
.hero__download a { color: var(--white); text-decoration: underline; }
.hero__download a:hover { color: #fea439; }
.hero__download a:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }

/* -- Whole-guide PDF links ----------------------------- */
.home-download { margin-top: 1rem; }
.home-download__icon { width: 2.5rem; height: 2.5rem; }
.mega-menu-pdf-link { margin-top: var(--s-m); }

/* Legacy card design -- the active design (ECL_CARDS=False, reverted per user
   feedback: the ECL card's fonts/sizing/spacing didn't work as well as this
   design). Kept switchable via ECL_CARDS -- see brand/ECL-Migration-Spec.md
   section 5.1.1. The border-bottom below is the one detail kept from the
   ECL card design at the user's request. Do not delete this block even if
   ECL_CARDS is flipped back to True later. */
/* -- Section card grid -------------------------------- */
.chapter-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px; margin-top: 30px;
}
.chapter-card {
  display: flex; flex-direction: column;
  position: relative;
  background: var(--white); border: none; border-bottom: 4px solid #5987ff; /* ECL primary-500, kept from the ECL card design */
  border-radius: var(--radius); padding: 20px;
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
  background: var(--white); border: none; border-bottom: 4px solid #5987ff; /* ECL primary-500, kept from the ECL card design */
  border-radius: var(--radius); padding: 20px;
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
.search-tips { font-size: .875rem; color: var(--secondary); margin: 0 0 8px; }
.search-count { font-size: .9375rem; color: var(--secondary); }
.search-results-list { list-style: none; margin: 0; padding: 0; }
.search-result { border-bottom: 1px solid var(--border); padding: 20px 0; margin: 0; }
.search-result:first-child { border-top: 1px solid var(--border); }
.search-result__title {
  font-size: 1.1875rem; line-height: 1.4; font-weight: 700;
  margin: 0 0 4px; padding-top: 0; border-top: none;   /* override the global h2 rule */
}
.search-result__title a { color: var(--green); }
.search-result__meta { font-size: .875rem; color: var(--secondary); margin-bottom: 4px; }
.search-result__snippet { font-size: .9375rem; margin: 4px 0 0; }
#search-results mark { background: var(--focus); color: var(--black); padding: 0 2px; }
.search-result__sections {
  list-style: none; margin: 12px 0 0; padding: 0 0 0 16px;
  border-left: 3px solid var(--border);
}
.search-result__section { margin: 0 0 12px; }
.search-result__section:last-child { margin-bottom: 0; }
.search-result__section > a { font-size: 1rem; font-weight: 600; color: var(--green); }
.search-result__context { font-size: .875rem; color: var(--secondary); }
.search-notice {
  background: var(--light-grey); border-left: 4px solid var(--focus);
  padding: 12px 16px; margin-bottom: 16px; font-size: .9375rem;
}
.search-notice p { margin: 0; }
.search-notice p + p { margin-top: 4px; }
.search-examples { margin-top: 8px; }
.search-examples p { font-size: .9375rem; color: var(--secondary); margin: 0 0 8px; }
.search-examples ul { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.search-examples a {
  display: inline-block; padding: 4px 14px; font-size: .9375rem;
  border: 1px solid var(--border); border-radius: 16px; color: var(--green); text-decoration: none;
}
.search-examples a:hover { border-color: var(--green); text-decoration: underline; }
.no-results { padding: 40px 0; text-align: center; color: var(--secondary); }

/* -- Search suggestions under the header / homepage boxes (search.js initSuggest) -- */
/* The ECL header form is already positioned (relative on desktop, absolute in
   its mobile pop-up) -- overriding that breaks the pop-up. */
.has-search-suggest:not(.ecl-search-form) { position: relative; }
/* .search-form clips its children to its rounded border; the list must escape it. */
.search-form.has-search-suggest { overflow: visible; }
.search-suggest {
  position: absolute; top: 100%; right: 0; z-index: 1000;
  width: max(100%, 400px); max-width: calc(100vw - 32px); max-height: 70vh; overflow-y: auto;
  margin: 4px 0 0; padding: 4px 0; list-style: none; text-align: left;
  background: var(--white); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow-2);
}
.search-suggest[hidden] { display: none; }
.hero .search-suggest { left: 0; right: auto; width: 100%; }
.search-suggest__option { margin: 0; padding: 8px 16px; cursor: pointer; font-size: .9375rem; line-height: 1.4; }
.search-suggest__option[aria-selected="true"], .search-suggest__option:hover { background: var(--mid-grey); }
.search-suggest__title {
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
  font-weight: 600; color: var(--green);
}
.search-suggest__context {
  display: block; font-size: .8125rem; color: var(--secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.search-suggest mark { background: none; color: inherit; font-weight: 800; padding: 0; }
.search-suggest__all { border-top: 1px solid var(--border); margin-top: 4px; padding-top: 10px; color: var(--green); }

/* -- Search-term highlights on chapter pages (main.js initSearchHighlight) -- */
mark.search-hl { background: #fff0a8; color: inherit; padding: 0 1px; border-radius: 2px; }
mark.search-hl--current { background: var(--focus); outline: 2px solid var(--black); outline-offset: 1px; }
.search-hl-bar {
  position: fixed; left: 24px; bottom: 24px; z-index: 9990;
  display: flex; align-items: center; gap: 4px;
  padding: 6px 6px 6px 16px; border-radius: 24px;
  background: var(--black); color: var(--white);
  box-shadow: 0 4px 14px rgba(0,0,0,.28);
  font-size: .875rem;
}
.search-hl-bar__count { margin-right: 6px; white-space: nowrap; }
.search-hl-bar button {
  font: inherit; color: var(--white); background: transparent; cursor: pointer;
  border: 1px solid rgba(255,255,255,.4); border-radius: 18px;
  min-width: 36px; height: 36px; padding: 0 12px; white-space: nowrap;
}
.search-hl-bar__step { font-size: 1.25rem; line-height: 1; padding: 0 !important; }
.search-hl-bar button:hover { background: rgba(255,255,255,.12); }
.search-hl-bar button:focus-visible { outline: 3px solid var(--focus); outline-offset: 1px; }
@media (max-width: 600px) {
  /* Clear of the chatbot button, which sits bottom-right. */
  .search-hl-bar { left: 16px; right: 16px; bottom: 84px; justify-content: flex-end; }
  .search-hl-bar__count { margin-right: auto; }
}
@media print { .search-hl-bar { display: none; } mark.search-hl { background: none; } }

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

/* -- Context box (Annex XII "CONTEXT" call-outs) ------- */
.context-box {
  margin: 20px 0; padding: 16px 20px;
  background: var(--light-grey); border: 1px solid var(--border); border-radius: var(--radius);
}
.context-box__label {
  font-weight: 700; text-transform: uppercase; font-size: .75rem;
  letter-spacing: .05em; color: var(--secondary); margin-bottom: 8px;
}
.context-box p:last-child { margin-bottom: 0; }

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
.footer-smallprint {
  font-size: 0.875rem; color: #9eaee1;
  margin: 16px 0 0; line-height: 1.5;
}

/* -- Homepage section dividers ------------------------ */
.home-sections > hr { margin: 2rem 0; border: none; border-top: 1px solid var(--border); }

/* -- ECL overrides -------------------------------------
   Targeted fixes for gaps in vendored ECL v5.1.0 (spec section 5:
   "add targeted overrides in a small ecl-overrides block only where
   something demonstrably breaks").

   Vendored ECL v5.1.0's own stylesheet sets .ecl-inpage-navigation to
   display: none by default below 996px, only switching it to
   display: block once ECL's own IntersectionObserver adds an
   --visible class as the reader scrolls -- and that class never gets
   a chance to fire from a cold load, so in practice the nav (and its
   __trigger button inside it, which needs the same --visible class
   plus display:flex to appear) is unreachable below 996px: not before
   scrolling, not after, not ever. Confirmed directly against
   docs/assets/ecl/styles/ecl-ec.css: .ecl-inpage-navigation{display:
   none} with @media(min-width:996px){display:block}, and
   .ecl-inpage-navigation__trigger{display:none} with only
   .ecl-inpage-navigation--visible .ecl-inpage-navigation__trigger
   {display:flex} turning it on (itself forced back to display:none at
   min-width:996px, so the trigger is mobile/tablet-only even when
   --visible does apply). There is no other mobile trigger, so without
   this override there is no way to see a page's table of contents at
   any viewport below 996px, on any content page, at any scroll
   position -- a real functional regression on a long-form reference
   guide. Re-show the nav and its trigger unconditionally below the
   same 996px breakpoint ECL itself uses; this must not touch anything
   at 996px and up, where the existing sticky scroll-spy behaviour
   (verified in Task 8) must keep working unchanged. */
@media screen and (max-width: 995px) {
  .ecl-inpage-navigation { display: block; }
  .ecl-inpage-navigation__trigger { display: flex; }
}

/* This site's own global "ul, ol { margin: 0 0 18px 1.5em; }" rule (for
   article-body bullet lists) has no article-scope selector, and with
   ecl-reset.css deliberately not loaded (spec section 5) that 1.5em
   left margin leaks into every ECL chrome list too, including the
   footer's legal-navigation row -- confirmed live via the CSSOM
   (ul.ecl-site-footer__list--inline matched "ul, ol" with no other
   rule resetting margin-left, putting it 24px right of the footer's
   true left margin, out of line with the logo/description/smallprint
   above and below it). Reset it back to ECL's own zero margin here
   rather than re-scoping the original site-wide rule, which content
   pages already render correctly against.
   Only margin-left and the global li margin-bottom are reset: an earlier
   blanket "margin: 0" here also wiped ECL's own margin-block-start on
   .ecl-site-footer__list (16px), jamming every link list up against its
   "Contact us"/"About us"/"Related links" heading. */
.ecl-site-footer ul, .ecl-site-footer ol { margin-left: 0; }
.ecl-site-footer li { margin-bottom: 0; }

/* Inline social icons (see _NETWORK_ICONS): white on the footer's brand
   background, with the knocked-out glyph filled to match that background,
   as the live DG ENV footer renders them. */
.ecl-site-footer .ecl-social-media-follow__icon { color: var(--cm-on-surface-inverted); }
.ecl-site-footer .ecl-social-media-follow__icon .cutout { fill: var(--cm-surface-brand); }

/* ECL's mega-menu panel lays its "info" (heading+summary) and "mega" (link
   list) panes out with position: absolute, sized and placed independently
   of one another -- built for a hover-card effect where the two can be
   different heights and visually float apart, not for a single attached
   two-column panel. On our Reference Guide item this rendered as two
   disconnected floating boxes instead of one panel. ECL also force-splits
   a flat one-level link list into 3 CSS columns once it has an info pane
   (.ecl-mega-menu__item--one-level-only ... .ecl-mega-menu__sublist,
   column-count: 3, display: block) -- the legacy .top-nav__dropdown design
   this replaced always used a single column. Both are overridden here,
   scoped to this specific menu item only: switch the two panes to a
   static flexbox row (ECL's own JS then measures and syncs their heights
   to match, and the legacy border-right visually attaches them), and put
   the list back into a single flex column -- setting column-count alone
   is not enough to undo ECL's rule, since it also switches the list to
   display: block, and under display: block a column-count of 1 still
   rendered as multiple columns live (confirmed via getBoundingClientRect:
   items landed in 3 groups ~1044px apart, off the edge of the panel);
   forcing display back to flex bypasses the column layout entirely
   instead of fighting it. Scoped to ECL's own min-width: 1140px
   breakpoint -- below that, ECL drops the side-by-side info/mega layout
   entirely for a full-width single-panel accordion (with its own "Back"
   button), and this override applied there too, forcing a side-by-side
   row onto a layout meant to stack, breaking the mobile/tablet menu. */
@media (min-width: 1140px) {
  #ecl-mega-menu-item-main-2 > .ecl-mega-menu__wrapper > .ecl-container {
    display: flex;
    align-items: stretch;
    gap: var(--s-2xl);
    /* Below ~1440px ECL narrows this to 24px and pulls the info pane out by
       -24px, leaving the panel heading 8px left of the logo and menu labels. */
    padding-inline: 2rem;
  }
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__info {
    position: static;
    flex: 0 0 260px;
    width: 260px;
    margin-inline: 0;
    padding-inline-start: 0;
    box-shadow: none; /* ECL's below-1440px card shadow outlined this pane */
    border-right: 1px solid var(--cm-border-low, #d4d4dc);
  }
  /* ECL's MegaMenu script writes inline heights on the wrapper and both
     panes, measured for its own absolute layout -- one row short for our
     single-column list. Sizing them all to content instead lets the flex
     row match the two panes' heights, and keeps the wrapper tall enough
     that ECL's 1px divider (.ecl-mega-menu__wrapper::before, pinned 16px
     above the wrapper's bottom edge) sits in the bottom padding below the
     list. With the panes overflowing a fixed-height wrapper, their white
     backgrounds covered the middle of that divider, leaving two stray
     line stubs at either side of the panel. */
  #ecl-mega-menu-item-main-2 > .ecl-mega-menu__wrapper,
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__mega,
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__info,
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__sublist {
    height: auto !important;
  }
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__info {
    overflow: visible;
    padding-bottom: 0;
    margin-bottom: 0; /* ECL adds 16px below ~1440px, cutting the border-right short of the list */
  }
  /* ECL draws a shadow strip on the info pane's right edge, positioned for
     its absolute layout; with the pane static it landed as a stray vertical
     bar at the far right of the container. The border-right above replaces it. */
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__info::after { content: none; }
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__mega {
    position: static;
    flex: 1 1 auto;
    width: auto;
  }
  #ecl-mega-menu-item-main-2 .ecl-mega-menu__sublist {
    display: flex;
    flex-direction: column;
    column-count: 1;
  }
  /* ECL sizes the desktop dropdown chevron at 10px, which read as
     near-invisible against the dark menu bar. */
  #ecl-mega-menu-item-main-2-link .ecl-icon--corner-arrow {
    width: 16px;
    height: 16px;
  }
}

/* -- Responsive --------------------------------------- */
/* Match .ecl-container's side padding at each breakpoint (16px / 24px / 32px
   at <768 / 768-1139 / 1140+) so page content lines up with the ECL header,
   breadcrumb and footer instead of sitting 4-8px to their right. */
@media screen and (max-width: 1139px) { :root { --gutter: 24px; } }
@media screen and (max-width: 767px)  { :root { --gutter: 16px; } }

@media screen and (max-width: 768px) {
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
}

/* -- Print -------------------------------------------- */
@media print {
  .chapter-nav, .back-to-top,
  .hero .search-form { display: none !important; }
  /* Task 11: pre-migration this list hid .official-strip, .site-header,
     .top-nav, .breadcrumbs, .sidebar, .mobile-contents and .site-footer by
     their bespoke class names. The ECL migration (Tasks 1-9) renamed those
     elements to ecl-* classes without updating this list, so browser print
     (Ctrl-P) silently regressed to printing the full site chrome on every
     page. Restore parity by hiding the ECL equivalents. The webtools-
     injected globan/cookie-consent banner (formerly .official-strip) is
     out of scope -- see spec section 8.1, it is not part of this build's
     static markup. */
  .ecl-site-header, .ecl-mega-menu, .ecl-breadcrumb,
  .ecl-inpage-navigation, .ecl-site-footer { display: none !important; }
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
        window.scrollTo(0, 0);
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

  /* Arriving from a search result (?hl=word word ...): highlight those words
     in the article, with a floating bar to step through the matches or clear
     them. search.js supplies every form of each search word that occurs in
     the guide ("permit permits permitted"), so no matching rules live here --
     just whole-word, case-insensitive matches. */
  function initSearchHighlight() {
    var params = new URLSearchParams(location.search);
    var article = document.querySelector('.article-body');
    if (!params.get('hl') || !article) return;
    var words = params.get('hl').split(/\\s+/).filter(Boolean).slice(0, 40)
      .sort(function (a, b) { return b.length - a.length; })
      .map(function (w) { return w.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); });
    if (!words.length) return;
    var re = new RegExp('(^|[^\\\\p{L}\\\\p{N}])(' + words.join('|') + ')(?=[^\\\\p{L}\\\\p{N}]|$)', 'giu');

    var walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        return n.parentNode.closest('script, style, svg, mark') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    var marks = [];
    nodes.forEach(function (node) {
      var text = node.nodeValue, last = 0, frag = null, m;
      re.lastIndex = 0;
      while ((m = re.exec(text))) {
        var start = m.index + m[1].length, end = start + m[2].length;
        frag = frag || document.createDocumentFragment();
        frag.appendChild(document.createTextNode(text.slice(last, start)));
        var mark = document.createElement('mark');
        mark.className = 'search-hl';
        mark.textContent = text.slice(start, end);
        frag.appendChild(mark);
        marks.push(mark);
        last = end;
      }
      if (frag) {
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
      }
    });
    if (!marks.length) return;

    var bar = document.createElement('div');
    bar.className = 'search-hl-bar';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', 'Search highlights');
    bar.innerHTML =
      '<span class="search-hl-bar__count" aria-live="polite"></span>' +
      '<button type="button" class="search-hl-bar__step" data-step="-1" aria-label="Previous match">&lsaquo;</button>' +
      '<button type="button" class="search-hl-bar__step" data-step="1" aria-label="Next match">&rsaquo;</button>' +
      '<button type="button" class="search-hl-bar__clear">Clear highlights</button>';
    var count = bar.querySelector('.search-hl-bar__count');
    count.textContent = marks.length + (marks.length === 1 ? ' match' : ' matches');
    var current = null;

    function step(dir) {
      // Matches inside collapsed content (e.g. hidden footnotes) are skipped.
      var shown = marks.filter(function (m) { return m.getClientRects().length; });
      if (!shown.length) return;
      var i = shown.indexOf(current);
      if (i === -1) {
        // First press: the first match at or below the top of the screen.
        i = 0;
        while (i < shown.length && shown[i].getBoundingClientRect().top < 0) i++;
        if (dir < 0) i--;
        i = (i + shown.length) % shown.length;
      } else {
        i = (i + dir + shown.length) % shown.length;
      }
      if (current) current.classList.remove('search-hl--current');
      current = shown[i];
      current.classList.add('search-hl--current');
      current.scrollIntoView({ block: 'center' });
      count.textContent = 'Match ' + (i + 1) + ' of ' + shown.length;
    }

    function clear() {
      marks.forEach(function (m) {
        var parent = m.parentNode;
        parent.replaceChild(document.createTextNode(m.textContent), m);
        parent.normalize();
      });
      bar.parentNode.removeChild(bar);
      params.delete('hl');
      var qs = params.toString();
      history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    }

    bar.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      if (btn.classList.contains('search-hl-bar__clear')) clear();
      else step(Number(btn.getAttribute('data-step')));
    });
    document.body.appendChild(bar);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSearchHighlight();
    initSidebarHighlight();
    initBackToTop();
    initFootnotesExpand();
    initTopNavDropdown();
  });
}());
"""

SEARCH_JS = """\
/* search.js -- client-side full-text search over search_index.json.
   The index holds one entry per page, split into heading-level sections
   (see build_search_index), so a result can link straight to the heading
   it matched. Matching is word-based: every search word must appear in a
   section (in any order, anywhere in it or its page title); words are cut
   to a rough stem so "permits"/"permit" and "trophies"/"trophy" match;
   hyphenated words match with or without the hyphen; "quoted phrases" must
   appear exactly. Misspelt words get a "did you mean" suggestion, and a
   search no section fully matches falls back to partial matches.
   Loaded on every page: besides the search page itself, it adds
   as-you-type suggestions to the header and homepage search boxes. */
(function () {
  'use strict';

  // Site root, from this script's own URL (assets/search.js), so the index
  // and result links resolve the same from any page depth.
  var ROOT = new URL('../', (document.currentScript && document.currentScript.src) || location.href);
  var INDEX_URL = new URL('search_index.json', ROOT).href;
  var SUGGESTIONS = 6;
  var SECTIONS_PER_RESULT = 3;
  var SNIPPET_WORDS = 34;
  var EXAMPLES = ['hunting trophies', 'captive-bred', 'personal effects', 'caviar', 'import permit', '"Annex A"'];
  var STOPWORDS = {};
  ('a an and are as at be been but by can could do does for from has have how i if in into is it its ' +
   'may me my no not of on or our should so than that the their them then there these they this those ' +
   'to was we what when where which who why will with would you your').split(' ')
    .forEach(function (w) { STOPWORDS[w] = true; });

  var TOKEN_RE = /[\\p{L}\\p{N}]+(?:['’][\\p{L}]+)*(?:[-./][\\p{L}\\p{N}]+)*/gu;

  var pages = null;    // index entries, prepared by prepare()
  var sections = [];   // every page's sections, flattened
  var vocab = {};      // key -> { df: sections containing it, forms: { surface word: count } }

  /* -- Text processing ----------------------------------------------------- */

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fold(s) {
    return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/['’]/g, '');
  }

  /* Rough English stemmer: enough to make singular/plural and common verb
     endings meet at the same key. Applied identically to the guide's text
     and to the query, so it only has to be consistent, not linguistically
     right ("trade", "traded", "trading" all become "trad"). */
  function stem(w) {
    if (w.length < 4 || /[0-9]/.test(w)) return w;
    if (/ies$/.test(w) && w.length > 4) w = w.slice(0, -3) + 'y';
    else if (/(xes|ches|shes|sses|zes)$/.test(w)) w = w.slice(0, -2);
    else if (/[^sui]s$/.test(w)) w = w.slice(0, -1);
    var m;
    if (/ied$/.test(w)) w = w.slice(0, -3) + 'y';
    else if (/eed$/.test(w)) { if (w.length > 4) w = w.slice(0, -1); }
    else if ((m = /^(.+?)(ed|ing)$/.exec(w)) && m[1].length >= 2 && /[aeiouy]/.test(m[1])) w = m[1];
    if (w.length >= 4 && /([b-df-hj-np-tv-z])\\1$/.test(w)) w = w.slice(0, -1);
    if (w.length >= 3 && /e$/.test(w)) w = w.slice(0, -1);
    return w;
  }

  /* A matched word -> its search keys. "re-export" gives "re", "export" and
     "reexport"; "338/97" and "3.3.1" are kept whole as well as split. */
  function keysFor(token) {
    var f = fold(token);
    var parts = f.split(/[-./]/).filter(Boolean);
    if (parts.length === 1) return [stem(f)];
    var keys = parts.map(stem);
    var joined = f.replace(/-/g, '');
    keys.push(/[./]/.test(joined) ? joined : stem(joined));
    return keys;
  }

  function tokenize(text) {
    var out = [], m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(text))) out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
    return out;
  }

  function keySet(text) {
    var set = {};
    tokenize(text).forEach(function (t) { keysFor(t.text).forEach(function (k) { set[k] = true; }); });
    return set;
  }

  /* Lower-case, unaccented, punctuation-free text with single spaces --
     used for exact-phrase checks, so "re-export" reads as "re export". */
  function flat(text) {
    return ' ' + fold(text).replace(/[^\\p{L}\\p{N}]+/gu, ' ').trim() + ' ';
  }

  /* -- Index preparation (once, on load) -------------------------------- */

  function prepare(data) {
    var totalLen = 0;
    data.forEach(function (page, pi) {
      page.titleKeys = keySet(page.title);
      page.summaryKeys = keySet(page.summary || '');
      var lastByLevel = {};
      page.sections.forEach(function (sec) {
        sec.page = page;
        sec.pageIndex = pi;
        sec.counts = {};
        sec.len = 0;
        tokenize(sec.x).forEach(function (t) {
          var form = t.text.toLowerCase();
          keysFor(t.text).forEach(function (k) {
            sec.counts[k] = (sec.counts[k] || 0) + 1;
            var v = vocab[k] || (vocab[k] = { df: 0, forms: {}, _last: -1 });
            v.forms[form] = (v.forms[form] || 0) + 1;
          });
          sec.len++;
        });
        sec.headKeys = keySet(sec.h);
        sec.flatText = flat(sec.h + ' ' + sec.x);
        sec.flatHead = flat(sec.h);
        sec.flatHeadBare = flat(sec.h.replace(/^[\\d.\\s]+/, ''));
        // Nearest enclosing heading, shown as context for h3/h4 sections.
        lastByLevel[sec.l] = sec;
        for (var l = sec.l - 1; l >= 2; l--) {
          if (lastByLevel[l]) { sec.parentHeading = lastByLevel[l].h; break; }
        }
        Object.keys(sec.counts).concat(Object.keys(sec.headKeys)).forEach(function (k) {
          var v = vocab[k] || (vocab[k] = { df: 0, forms: {}, _last: -1 });
          if (v._last !== sections.length) { v.df++; v._last = sections.length; }
        });
        totalLen += sec.len;
        sections.push(sec);
      });
    });
    prepare.avgLen = totalLen / Math.max(1, sections.length);
    return data;
  }

  /* -- Query parsing ------------------------------------------------------ */

  /* typing: the box is live (suggestions), so the last word is always
     treated as possibly unfinished, even when it is a word on its own. */
  function parseQuery(raw, typing) {
    var phrases = [];
    var text = raw.replace(/"([^"]+)"/g, function (_, p) {
      if (p.trim()) phrases.push(flat(p));
      return ' ' + p + ' ';
    }).replace(/"/g, ' ');
    var tokens = tokenize(text);
    var meaningful = tokens.filter(function (t) { return !STOPWORDS[fold(t.text)]; });
    if (!meaningful.length) meaningful = tokens;
    // Still typing the last word? Let it match longer words it begins.
    var lastIsOpen = /[\\p{L}\\p{N}]$/u.test(raw);
    var terms = meaningful.map(function (t, i) {
      var keys = {};
      var main = keysFor(t.text);
      var compound = main[main.length - 1];
      keys[compound] = 1;
      var known = !!vocab[compound];
      var isLast = i === meaningful.length - 1 && t === tokens[tokens.length - 1];
      if ((!known || typing) && isLast && lastIsOpen && compound.length >= 3 && !/[0-9]/.test(compound)) {
        var prefix = fold(t.text).replace(/-/g, '');
        Object.keys(vocab).forEach(function (k) {
          if (k.lastIndexOf(prefix, 0) === 0 && k !== compound) { keys[k] = 0.5; known = true; }
        });
      }
      return { raw: t.text, keys: keys, known: known };
    });
    var words = tokens.map(function (t) { return fold(t.text).replace(/[^\\p{L}\\p{N}]+/gu, ' '); });
    var pairs = [];
    for (var i = 0; i + 1 < words.length; i++) pairs.push(' ' + words[i] + ' ' + words[i + 1] + ' ');
    return {
      raw: raw, terms: terms, phrases: phrases, pairs: pairs,
      whole: words.length > 1 ? ' ' + words.join(' ') + ' ' : '',
      exact: flat(words.join(' '))
    };
  }

  /* -- Scoring ------------------------------------------------------------ */

  function idf(term) {
    var df = 0;
    Object.keys(term.keys).forEach(function (k) { if (vocab[k]) df = Math.max(df, vocab[k].df); });
    var n = sections.length;
    return Math.log(1 + (n - df + 0.5) / (df + 0.5));
  }

  /* BM25-style score for one section: word frequency (damped, adjusted
     for section length) plus bonuses for the word being in the section's
     heading or the page title, and for the words appearing together. */
  function scoreSection(sec, q, idfs) {
    var score = 0, matched = 0, inContent = false;
    var norm = 1 - 0.75 + 0.75 * (sec.len / prepare.avgLen);
    q.terms.forEach(function (term, ti) {
      var tf = 0, head = false, title = false, summary = false;
      Object.keys(term.keys).forEach(function (k) {
        var w = term.keys[k];
        tf += (sec.counts[k] || 0) * w;
        if (sec.headKeys[k]) head = true;
        if (sec.page.titleKeys[k]) title = true;
        if (sec.page.summaryKeys[k]) summary = true;
      });
      if (tf > 0 || head) inContent = true;
      if (tf > 0 || head || title) matched++;
      var tfPart = tf > 0 ? (tf * 2.2) / (tf + 1.2 * norm) : 0;
      score += idfs[ti] * (tfPart + (head ? 2.5 : 0) + (title ? 1.5 : 0) + (summary ? 0.3 : 0));
    });
    for (var i = 0; i < q.phrases.length; i++) {
      if (sec.flatText.indexOf(q.phrases[i]) === -1) return null;   // quoted phrase is required
      if (sec.flatHead.indexOf(q.phrases[i]) !== -1) score += 4;
    }
    if (q.whole && sec.flatText.indexOf(q.whole) !== -1) score += 3;
    if (q.whole && sec.flatHead.indexOf(q.whole) !== -1) score += 4;
    if (sec.flatHeadBare === q.exact) score += 6;   // heading is exactly the search
    q.pairs.forEach(function (p) { if (sec.flatText.indexOf(p) !== -1) score += 0.75; });
    return { sec: sec, score: score, matched: matched, inContent: inContent };
  }

  /* All matching pages, best first, each with its best sections. With
     requireAll=false a section only needs one of the search words. */
  function run(q, requireAll) {
    if (!q.terms.length) return [];
    var idfs = q.terms.map(idf);
    var need = requireAll ? q.terms.length : 1;
    var byPage = {};
    sections.forEach(function (sec) {
      var r = scoreSection(sec, q, idfs);
      if (!r || r.matched < need) return;
      if (!requireAll) r.score *= r.matched / q.terms.length;
      (byPage[sec.pageIndex] || (byPage[sec.pageIndex] = [])).push(r);
    });
    return Object.keys(byPage).map(function (pi) {
      var hits = byPage[pi].sort(function (a, b) { return b.score - a.score; });
      var score = hits[0].score + 0.3 * (hits[1] ? hits[1].score : 0) + 0.15 * (hits[2] ? hits[2].score : 0);
      return {
        page: pages[pi], score: score,
        shown: hits.filter(function (h) { return h.inContent; }).slice(0, SECTIONS_PER_RESULT)
      };
    }).sort(function (a, b) { return b.score - a.score; });
  }

  /* -- Spelling suggestions ------------------------------------------------ */

  function editDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    var prev2 = null, prev = [], cur, i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur = [i];
      var rowMin = i;
      for (j = 1; j <= b.length; j++) {
        var cost = a[i - 1] === b[j - 1] ? 0 : 1;
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
          cur[j] = Math.min(cur[j], prev2[j - 2] + 1);   // transposed letters
        }
        if (cur[j] < rowMin) rowMin = cur[j];
      }
      if (rowMin > max) return max + 1;
      prev2 = prev; prev = cur;
    }
    return prev[b.length];
  }

  var allForms = null;
  function suggest(word) {
    var w = fold(word);
    if (w.length < 3 || /[0-9]/.test(w)) return null;
    if (!allForms) {
      var totals = {};
      Object.keys(vocab).forEach(function (k) {
        var forms = vocab[k].forms;
        Object.keys(forms).forEach(function (f) {
          if (/^[\\p{L}]{3,}$/u.test(f)) totals[f] = Math.max(totals[f] || 0, forms[f]);
        });
      });
      allForms = Object.keys(totals).map(function (f) { return { form: f, n: totals[f] }; });
    }
    var max = w.length <= 4 ? 1 : 2;
    var best = null;
    allForms.forEach(function (c) {
      var d = editDistance(w, fold(c.form), max);
      if (d > max) return;
      if (!best || d < best.d || (d === best.d && c.n > best.n)) best = { form: c.form, d: d, n: c.n };
    });
    return best && best.form;
  }

  function correctedQuery(q) {
    var out = q.raw, changed = false;
    for (var i = 0; i < q.terms.length; i++) {
      var t = q.terms[i];
      if (t.known) continue;
      var s = suggest(t.raw);
      if (!s) return null;
      out = out.replace(t.raw, s);
      changed = true;
    }
    return changed ? out : null;
  }

  /* -- Rendering ------------------------------------------------------------ */

  function isHit(token, q) {
    var keys = keysFor(token);
    for (var i = 0; i < q.terms.length; i++) {
      for (var j = 0; j < keys.length; j++) if (q.terms[i].keys[keys[j]] !== undefined) return i;
    }
    return -1;
  }

  /* Text with every search-word occurrence wrapped in <mark>. */
  function markAll(text, q) {
    var out = '', pos = 0;
    tokenize(text).forEach(function (t) {
      if (isHit(t.text, q) === -1) return;
      out += esc(text.slice(pos, t.start)) + '<mark>' + esc(t.text) + '</mark>';
      pos = t.end;
    });
    return out + esc(text.slice(pos));
  }

  /* The stretch of a section's text with the most different search words
     in it (then the most occurrences), trimmed to whole words. */
  function snippet(text, q) {
    var toks = tokenize(text);
    if (!toks.length) return '';
    var hits = toks.map(function (t) { return isHit(t.text, q); });
    var best = -1, bestStart = 0, counts = {}, distinct = 0, total = 0;
    for (var i = 0; i < toks.length; i++) {
      if (hits[i] !== -1) { if (!counts[hits[i]]) distinct++; counts[hits[i]] = (counts[hits[i]] || 0) + 1; total++; }
      var out = i - SNIPPET_WORDS;
      if (out >= 0 && hits[out] !== -1) { counts[hits[out]]--; if (!counts[hits[out]]) distinct--; total--; }
      var value = distinct * 100 + total;
      if (total && value > best) { best = value; bestStart = Math.max(0, i - SNIPPET_WORDS + 1); }
    }
    if (best === -1) bestStart = 0;
    // Pull the window back a little so the first match isn't the first word.
    var firstHit = bestStart;
    while (firstHit < toks.length && hits[firstHit] === -1) firstHit++;
    var from = Math.max(0, Math.min(bestStart, firstHit - 6));
    var to = Math.min(toks.length - 1, from + SNIPPET_WORDS - 1);
    var s = toks[from].start, e = toks[to].end;
    return (s > 0 ? '&hellip;' : '') + markAll(text.slice(s, e), q) + (e < text.length ? '&hellip;' : '');
  }

  /* Words to highlight on the destination page: every form of each matched
     search word that occurs in the guide ("permit", "permits", "permitted"),
     limited to words that occur on that page -- an unfinished word like
     "tro" would otherwise bring in every word that starts with it. */
  function onPage(page, k) {
    return page.sections.some(function (sec) { return sec.counts[k] || sec.headKeys[k]; });
  }

  function highlightParam(q, page) {
    var words = {};
    q.terms.forEach(function (term) {
      Object.keys(term.keys).forEach(function (k) {
        var v = vocab[k];
        if (!v || !onPage(page, k)) return;
        Object.keys(v.forms).sort(function (a, b) { return v.forms[b] - v.forms[a]; })
          .slice(0, 6).forEach(function (f) { words[f] = true; });
      });
    });
    var list = Object.keys(words).slice(0, 40);
    return list.length ? 'hl=' + encodeURIComponent(list.join(' ')) : '';
  }

  function pageMeta(page) {
    var bits = [];
    if (page.label) bits.push(esc(page.label));
    if (page.parent) bits.push(esc(page.parent));
    return bits.join(' &middot; ');
  }

  function renderResult(r, q) {
    var page = r.page;
    var hl = highlightParam(q, page);
    var url = page.url + (hl ? '?' + hl : '');
    var meta = pageMeta(page);
    var html = '<li class="search-result">' +
      (meta ? '<div class="search-result__meta">' + meta + '</div>' : '') +
      '<h2 class="search-result__title"><a href="' + esc(url) + '">' + markAll(page.title, q) + '</a></h2>';
    var intro = r.shown.filter(function (h) { return !h.sec.id; })[0];
    var subs = r.shown.filter(function (h) { return h.sec.id; });
    if (intro) {
      html += '<p class="search-result__snippet">' + snippet(intro.sec.x, q) + '</p>';
    } else if (!subs.length && page.summary) {
      html += '<p class="search-result__snippet">' + markAll(page.summary, q) + '</p>';
    }
    if (subs.length) {
      html += '<ul class="search-result__sections">' + subs.map(function (h) {
        var sec = h.sec;
        return '<li class="search-result__section">' +
          (sec.parentHeading ? '<span class="search-result__context">' + esc(sec.parentHeading) + ' &rsaquo;</span> ' : '') +
          '<a href="' + esc(url + '#' + sec.id) + '">' + markAll(sec.h, q) + '</a>' +
          (sec.x ? '<p class="search-result__snippet">' + snippet(sec.x, q) + '</p>' : '') +
          '</li>';
      }).join('') + '</ul>';
    }
    return html + '</li>';
  }

  function searchLink(text) {
    return '<a href="search.html?q=' + encodeURIComponent(text) + '" data-query="' + esc(text) + '">' + esc(text) + '</a>';
  }

  function examples() {
    return '<div class="search-examples"><p>Try searching for:</p><ul>' +
      EXAMPLES.map(function (e) { return '<li>' + searchLink(e) + '</li>'; }).join('') + '</ul></div>';
  }

  function render(raw) {
    var container = document.getElementById('search-results');
    var countEl = document.getElementById('search-count');
    if (!container) return;
    raw = raw.trim();
    if (!raw) { container.innerHTML = examples(); countEl.textContent = ''; return; }

    var q = parseQuery(raw);
    var results = run(q, true);
    var notice = '', shownQuery = q;

    if (!results.length) {
      var fix = correctedQuery(q);
      var fixedQ = fix && parseQuery(fix);
      var fixedResults = fixedQ ? run(fixedQ, true) : [];
      if (fixedResults.length) {
        results = fixedResults; shownQuery = fixedQ;
        notice = '<p>No results for <strong>' + esc(raw) + '</strong>. Showing results for ' +
          '<strong>' + esc(fix) + '</strong> instead.</p>';
        countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for ' + fix;
      } else {
        results = q.terms.length > 1 ? run(q, false) : [];
        var didYouMean = fix ? '<p>Did you mean ' + searchLink(fix) + '?</p>' : '';
        if (results.length) {
          notice = '<p>No pages contain all of the words in <strong>' + esc(raw) + '</strong>. ' +
            'Showing pages that contain some of them.</p>' + didYouMean;
          countEl.textContent = 'No exact matches. ' + results.length + ' partial match' +
            (results.length !== 1 ? 'es' : '');
        } else {
          countEl.textContent = '0 results';
          container.innerHTML = '<div class="no-results"><p>No results found for <strong>' + esc(raw) +
            '</strong>.</p>' + didYouMean +
            '<p>Try fewer or different words, or <a href="index.html">browse sections</a>.</p></div>' + examples();
          return;
        }
      }
    } else {
      countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for ' + raw;
    }

    container.innerHTML = (notice ? '<div class="search-notice">' + notice + '</div>' : '') +
      '<ol class="search-results-list">' + results.map(function (r) { return renderResult(r, shownQuery); }).join('') + '</ol>';
  }

  /* -- Wiring ----------------------------------------------------------------- */

  var loading = null;
  function loadIndex() {
    if (!loading) {
      loading = fetch(INDEX_URL)
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (data) { pages = prepare(data); return pages; });
    }
    return loading;
  }

  function initSearchPage() {
    var input = document.getElementById('search-input');
    var container = document.getElementById('search-results');
    if (!input || !container) return;
    var form = input.form;

    var initial = new URLSearchParams(window.location.search).get('q') || '';
    if (initial) input.value = initial;

    function update() {
      var q = input.value.trim();
      var url = new URL(window.location);
      if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
      window.history.replaceState({}, '', url);
      if (pages) render(q);
    }

    loadIndex()
      .then(function () { render(input.value); })
      .catch(function () {
        container.innerHTML = '<p>Search is temporarily unavailable.</p>';
      });

    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(update, 200);
    });
    if (form) form.addEventListener('submit', function (e) {
      e.preventDefault();
      clearTimeout(timer);
      update();
    });
    // Example and "did you mean" links search in place instead of reloading.
    container.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-query]');
      if (!a) return;
      e.preventDefault();
      input.value = a.getAttribute('data-query');
      update();
      input.focus();
    });
  }

  /* Top matches for the suggestion list: each page's best section. */
  function suggestions(raw) {
    var q = parseQuery(raw, true);
    var items = run(q, true).slice(0, SUGGESTIONS).map(function (r) {
      var hl = highlightParam(q, r.page);
      var sec = r.shown[0] && r.shown[0].sec.id ? r.shown[0].sec : null;
      return {
        url: new URL(r.page.url, ROOT).href + (hl ? '?' + hl : '') + (sec ? '#' + sec.id : ''),
        title: sec ? sec.h : r.page.title,
        context: sec ? r.page.title : (r.page.label || r.page.parent || '')
      };
    });
    return { q: q, items: items };
  }

  /* Header / homepage search box -> ARIA combobox with a suggestion list.
     Enter with nothing chosen still submits to the search page as before,
     and if the index can't load the box simply behaves as a plain form. */
  var suggestCount = 0;
  function initSuggest(input) {
    var form = input.form;
    var id = 'search-suggest-' + (++suggestCount);
    var list = document.createElement('ul');
    list.id = id;
    list.className = 'search-suggest';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Search suggestions');
    list.hidden = true;
    var status = document.createElement('div');
    status.className = 'sr-only';
    status.setAttribute('role', 'status');
    form.classList.add('has-search-suggest');
    form.appendChild(list);
    form.appendChild(status);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id);
    input.setAttribute('autocomplete', 'off');

    var items = [], active = -1, timer;

    function close() {
      list.hidden = true;
      items = [];
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function setActive(i) {
      var opts = list.children;
      if (opts[active]) opts[active].setAttribute('aria-selected', 'false');
      active = i;
      opts[i].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', opts[i].id);
      opts[i].scrollIntoView({ block: 'nearest' });
    }

    // Keep the list inside 16px screen gutters (it is right-aligned to the form).
    function fit() {
      list.style.transform = '';
      var r = list.getBoundingClientRect(), shift = 0;
      if (r.right > window.innerWidth - 16) shift = window.innerWidth - 16 - r.right;
      if (r.left + shift < 16) shift = 16 - r.left;
      if (shift) list.style.transform = 'translateX(' + Math.round(shift) + 'px)';
    }

    function update() {
      var raw = input.value.trim();
      if (raw.length < 2) { close(); status.textContent = ''; return; }
      loadIndex().then(function () {
        if (input.value.trim() !== raw || document.activeElement !== input) return;
        var s = suggestions(raw);
        items = s.items.concat([{ url: form.action + '?q=' + encodeURIComponent(raw), all: true }]);
        list.innerHTML = items.map(function (it, i) {
          var body = it.all
            ? 'See all results for <strong>' + esc(raw) + '</strong>'
            : '<span class="search-suggest__title">' + markAll(it.title, s.q) + '</span>' +
              (it.context ? '<span class="search-suggest__context">' + esc(it.context) + '</span>' : '');
          return '<li role="option" id="' + id + '-' + i + '" aria-selected="false" class="search-suggest__option' +
            (it.all ? ' search-suggest__all' : '') + '">' + body + '</li>';
        }).join('');
        active = -1;
        input.removeAttribute('aria-activedescendant');
        list.hidden = false;
        fit();
        input.setAttribute('aria-expanded', 'true');
        status.textContent = s.items.length
          ? s.items.length + (s.items.length === 1 ? ' suggestion' : ' suggestions') + '. Use the up and down arrows to choose.'
          : 'No suggestions. Press Enter to search.';
      }, close);
    }

    input.addEventListener('focus', function () { loadIndex().catch(function () {}); });
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(update, 120);
    });
    input.addEventListener('keydown', function (e) {
      if (list.hidden) {
        if (e.key === 'ArrowDown') { e.preventDefault(); update(); }
        return;
      }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1 < items.length ? active + 1 : 0); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active > 0 ? active - 1 : items.length - 1); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); location.href = items[active].url; }
    });
    input.addEventListener('blur', close);
    // mousedown would blur the input (closing the list) before the click lands.
    list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    list.addEventListener('click', function (e) {
      var li = e.target.closest('[role="option"]');
      if (li) location.href = items[Array.prototype.indexOf.call(list.children, li)].url;
    });
  }

  function init() {
    initSearchPage();
    var inputs = document.querySelectorAll('form[action$="search.html"] input[name="q"]');
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.id !== 'search-input') initSuggest(input);
    });
  }

  document.addEventListener('DOMContentLoaded', init);

}());
"""


# ==============================================================================
# SECTION 4 -- HTML base template
# ==============================================================================

# ==============================================================================
# Site chrome -- see brand/ECL-Migration-Spec.md section 4.
# Most functions below return "" when ECL_CHROME is False, so a move into
# Drupal / oe_theme (which supplies its own chrome) is one flag rather than a
# rewrite. The exception is ecl_scripts(): it always emits (see its own
# docstring for why) -- only ecl_chrome_scripts() at the end of this section
# is gated, carrying just the genuinely chrome-specific webtools loader and
# cookie-consent JSON.
# ==============================================================================

# First src of each Inter @font-face in ecl-ec.css. Preloading these exact URLs
# starts the font fetch while the HTML is still parsing (rather than after the
# CSS is parsed and text is laid out), so the font is ready inside the
# font-display:fallback block period and pages first paint in Inter. Must stay
# in sync with ecl-ec.css's @font-face src order, or the preload is wasted.
INTER_FONT_URLS = [
    "https://webtools.europa.eu/fonts/inter/InterVariable.woff2",
    "https://webtools.europa.eu/fonts/inter/InterVariable-Italic.woff2",
]


def ecl_head_links(root: str) -> str:
    """Stylesheet, font and favicon links for <head>."""
    e = f"{root}assets/ecl"
    preloads = "".join(
        f'  <link rel="preload" href="{u}" as="font" type="font/woff2" crossorigin>\n'
        for u in INTER_FONT_URLS
    )
    return (
        preloads +
        f'  <link rel="stylesheet" href="{e}/styles/optional/ecl-ec-default.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{e}/styles/ecl-ec.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{e}/styles/optional/ecl-ec-utilities.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{root}assets/style.css">\n'
        f'  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg">\n'
        f'  <link rel="icon" type="image/x-icon" href="{root}favicon.ico">\n'
        f'  <link rel="apple-touch-icon" href="{root}apple-touch-icon.png">'
    )


def ecl_globan() -> str:
    """Official-status strip. Task 5 replaces this with the webtools globan."""
    if not ECL_CHROME:
        return ""
    return (
        '<div id="cck_here"><!-- cookie consent --></div>\n'
        '<script type="application/json">'
        '{"utility":"globan","theme":"light","logo":true,"link":true,"mode":false}'
        '</script>'
    )


# Path data copied from ECL 5's sprite (see brand/ecl/reference-menu-search.html),
# inlined because icons/icons.svg is not vendored (spec section 7.2).
_ECL_ICON_PATHS = {
    "corner-arrow": '<path d="m45 30.12-2.73 2.82-18.24-18.36L5.73 33 3 30.18 24.03 9z"/>',
    "arrow-left": '<path fill-rule="evenodd" d="M24.15 11.1 22.02 9 6 25.02 22.05 41.1l2.13-2.1-12.423-12.45H42v-3H11.7z"/>',
    "search": '<path fill-rule="evenodd" clip-rule="evenodd" d="M3 21c0-9.93 8.07-18 18-18s18 8.07 18 18c0 4.416-1.596 8.465-4.242 11.599l11.309 11.31-2.121 2.12L32.64 34.723A17.92 17.92 0 0 1 21 39c-9.93 0-18-8.07-18-18m33 0c0-8.28-6.72-15-15-15S6 12.72 6 21s6.72 15 15 15 15-6.72 15-15"/>',
    "hamburger": '<path fill-rule="evenodd" d="M42 12H6v3h36zM42 33H6v3h36zM6 22.5h36v3H6z"/>',
    "close": '<path fill-rule="evenodd" d="M42 8.01 39.99 6 24 21.99 8.01 6 6 8.01 21.99 24 6 39.99 8.01 42 24 26.01 39.99 42 42 39.99 26.01 24z"/>',
    # Not in brand/ecl/reference-menu-search.html, so drawn to match the
    # sprite's 48x48 grid and 3px stroke rather than copied from it.
    "download": '<path fill-rule="evenodd" d="M25.5 6h-3v23.38l-8.44-8.44-2.12 2.12L24 35.12l12.06-12.06-2.12-2.12-8.44 8.44zM9 39h30v3H9z"/>',
    "file": '<path fill-rule="evenodd" d="M28.62 3H9v42h30V13.38zM28.5 7.24 34.76 13.5H28.5zM12 42V6h13.5v10.5H36V42z"/>',
}


def _ecl_icon(name: str, classes: str) -> str:
    return (
        f'<svg class="ecl-icon {classes} ecl-icon--{name}" viewBox="0 0 48 48" fill="currentColor" '
        f'focusable="false" aria-hidden="true">{_ECL_ICON_PATHS[name]}</svg>'
    )


# Social network icons, copied verbatim from the live DG ENV footer
# (environment.ec.europa.eu/topics/nature-and-biodiversity/wildlife-trade_en).
# There, Webtools swaps each `<span class="wt-icon-networks--*">` for an
# inline SVG at runtime; Webtools isn't loaded here, so those spans rendered
# blank and the SVGs are inlined instead. Paths with class "cutout" are the
# glyph knocked out of a solid shape (Facebook "f", LinkedIn "in", Mastodon
# "m") and are filled with the footer background colour in CSS.
_NETWORK_ICONS = {
    "facebook": ("0 0 48 48", '<path fill="currentColor" d="M46 24.134C46 11.911 36.15 2 24 2S2 11.91 2 24.134C2 35.182 10.045 44.34 20.563 46V30.532h-5.586v-6.398h5.585v-4.876c0-5.548 3.285-8.612 8.31-8.612 2.407 0 4.925.432 4.925.432v5.448h-2.775c-2.732 0-3.584 1.705-3.584 3.457v4.151h6.1l-.973 6.398h-5.127V46C37.954 44.339 46 35.182 46 24.134"/><path class="cutout" d="m32.756 30.539.984-6.287h-6.16v-4.079c0-1.72.862-3.396 3.62-3.396H34v-5.353S31.458 11 29.028 11c-5.073 0-8.39 3.01-8.39 8.461v4.791H15v6.287h5.639v15.197c1.131.174 2.29.264 3.47.264 1.182 0 2.34-.09 3.47-.264V30.54h5.177Z"/>'),
    "instagram": ("0 0 48 48", '<path fill="currentColor" d="M46 23.96c-.01-5.97-.04-6.72-.15-9.07s-.48-3.94-1.03-5.34c-.57-1.45-1.32-2.67-2.54-3.89s-2.45-1.97-3.9-2.53c-1.4-.54-3-.91-5.34-1.01s-3.1-.13-9.07-.12-6.72.04-9.07.15c-2.34.11-3.94.48-5.34 1.03-1.45.56-2.67 1.32-3.89 2.54S3.7 8.17 3.14 9.62c-.54 1.4-.91 3-1.01 5.34s-.13 3.09-.11 9.07c.01 5.97.04 6.72.15 9.07.11 2.34.48 3.94 1.03 5.34.56 1.45 1.32 2.67 2.54 3.89s2.45 1.97 3.9 2.53c1.4.54 3 .91 5.34 1.01s3.1.13 9.07.11c5.97-.01 6.72-.04 9.07-.15s3.94-.49 5.33-1.03c1.45-.57 2.67-1.32 3.89-2.54s1.97-2.45 2.53-3.9c.54-1.4.91-3 1.01-5.34.1-2.35.13-3.1.12-9.07Zm-4.07 8.9c-.09 2.14-.45 3.31-.75 4.09-.4 1.03-.87 1.76-1.64 2.53s-1.5 1.25-2.53 1.65c-.77.3-1.94.66-4.08.77-2.32.11-3.02.13-8.89.14s-6.57-.01-8.89-.11c-2.15-.09-3.31-.45-4.09-.75-1.03-.4-1.76-.87-2.53-1.64s-1.25-1.5-1.65-2.53c-.3-.78-.66-1.94-.76-4.08-.11-2.32-.13-3.01-.14-8.89-.01-5.87 0-6.57.11-8.89.09-2.14.45-3.31.75-4.08.4-1.03.87-1.76 1.64-2.53s1.5-1.25 2.53-1.65c.78-.3 1.94-.66 4.08-.76 2.32-.11 3.02-.13 8.89-.14s6.57 0 8.89.11c2.14.09 3.31.45 4.08.75 1.03.4 1.76.87 2.53 1.64s1.25 1.5 1.65 2.53c.3.77.66 1.94.76 4.08.11 2.32.14 3.02.15 8.89s-.01 6.57-.11 8.89Z"/><path fill="currentColor" d="M23.98 12.7c-6.24.01-11.29 5.08-11.27 11.32.01 6.24 5.08 11.29 11.32 11.27 6.24-.01 11.29-5.08 11.28-11.32S30.23 12.68 23.99 12.7Zm6.8 14.09a7.36 7.36 0 0 1-2.69 3.3c-1.2.81-2.62 1.24-4.07 1.24-.96 0-1.92-.19-2.81-.55s-1.7-.91-2.38-1.58a7.36 7.36 0 0 1-2.15-5.19c0-1.45.42-2.87 1.23-4.08.8-1.21 1.95-2.15 3.29-2.71a7.325 7.325 0 0 1 8 1.57 7.26 7.26 0 0 1 2.01 3.75c.29 1.42.14 2.9-.41 4.24ZM37.18 10.04c-.43-.29-.95-.44-1.47-.44-.7 0-1.37.28-1.86.78s-.77 1.17-.77 1.87a2.636 2.636 0 0 0 1.64 2.44c.48.2 1.01.25 1.53.15.51-.1.98-.36 1.35-.73s.62-.84.72-1.35.05-1.04-.15-1.53c-.2-.48-.54-.89-.97-1.18Z"/>'),
    "x": ("0 0 24 24", '<path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>'),
    "youtube": ("0 0 461 461", '<path fill="currentColor" d="M365.257,67.393H95.744C42.866,67.393,0,110.259,0,163.137v134.728 c0,52.878,42.866,95.744,95.744,95.744h269.513c52.878,0,95.744-42.866,95.744-95.744V163.137 C461.001,110.259,418.135,67.393,365.257,67.393z M300.506,237.056l-126.06,60.123c-3.359,1.602-7.239-0.847-7.239-4.568V168.607 c0-3.774,3.982-6.22,7.348-4.514l126.06,63.881C304.363,229.873,304.298,235.248,300.506,237.056z"/>'),
    "linkedin": ("0 0 48 48", '<path fill="currentColor" d="M42.74 2.003H5.247A3.21 3.21 0 0 0 2 5.175v37.65A3.213 3.213 0 0 0 5.247 46H42.74A3.22 3.22 0 0 0 46 42.825V5.172A3.218 3.218 0 0 0 42.74 2"/><path class="cutout" d="M31.295 17.778c6.506 0 7.705 4.286 7.705 9.863L38.997 39H32.58V28.6c-.01-2.345-.193-5.161-3.338-5.161-3.266 0-3.83 2.496-3.854 5.129v10.431h-6.42V18.293h6.162v2.83h.087a6.75 6.75 0 0 1 6.08-3.345Zm-16.36.515V39H8.509V18.293h6.426ZM11.725 8a3.728 3.728 0 0 1 3.724 3.73c0 .99-.392 1.94-1.09 2.64a3.721 3.721 0 0 1-2.634 1.093A3.728 3.728 0 0 1 8 11.732 3.728 3.728 0 0 1 11.724 8Z"/>'),
    "mastodon": ("0 0 40 40", '<path fill="currentColor" d="M36.364 23.647C35.8703 26.2341 31.8613 29.0581 27.2599 29.6111C24.8703 29.8876 22.5005 30.1641 19.9924 30.0456C15.8846 29.8481 12.6261 29.0581 12.6261 29.0581C12.6261 29.4531 12.6459 29.8481 12.7051 30.2036C13.2383 34.252 16.7338 34.5088 20.0319 34.6273C23.3694 34.7458 26.3317 33.7978 26.3317 33.7978L26.4699 36.8194C26.4699 36.8194 24.1396 38.0635 19.9726 38.3005C17.6818 38.419 14.838 38.2413 11.5202 37.3723C4.37117 35.4567 3.12701 27.7745 2.94927 20.0133C2.89003 17.7027 2.92952 15.5303 2.92952 13.7134C2.92952 5.75472 8.14317 3.42438 8.14317 3.42438C10.75 2.21971 15.2724 1.70625 19.9529 1.66675H20.0714C24.7518 1.70625 29.2742 2.21971 31.9008 3.42438C31.9008 3.42438 37.1145 5.75472 37.1145 13.7134C37.0947 13.6937 37.1737 19.559 36.364 23.647Z"/><path class="cutout" d="M30.9679 14.3255V23.9629H27.1564V14.602C27.1564 12.6271 26.327 11.6397 24.6681 11.6397C22.8315 11.6397 21.923 12.8246 21.923 15.1747V20.2896H18.1313V15.155C18.1313 12.8049 17.2031 11.6199 15.3862 11.6199C13.7273 11.6199 12.8979 12.6271 12.8979 14.5822V23.9234H9.06665V14.3255C9.06665 12.3506 9.56037 10.7905 10.5675 9.64508C11.6142 8.47991 12.9571 7.88745 14.6555 7.88745C16.6106 7.88745 18.0918 8.6379 19.0595 10.1388L20.0074 11.7384L20.9553 10.1388C21.923 8.6379 23.4042 7.88745 25.3593 7.88745C27.0379 7.88745 28.4006 8.47991 29.4473 9.64508C30.4742 10.7905 30.9679 12.3506 30.9679 14.3255Z"/>'),
    "bluesky": ("0 0 48 48", '<path fill="currentColor" fill-rule="evenodd" d="M12.1 8.473c4.818 3.59 9.99 10.865 11.893 14.78v10.324c0-.216-.082.027-.273.57-1 2.899-4.872 14.198-13.727 5.161-4.667-4.755-2.504-9.51 5.994-10.947-4.858.827-10.318-.542-11.824-5.852C3.725 20.978 3 11.549 3 10.275c0-6.395 5.625-4.403 9.1-1.802m23.8 0c-4.818 3.59-9.99 10.865-11.893 14.78v10.324c0-.216.082.027.273.57 1 2.899 4.872 14.198 13.727 5.161 4.666-4.755 2.504-9.51-5.994-10.947 4.858.827 10.318-.542 11.824-5.852C44.275 20.978 45 11.549 45 10.275c0-6.395-5.625-4.403-9.1-1.802"/>'),
    "chain": ("0 0 48 48", '<path fill="currentColor" d="M23.55 30.75a6.56 6.56 0 0 0 4.68 1.95c1.77 0 3.42-.69 4.68-1.95l10.56-10.56a6.627 6.627 0 0 0 0-9.36l-2.1-2.1a6.627 6.627 0 0 0-9.36 0l-5.28 5.28 2.97 2.97 5.28-5.28c.93-.93 2.49-.93 3.42 0l2.1 2.1c.93.93.93 2.49 0 3.42L29.94 27.78c-.93.9-2.52.9-3.42 0l-3.15-3.15-2.97 2.97z"/><path fill="currentColor" d="M6.6 39.27c1.29 1.29 3 1.92 4.68 1.92 1.71 0 3.39-.63 4.68-1.92l5.31-5.31-2.97-2.97-5.31 5.31c-.96.96-2.49.93-3.42 0l-2.1-2.1c-.93-.96-.93-2.49 0-3.42l10.56-10.56c.96-.96 2.49-.93 3.42 0l3.18 3.18 2.97-2.97-3.18-3.18a6.627 6.627 0 0 0-9.36 0L4.5 27.81a6.627 6.627 0 0 0 0 9.36z"/>'),
}


def _social_links(links: list[tuple[str, str, str]], section: str) -> str:
    """`<li>` items for an ecl-social-media-follow list: (href, network, label)."""
    items = []
    for href, network, label in links:
        view_box, paths = _NETWORK_ICONS[network]
        items.append(
            f'<li class="ecl-social-media-follow__item">'
            f'<a href="{href}" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-link--icon ecl-social-media-follow__link ecl-link--icon-only" '
            f'data-section-label-untranslated="{section}" data-footer-link-label="{label}">'
            f'<svg class="ecl-icon ecl-icon--s ecl-link__icon ecl-social-media-follow__icon ecl-icon-networks--{network}" '
            f'viewBox="{view_box}" focusable="false" aria-hidden="true">{paths}</svg>'
            f'<span class="ecl-link__label">{label}</span></a></li>'
        )
    return "\n".join(items)


def ecl_site_header(root: str) -> str:
    """<header class="ecl-site-header ..."> with an embedded ECL mega menu + search form.

    Outer shell modelled on brand/ecl/quick_example.html (the vendored v5.1.0
    reference). Menu and search markup modelled on
    brand/ecl/reference-menu-search.html, fetched live from the EC Component
    Library's SiteHeader/mega-menu Storybook demo -- that demo reported
    v5.2.2 at fetch time, not this migration's v5.1.0 target; see that file
    for the full version-mismatch note (spec section 10 -- header-only risk).
    Icons are inlined via _ecl_icon() rather than referencing
    icons/icons.svg#name as brand/ecl/quick_example.html does, because that
    sprite is not vendored (spec section 7.2) and the buttons rendered blank.
    """
    if not ECL_CHROME:
        return ""
    guide_sublist_items = "".join(
        f'<li class="ecl-mega-menu__subitem" data-ecl-mega-menu-subitem>'
        f'<a href="{root}chapters/{h(slug)}.html" class="ecl-link ecl-link--standalone ecl-mega-menu__sublink" data-ecl-mega-menu-sublink>{h(item_title)}</a>'
        f'</li>'
        for item_title, slug in _GUIDE_NAV_ITEMS
    )
    annexes_href = f'{root}chapters/{h(_ANNEXES_SLUG)}.html' if _ANNEXES_SLUG else f'{root}index.html'
    # The whole-guide PDF link sits in the info pane under the summary, not in
    # the sublist, where it would read as an 11th section. It deliberately
    # avoids ECL's own .ecl-mega-menu__info-link class: ECL's arrow-key
    # handler walks a fixed DOM path from that link into a see-all <li>
    # inside the sublist, and throws from anywhere else.
    logo =f"{root}assets/ecl/images/logo/positive/logo-ec--en.svg"
    return f"""<header class="ecl-site-header ecl-site-header-with-logo-l ecl-site-header--has-mega-menu" data-ecl-auto-init="SiteHeader">
  <div class="ecl-site-header__inner">
    <div class="ecl-site-header__background">
      <div class="ecl-site-header__header">
        <div class="ecl-site-header__container ecl-container">
          <div class="ecl-site-header__top">
            <a href="{root}index.html" class="ecl-link ecl-link--standalone ecl-site-header__logo-link">
              <picture class="ecl-picture ecl-site-header__picture">
                <source srcset="{logo}" media="(min-width: 996px)">
                <img class="ecl-site-header__logo-image ecl-site-header__logo-image--l" src="{logo}" alt="European Commission">
              </picture>
            </a>
            <div class="ecl-site-header__action">
              <script type="application/json">{{
                "service": "etrans",
                "delay": 1000,
                "lang": "en",
                "languages": {{
                  "exclude": ["en"]
                }},
                "renderAs": {{
                  "icon": true,
                  "button": false,
                  "link": false
                }}
              }}</script>
              <div class="ecl-site-header__search-container" role="search">
                <button class="ecl-button ecl-button--tertiary ecl-button--neutral ecl-site-header__search-toggle ecl-button--icon-only" type="button" data-ecl-search-toggle aria-controls="header-search-form" aria-expanded="false">
                  <span class="ecl-button__container">
                    <span class="ecl-button__label" data-ecl-label="true">Search</span>
                    {_ecl_icon("search", "ecl-icon--m ecl-button__icon ecl-site-header__icon")}
                  </span>
                </button>
                <form class="ecl-search-form ecl-site-header__search" action="{root}search.html" method="get" data-ecl-search-form id="header-search-form">
                  <div class="ecl-form-group">
                    <label for="header-search-input" id="header-search-input-label" class="ecl-form-label-wrapper ecl-search-form__label">
                      <span class="ecl-form-label">Search</span>
                    </label>
                    <input id="header-search-input" name="q" class="ecl-text-input ecl-text-input--m ecl-search-form__text-input" type="search" aria-label="Search the guide">
                  </div>
                  <button class="ecl-button ecl-button--tertiary ecl-button--neutral ecl-search-form__button ecl-button--icon-only" type="submit">
                    <span class="ecl-button__container">
                      {_ecl_icon("search", "ecl-icon--m ecl-button__icon")}
                      <span class="ecl-button__label" data-ecl-label="true">Search</span>
                    </span>
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
        <div class="ecl-site-header__banner">
          <div class="ecl-container">
            <div class="ecl-site-header__site-name">{h(SITE_NAME_BANNER)}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <nav class="ecl-mega-menu" data-ecl-mega-menu data-ecl-auto-init="MegaMenu" data-ecl-mega-menu-label-open="Menu" data-ecl-mega-menu-label-close="Close" role="navigation" id="main-nav" aria-label="Main navigation">
    <div class="ecl-mega-menu__overlay">&nbsp;</div>
    <div class="ecl-container ecl-mega-menu__container">
      <button class="ecl-button ecl-button--tertiary ecl-button--neutral ecl-mega-menu__open ecl-button--icon-only" type="button" data-ecl-mega-menu-open aria-expanded="false">
        <span class="ecl-button__container">
          {_ecl_icon("hamburger", "ecl-icon--m ecl-button__icon")}
          {_ecl_icon("close", "ecl-icon--m ecl-button__icon")}
          <span class="ecl-button__label" data-ecl-label="true">Menu</span>
        </span>
      </button>
      <section class="ecl-mega-menu__inner" data-ecl-mega-menu-inner>
        <header class="ecl-mega-menu__inner-header">
          <button class="ecl-button ecl-button--tertiary ecl-mega-menu__back" type="submit" data-ecl-mega-menu-back>
            <span class="ecl-button__container">
              {_ecl_icon("corner-arrow", "ecl-icon--xs ecl-icon--rotate-270 ecl-button__icon")}
              <span class="ecl-button__label" data-ecl-label="true">Back</span>
            </span>
          </button>
        </header>
        <ul class="ecl-mega-menu__list">
          <li class="ecl-mega-menu__item" data-ecl-mega-menu-item id="ecl-mega-menu-item-main-0">
            <a href="{root}index.html" class="ecl-link ecl-link--standalone ecl-mega-menu__link" data-ecl-mega-menu-link id="ecl-mega-menu-item-main-0-link">Home</a>
          </li>
          <li class="ecl-mega-menu__item" data-ecl-mega-menu-item id="ecl-mega-menu-item-main-1">
            <a href="{root}about.html" class="ecl-link ecl-link--standalone ecl-mega-menu__link" data-ecl-mega-menu-link id="ecl-mega-menu-item-main-1-link">{h(ABOUT_TITLE)}</a>
          </li>
          <li class="ecl-mega-menu__item ecl-mega-menu__item--has-children ecl-mega-menu__item--one-level-only" data-ecl-mega-menu-item data-ecl-has-children aria-haspopup="true" id="ecl-mega-menu-item-main-2">
            <button class="ecl-button ecl-button--primary ecl-mega-menu__link" type="button" data-ecl-mega-menu-link id="ecl-mega-menu-item-main-2-link" aria-expanded="false">
              <span class="ecl-button__container">
                <span class="ecl-button__label" data-ecl-label="true">Reference Guide</span>
                {_ecl_icon("corner-arrow", "ecl-icon--2xs ecl-icon--rotate-90 ecl-button__icon")}
                {_ecl_icon("arrow-left", "ecl-icon--s ecl-icon--flip-horizontal ecl-button__icon")}
              </span>
            </button>
            <div class="ecl-mega-menu__wrapper">
              <div class="ecl-container">
                <div class="ecl-mega-menu__info" data-ecl-mega-menu-mega>
                  <span class="ecl-mega-menu__info-title">Reference Guide</span>
                  <div class="ecl-mega-menu__info-scrollable">
                    <div class="ecl-mega-menu__info-content">Browse the sections of the EU Wildlife Trade Regulations Reference Guide.</div>
                  </div>
                  <a href="{root}{PDF_FILENAME}" class="ecl-link ecl-link--standalone ecl-link--icon mega-menu-pdf-link">{_ecl_icon("download", "ecl-icon--s ecl-link__icon")}<span class="ecl-link__label">Download the full guide (PDF)</span></a>
                </div>
                <div class="ecl-mega-menu__mega" data-ecl-mega-menu-mega>
                  <ul class="ecl-mega-menu__sublist" aria-label="Pages in this section">
                    {guide_sublist_items}
                  </ul>
                </div>
              </div>
            </div>
          </li>
          <li class="ecl-mega-menu__item" data-ecl-mega-menu-item id="ecl-mega-menu-item-main-3">
            <a href="{annexes_href}" class="ecl-link ecl-link--standalone ecl-mega-menu__link" data-ecl-mega-menu-link id="ecl-mega-menu-item-main-3-link">Annexes</a>
          </li>
        </ul>
      </section>
    </div>
  </nav>
</header>"""


def ecl_breadcrumb(segments: list[tuple[str, str | None]]) -> str:
    """ECL breadcrumb component. Each segment is (label, href); href=None marks
    the current page (always the last segment). The ellipsis <li> is emitted
    right after the first (static) segment, but only when there's at least one
    "expandable" (intermediate) segment behind it to collapse -- ECL's JS
    shows/hides the ellipsis and the expandable segments based on available
    width. With only 2 segments (Home + current page, no intermediates) there
    is nothing to collapse, and omitting the ellipsis lets ECL's own
    :first-of-type:nth-last-of-type(2) CSS rule apply, which is what renders
    a usable "< Home"-style link at narrow viewports; keeping a pointless
    ellipsis there instead defeats that rule and leaves the mobile breadcrumb
    empty (verified against the vendored ECL v5.1.0 stylesheet and reproduced
    in the unmodified brand/ecl/quick_example.html reference)."""
    if not ECL_CHROME or not segments:
        return ""

    n = len(segments)
    items = ""
    for i, (label, url) in enumerate(segments):
        is_last = i == n - 1
        if is_last:
            items += (
                f'<li class="ecl-breadcrumb__segment ecl-breadcrumb__current-page" '
                f'data-ecl-breadcrumb-item="static" aria-current="page">{h(label)}</li>\n'
            )
        else:
            item_type = "static" if i == 0 else "expandable"
            items += (
                f'<li class="ecl-breadcrumb__segment" data-ecl-breadcrumb-item="{item_type}">\n'
                f'  <a href="{h(url)}" class="ecl-link ecl-link--standalone ecl-link--no-visited ecl-breadcrumb__link">{h(label)}</a>\n'
                f'  {_ecl_icon("corner-arrow", "ecl-icon--fluid ecl-breadcrumb__icon")}\n'
                f'</li>\n'
            )
            if i == 0 and n > 2:
                items += (
                    '<li class="ecl-breadcrumb__segment ecl-breadcrumb__segment--ellipsis" data-ecl-breadcrumb-ellipsis aria-hidden="true">\n'
                    '  <button class="ecl-button ecl-button--ghost ecl-breadcrumb__ellipsis" type="button" data-ecl-breadcrumb-ellipsis-button aria-label="Show more breadcrumb items">&hellip;</button>\n'
                    f'  {_ecl_icon("corner-arrow", "ecl-icon--fluid ecl-breadcrumb__icon")}\n'
                    '</li>\n'
                )

    return f"""<div class="ecl-container">
  <nav class="ecl-breadcrumb ecl-page-header__breadcrumb" aria-label="You are here:" data-ecl-breadcrumb="true" data-ecl-auto-init="Breadcrumb">
    <ol class="ecl-breadcrumb__container">
      {items}
    </ol>
  </nav>
</div>"""


def ecl_site_footer(root: str) -> str:
    """DG ENV's real ECL footer, copied from brand/ecl/quick_example.html (see
    ECL-Migration-Spec.md section 6.6). `__row--specific` keeps DG ENV's own
    structure and links verbatim (site-info, contact, about, related links,
    social accounts) per the approved decision; TRAFFIC co-branding is added
    to the site-info description alongside DG ENV's own text, and the old
    `.site-footer__cta` feedback mailto folds into the contact list as an
    extra item rather than staying a standalone CTA bar.

    `__row--common` is mandatory EC furniture the site never had: the
    mute-negative EC logo, the "Follow the European Commission" social row,
    and the legal navigation -- copied in full, minus the template's empty
    trailing `<a href="">` (spec section 1.1).

    "Download PDF" has no DG-ENV counterpart to copy, but it's the only way
    a visitor gets the offline whole-guide PDF and doesn't duplicate any
    link already in the header mega menu (unlike Home/About/Search, which
    do and are dropped). It's added as a second item in `__section--about`,
    next to Accessibility -- both are, unlike Related links' outbound EU
    topic portals, links *about this site's own material* rather than
    external destinations.

    Below DG ENV's description sits a single smallprint line in ECL's light
    blue: the EC/TRAFFIC attribution plus "Updated <Month YYYY>", taken from
    the build date so it moves on whenever the guide is rebuilt. The longer
    _footer_content.md paragraphs (revision history, opinion disclaimer,
    citation notice) used to follow it but took up too much of the footer and
    were dropped at the user's request.

    Social icons in both rows are inline SVGs (_social_links) -- the live
    site's Webtools `wt-icon-networks--*` spans render blank without
    Webtools. The common-row logo is the full negative "European Commission"
    wordmark, matching the live footer, not the mute (emblem-only) variant.
    """
    if not ECL_CHROME:
        return ""
    updated = date.today().strftime("%B %Y")
    logo = f"{root}assets/ecl/images/logo/negative/logo-ec--en.svg"
    dg_env_social = _social_links([
        ("https://www.facebook.com/EUEnvironment/", "facebook", "Facebook"),
        ("https://www.instagram.com/ourplanet_eu/", "instagram", "Instagram"),
        ("https://twitter.com/EU_ENV", "x", "X"),
        ("https://www.youtube.com/user/EUEnvironment", "youtube", "YouTube"),
        ("https://www.linkedin.com/showcase/eu-environment-climate/", "linkedin", "LinkedIn"),
    ], "Follow&#x20;us")
    ec_social = _social_links([
        ("https://ec.social-network.europa.eu/@EUCommission", "mastodon", "Mastodon"),
        ("http://www.linkedin.com/company/european-commission", "linkedin", "LinkedIn"),
        ("https://bsky.app/profile/ec.europa.eu", "bluesky", "Bluesky"),
        ("https://www.facebook.com/EuropeanCommission", "facebook", "Facebook"),
        ("https://www.youtube.com/user/eutube", "youtube", "Youtube"),
        ("https://european-union.europa.eu/contact-eu/social-media-channels_en", "chain", "Other"),
    ], "social_media_common_links")
    pdf_href = f"{root}{PDF_FILENAME}"
    return f"""<footer class="ecl-site-footer" data-logo-area-label-en="Home&#x20;-&#x20;European&#x20;Commission">
  <div class="ecl-site-footer__main">
    <div class="ecl-container ecl-site-footer__container">
      <div class="ecl-site-footer__row ecl-site-footer__row--specific">
        <div class="ecl-site-footer__section ecl-site-footer__section--site-info">
          <div class="ecl-site-footer__title">
            <a href="https://environment.ec.europa.eu/index_en" class="ecl-link ecl-link--inverted ecl-link--icon ecl-site-footer__title-link"><span class="ecl-link__label">Environment</span>{_ecl_icon("arrow-left", "ecl-icon--xs ecl-icon--flip-horizontal ecl-link__icon")}</a>
          </div>
          <div class="ecl-site-footer__description">
            This site is managed by:<br />Directorate-General for Environment
          </div>
          <p class="footer-smallprint">A reference guide developed by the European Commission and TRAFFIC. Updated {updated}.</p>
          <div class="ecl-social-media-follow ecl-social-media-follow--horizontal ecl-social-media-follow--left ecl-site-footer__social-media">
            <ul class="ecl-social-media-follow__list">
              {dg_env_social}
            </ul>
          </div>
        </div>
        <div class="ecl-site-footer__section ecl-site-footer__section--links">
          <div class="ecl-site-footer__row">
            <div class="ecl-site-footer__section ecl-site-footer__section--contact">
              <div class="ecl-site-footer__title">Contact us</div>
              <ul class="ecl-site-footer__list">
                <li class="ecl-site-footer__list-item">
                  <a href="https://ec.europa.eu/info/departments/environment_en#contact" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Contact&#x20;us" data-footer-link-label="Contact&#x20;DG&#x20;Environment">Contact DG Environment</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="mailto:antony.bagott@traffic.org" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link">Send feedback</a>
                </li>
              </ul>
            </div>
            <div class="ecl-site-footer__section ecl-site-footer__section--about">
              <div class="ecl-site-footer__title">About us</div>
              <ul class="ecl-site-footer__list">
                <li class="ecl-site-footer__list-item">
                  <a href="https://commission.europa.eu/accessibility-statement_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="About&#x20;us" data-footer-link-label="Accessibility">Accessibility</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="{pdf_href}" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link">Download PDF</a>
                </li>
              </ul>
            </div>
          </div>
          <div class="ecl-site-footer__row">
            <div class="ecl-site-footer__section ecl-site-footer__section--related">
              <div class="ecl-site-footer__title">Related links</div>
              <ul class="ecl-site-footer__list">
                <li class="ecl-site-footer__list-item">
                  <a href="https://commission.europa.eu/food-farming-fisheries_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Related&#x20;links" data-footer-link-label="Agriculture">Agriculture</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="https://oceans-and-fisheries.ec.europa.eu/index_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Related&#x20;links" data-footer-link-label="Fisheries">Fisheries</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="https://climate.ec.europa.eu/index_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Related&#x20;links" data-footer-link-label="Climate&#x20;action">Climate action</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="https://energy.ec.europa.eu/index_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Related&#x20;links" data-footer-link-label="Energy">Energy</a>
                </li>
                <li class="ecl-site-footer__list-item">
                  <a href="https://climate-pact.europa.eu/index_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="Related&#x20;links" data-footer-link-label="European&#x20;Climate&#x20;Pact">European Climate Pact</a>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
      <div class="ecl-site-footer__row ecl-site-footer__row--common">
        <div class="ecl-site-footer__section ecl-site-footer__section--common">
          <a href="https://commission.europa.eu/index_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__logo-link"><picture class="ecl-picture ecl-site-footer__picture"><img class="ecl-site-footer__logo-image" src="{logo}" alt="European Commission"></picture></a>
          <div class="ecl-site-footer__extra-links-container">
            <div class="ecl-social-media-follow ecl-social-media-follow--left ecl-social-media-follow--description_inline ecl-site-footer__social-media">
              <p class="ecl-social-media-follow__description">Follow the European Commission</p>
              <ul class="ecl-social-media-follow__list">
                {ec_social}
              </ul>
            </div>
            <ul class="ecl-site-footer__list ecl-site-footer__list--inline">
              <li class="ecl-site-footer__list-item">
                <a href="https://commission.europa.eu/about/contact_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="extra_links" data-footer-link-label="Contact&#x20;us">Contact us</a>
              </li>
            </ul>
          </div>
          <ul class="ecl-site-footer__list ecl-site-footer__list--inline">
            <li class="ecl-site-footer__list-item">
              <a href="https://commission.europa.eu/legal-notice/vulnerability-disclosure-policy_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="legal_navigation" data-footer-link-label="Report&#x20;an&#x20;IT&#x20;vulnerability">Report an IT vulnerability</a>
            </li>
            <li class="ecl-site-footer__list-item">
              <a href="https://commission.europa.eu/languages-our-websites_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="legal_navigation" data-footer-link-label="Languages&#x20;on&#x20;our&#x20;websites">Languages on our websites</a>
            </li>
            <li class="ecl-site-footer__list-item">
              <a href="https://commission.europa.eu/cookies-policy_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="legal_navigation" data-footer-link-label="Cookies">Cookies</a>
            </li>
            <li class="ecl-site-footer__list-item">
              <a href="https://commission.europa.eu/privacy-policy-websites-managed-european-commission_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="legal_navigation" data-footer-link-label="Privacy&#x20;policy">Privacy policy</a>
            </li>
            <li class="ecl-site-footer__list-item">
              <a href="https://commission.europa.eu/legal-notice_en" class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link" data-section-label-untranslated="legal_navigation" data-footer-link-label="Legal&#x20;notice">Legal notice</a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</footer>"""


def ecl_scripts(root: str) -> str:
    """Scripts after {extra_js}, before </body> -- always emitted, regardless of
    ECL_CHROME. Task 4 adds ecl-ec.js + ECL.autoInit(). Deliberately excludes
    assets/main.js and {extra_js} itself -- both stay as separate lines in
    base_html()'s own template, in their original position, so search.html
    keeps loading assets/search.js. See Step 2's note on extra_js.

    None of this is "chrome" in the ECL_CHROME sense (spec: "False -> Drupal /
    oe_theme supplies the chrome"): the chatbot is a site feature, not chrome;
    the ecl-ec.js bundle + ECL.autoInit() are needed because the page still
    emits ECL-classed markup (mega menu, in-page nav, cards, etc.) even when
    ECL_CHROME is False -- a Drupal/oe_theme host would still need ECL's JS to
    initialize that markup; and the inline shim below exists purely because of
    THIS SITE'S OWN CONTENT, so a Drupal host would still need it too (Drupal
    would not know about this site's specific heading-id scheme). Only the
    genuinely EC/chrome-specific webtools loader and cookie-consent JSON are
    gated -- see ecl_chrome_scripts() below.

    Task 8 adds the inline shim below, immediately before ECL.autoInit(). ECL's
    InpageNavigation component (vendored v5.1.0) calls document.querySelector()
    on each nav link's raw href fragment during init(); this site's section-
    numbered heading ids (e.g. "321-standard-operating-procedures", from
    markdown2's header-ids slugifier) start with a digit, which is not a valid
    CSS identifier -- querySelector() throws, aborting init() before it reaches
    the scroll-spy setup *or* the mobile trigger's click listener, so without
    this shim the in-page nav is silently inert (no highlighting, no expand/
    collapse) on every content page. The shim inserts an extra, CSS-safe empty
    anchor immediately before each such heading and repoints the nav link at
    it, leaving the heading's real id untouched for cross-references,
    footnotes and the PDF."""
    return f"""<script src="{root}assets/chatbot.js"></script>
<script>
  ChatbotWidget.init({{
    apiUrl: 'https://chatbot-api-khaki.vercel.app/api/chat',
    title: 'Wildlife Trade Regulations Assistant',
    placeholder: 'Ask about permits, forms, species listings…'
  }});
</script>
<script src="{root}assets/ecl/scripts/ecl-ec.js"></script>
<script>
  // Work around ECL InpageNavigation's document.querySelector(href) crashing
  // on digit-leading heading ids (see ecl_scripts() docstring in build_site.py).
  document.querySelectorAll('[data-ecl-inpage-navigation-link]').forEach(function (a, i) {{
    var href = a.getAttribute('href') || '';
    var frag = href.slice(1);
    if (!frag || !/^[0-9]/.test(frag)) return;
    var target = document.getElementById(frag);
    if (!target || !target.parentNode) return;
    var safeId = 'inpage-nav-anchor-' + i;
    var marker = document.createElement('span');
    marker.id = safeId;
    target.parentNode.insertBefore(marker, target);
    a.setAttribute('href', '#' + safeId);
  }});
</script>
<script>ECL.autoInit();</script>"""


def ecl_chrome_scripts() -> str:
    """Scripts after ecl_scripts(), before </body> -- gated on ECL_CHROME. Task 5
    adds the webtools loader. Both the loader and the cookie-consent JSON are
    genuinely EC/chrome-specific (spec: "False -> Drupal / oe_theme supplies
    the chrome") -- a Drupal/oe_theme host supplies its own equivalent, so
    these (and only these) return "" when ECL_CHROME is False."""
    if not ECL_CHROME:
        return ""
    return """<script defer src="https://webtools.europa.eu/load.js"></script>
<script type="application/json">{"utility":"cck"}</script>"""


def base_html(
    *,
    title: str,
    content: str,
    breadcrumbs: list[tuple[str, str | None]],
    depth: int = 0,
    sidebar_html: str = "",
    extra_js: str = "",
    page_header_html: str = "",
    top_section_id: str = "",
    hero_html: str = "",
) -> str:
    root       = "../" * depth
    page_title = f"{h(title)} -- EU Wildlife Trade Reference Guide"

    if sidebar_html:
        # No .ecl-container here: this already sits inside .container, and a
        # second container doubled the side padding, indenting the sidebar and
        # article text 32px past the page title and header.
        grid_open     = '<div class="ecl-row" data-ecl-inpage-navigation-container>'
        sidebar_col   = f'<div class="ecl-col-l-3">{sidebar_html}</div>'
        grid_close    = '</div>'
        article_open  = '<div class="ecl-col-l-9 article">'
        article_close = '</div>'
    else:
        grid_open = grid_close = sidebar_col = ""
        article_open = article_close = ""

    description = f"EU Wildlife Trade Regulations Reference Guide -- {h(title)}"

    return f"""<!DOCTYPE html>
<html lang="en" class="no-js">
<head>
  <script>
    var cl = document.querySelector('html').classList;
    cl.remove('no-js');
    cl.add('has-js');
  </script>{top_section_script(top_section_id)}
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <meta name="description" content="{description}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="EU Wildlife Trade Reference Guide">
  <meta property="og:title" content="{page_title}">
  <meta property="og:description" content="{description}">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{page_title}">
  <meta name="twitter:description" content="{description}">
{ecl_head_links(root)}
</head>
<body id="top">
<a href="#main-content" class="skip-link">Skip to main content</a>

{ecl_globan()}

{ecl_site_header(root)}

{page_header_html if page_header_html else ecl_breadcrumb(breadcrumbs)}

<div class="main-content" id="main-content">
  {hero_html}
  <div class="container">
    {grid_open}
      {sidebar_col}
      {article_open}
        {content}
      {article_close}
    {grid_close}
  </div>
</div>

{ecl_site_footer(root)}

<script src="{root}assets/main.js"></script>
<script src="{root}assets/search.js"></script>
{extra_js}
{ecl_scripts(root)}
{ecl_chrome_scripts()}
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


def top_section_id(headings: list[dict]) -> str:
    """Id of the page's first H2 (the top entry in its in-page navigation)."""
    return next((hd["id"] for hd in headings if hd["level"] == 2), "")


def top_section_script(section_id: str) -> str:
    """Head script: arriving from elsewhere via a link to the page's first
    section (e.g. about.html#11-overview from the homepage) opens the page at
    the very top, so the page title stays in view, instead of jumping past it.
    The hash is removed before the browser acts on it and restored (without
    scrolling) once the page has loaded. In-page clicks are unaffected."""
    if not section_id:
        return ""
    return f"""
  <script>
    if (location.hash === {json.dumps('#' + section_id)}) {{
      var topHash = location.hash, bare = location.pathname + location.search;
      history.replaceState(history.state, '', bare);
      addEventListener('load', function () {{
        history.replaceState(history.state, '', bare + topHash);
      }});
    }}
  </script>"""


def heading_items(headings: list[dict]) -> list[tuple[str, str, str]]:
    """H2 headings -> (ref, title, href) triples."""
    h2s = [hd for hd in headings if hd["level"] == 2]
    return [(*split_heading_ref(hd["text"]), f'#{hd["id"]}') for hd in h2s]


def make_sidebar(headings: list[dict], depth: int = 1) -> str:
    """ECL in-page navigation (spec §6.5). Structure per brand/ecl/quick_example.html:
    nav.ecl-inpage-navigation > __title, __body > __trigger-wrapper (trigger button
    + icon) and __list of __item > ecl-link ecl-inpage-navigation__link. ECL's own
    trigger provides the mobile affordance, so there is no separate mobile contents
    box (make_mobile_contents() was removed -- see Task 8).

    The trigger's [data-ecl-inpage-navigation-trigger-current] label was left
    empty pre-fix -- cosmetically harmless while the trigger was hidden below
    996px (see Task 8's report), but the final-review CSS override below now
    makes the trigger visible (and clickable) at those widths, so an empty
    label would show as a blank button. Seed it with the nav's own title
    ("Page contents") as a simple static fallback; ECL's own JS updates it to
    the active section's heading once InpageNavigation's scroll-spy runs.
    """
    items = heading_items(headings)
    if not items:
        return ""
    root  = "../" * depth
    nav_title = "Page contents"

    list_items = ""
    for ref, title, href in items:
        label = f"{ref} {title}" if ref else title
        list_items += (
            '<li class="ecl-inpage-navigation__item">'
            f'<a href="{h(href)}" class="ecl-link ecl-inpage-navigation__link" data-ecl-inpage-navigation-link>{h(label)}</a>'
            '</li>\n'
        )

    return f"""<nav class="ecl-inpage-navigation" data-ecl-auto-init="InpageNavigation" data-ecl-inpage-navigation="true" aria-labelledby="inpage-navigation-title">
  <div class="ecl-inpage-navigation__title" id="inpage-navigation-title">{nav_title}</div>
  <div class="ecl-inpage-navigation__body">
    <div class="ecl-inpage-navigation__trigger-wrapper">
      <button type="button" class="ecl-inpage-navigation__trigger" id="inpage-navigation-trigger" data-ecl-inpage-navigation-trigger="true" aria-controls="inpage-navigation-list" aria-expanded="false" aria-label="Page contents trigger">
        <span class="ecl-inpage-navigation__trigger-current" data-ecl-inpage-navigation-trigger-current="true">{nav_title}</span>
        {_ecl_icon("corner-arrow", "ecl-icon--xs ecl-icon--rotate-180 ecl-inpage-navigation__trigger-icon")}
      </button>
    </div>
    <ul class="ecl-inpage-navigation__list" data-ecl-inpage-navigation-list="true" id="inpage-navigation-list">
      {list_items}
    </ul>
  </div>
</nav>"""


def make_page_header(
    *,
    title: str,
    breadcrumbs: list[tuple[str, str | None]],
    meta: list[str] | None = None,
) -> str:
    """ECL page-header component: breadcrumb + h1 + meta, as one full-width block."""
    breadcrumb_html = ecl_breadcrumb(breadcrumbs)

    meta_html = ""
    if meta:
        items = "".join(f'<li class="page-header__meta-item">{h(m)}</li>' for m in meta)
        meta_html = f'<ul class="page-header__meta">{items}</ul>'

    return f"""
<div class="page-header">
  {breadcrumb_html}
  <div class="ecl-container">
    {meta_html}
    <h1 class="ecl-page-header__title">{h(title)}</h1>
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

    sidebar_html = make_sidebar(headings, depth=1)

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
        top_section_id=top_section_id(headings),
        page_header_html=page_header_html,
    )


def annex_first_heading(sub: dict) -> str:
    """Return the first H2 heading text from an annex sub-page body, with any
    inline markdown (bold/italic/footnote markers) stripped for display as a
    plain-text card excerpt."""
    m = re.search(r'^## (.+)$', sub["body"], re.MULTILINE)
    return strip_markdown(m.group(1)) if m else ""


def _landing_intro_body(body: str) -> str:
    """A parent section's (3, 4, Annexes) own intro text, without the list of
    sub-pages that follows it. Handles "## Sub-sections" at the start of the
    body (Section 3: no intro text), mid-body (Section 4), and a plain
    markdown link list with no heading (Annexes parent)."""
    m = re.search(r'(?:^|\n)## ', body)
    if m:
        return body[:m.start()].strip()
    m2 = re.search(r'\n- \[', body)
    if m2:
        return body[:m2.start()].strip()
    return body


def build_parent_landing(ch: dict, sub_chapters: list[dict], nav_sections: list[dict], summaries: dict) -> str:
    """Landing page for parent sections (3, 4, Annexes): intro + sub-page card grid."""
    body = _landing_intro_body(ch["body"])
    intro_html = render_markdown(body) if body else ""

    is_annexes = ch["slug"] == "annexes"

    # Sub-page cards. Landing pages are a hub of cards, not "content" --
    # they don't get a contents box or sidebar.
    cards = []
    for sub in sub_chapters:
        href = f'{sub["slug"]}.html'
        if is_annexes:
            heading     = sub["title"]          # "Annex I", "Annex II", …
            description = annex_first_heading(sub)
            num_label   = None
        else:
            heading     = sub["title"]
            description = summaries.get(sub["slug"]) or first_sentences(strip_markdown(sub["body"]), 2)
            ss    = sub.get("sub_section", "")
            nm    = re.match(r"^(\d+(?:\.\d+)+)", ss) if ss else None
            num_label = nm.group(1) if nm else None
        cards.append(_make_subpage_card(href, heading, description, num_label))

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
{_make_cards_grid(cards, "subpage-grid")}
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

    sidebar_html = make_sidebar(headings, depth=1)

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
        top_section_id=top_section_id(headings),
        page_header_html=page_header_html,
    )


def build_about_page(ch: dict) -> str:
    """About page (Section 1 content) at site root."""
    rendered = _link_figure_table_refs(
        _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=0), depth=0),
        depth=0,
    )
    headings = extract_headings(rendered)
    sidebar_html = make_sidebar(headings, depth=0)

    page_header_html = make_page_header(
        title=ABOUT_TITLE,
        breadcrumbs=[("Home", "index.html"), (ABOUT_TITLE, None)],
    )
    content = f"""
<article class="article-body">
  {rendered}
</article>
<a href="#top" class="back-to-top">Back to top</a>
"""
    return base_html(
        title=ABOUT_TITLE,
        content=content,
        breadcrumbs=[],
        depth=0,
        sidebar_html=sidebar_html,
        top_section_id=top_section_id(headings),
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

    if not ECL_CARDS:
        return _make_card_legacy(ch, label, summary, actual_href, icon_path, icon_color)

    icon_html = (
        f'<div class="chapter-card__icon" aria-hidden="true" style="color:{icon_color}">'
        f'<svg viewBox="0 0 256 256" fill="currentColor"><path d="{icon_path}"/></svg>'
        f'</div>'
    ) if icon_path else ""

    return (
        f'<article class="ecl-card">'
        f'<div class="ecl-card__body">'
        f'{icon_html}'
        f'<div class="ecl-card__meta">{label}</div>'
        f'<div class="ecl-card__title">'
        f'<a href="{actual_href}" class="ecl-link ecl-link--standalone">{h(ch["title"])}</a>'
        f'</div>'
        f'<p class="ecl-card__description">{h(summary)}</p>'
        f'</div>'
        f'</article>'
    )


def _make_card_legacy(ch: dict, label: str, summary: str, actual_href: str,
                       icon_path: str | None, icon_color: str) -> str:
    """ECL_CARDS = False body of _make_card(), kept verbatim for a clean revert."""
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
    if not ECL_CARDS:
        return _make_about_subcard_legacy(ref, title, href, summary, icon_path, icon_color)

    icon_html = (
        f'<div class="chapter-card__icon" aria-hidden="true" style="color:{icon_color}">'
        f'<svg viewBox="0 0 256 256" fill="currentColor"><path d="{icon_path}"/></svg>'
        f'</div>'
    ) if icon_path else ""

    return (
        f'<article class="ecl-card">'
        f'<div class="ecl-card__body">'
        f'{icon_html}'
        f'<div class="ecl-card__meta">Section {h(ref)}</div>'
        f'<div class="ecl-card__title">'
        f'<a href="{h(href)}" class="ecl-link ecl-link--standalone">{h(title)}</a>'
        f'</div>'
        f'<p class="ecl-card__description">{h(summary)}</p>'
        f'</div>'
        f'</article>'
    )


def _make_about_subcard_legacy(ref: str, title: str, href: str, summary: str,
                                icon_path: str, icon_color: str) -> str:
    """ECL_CARDS = False body of _make_about_subcard(), kept verbatim for a clean revert."""
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


def _make_subpage_card(href: str, heading: str, description: str,
                        num_label: str | None = None) -> str:
    """Sub-page hub card used on parent landing pages (Section 3, 4, Annexes).

    This is the second "card type" named by spec section 5.1.1
    (`render_subpage_card()`) -- the single dispatch point for subpage-card
    markup, mirroring `_make_card()` for chapter cards.
    """
    if not ECL_CARDS:
        return _make_subpage_card_legacy(href, heading, description, num_label)

    meta_html = f'<div class="ecl-card__meta">Section {h(num_label)}</div>' if num_label else ""

    return (
        f'<article class="ecl-card">'
        f'<div class="ecl-card__body">'
        f'{meta_html}'
        f'<div class="ecl-card__title">'
        f'<a href="{h(href)}" class="ecl-link ecl-link--standalone">{h(heading)}</a>'
        f'</div>'
        f'<p class="ecl-card__description">{h(description)}</p>'
        f'</div>'
        f'</article>'
    )


def _make_subpage_card_legacy(href: str, heading: str, description: str,
                               num_label: str | None = None) -> str:
    """ECL_CARDS = False body of _make_subpage_card(), kept verbatim for a clean revert."""
    num_html = f'<div class="subpage-card__num">Section {h(num_label)}</div>' if num_label else ""

    return (
        f'<a class="subpage-card" href="{h(href)}">'
        f'{num_html}'
        f'<div class="subpage-card__title">{h(heading)}</div>'
        f'<div class="subpage-card__excerpt">{h(description)}</div>'
        f'</a>'
    )


def _make_cards_grid(card_items: list[str], legacy_grid_class: str) -> str:
    """Wrap already-rendered card fragments in their grid container.

    Single dispatch point for BOTH `.chapter-grid` and `.subpage-grid` ->
    `.ecl-row` conversions (spec 5.1.1 / 6.5). Under ECL_CARDS=True each card
    gets an `.ecl-col-s-6 .ecl-col-l-4` wrapper inside `.ecl-row`; under
    ECL_CARDS=False cards are direct children of the legacy CSS-grid class,
    unchanged.
    """
    if not ECL_CARDS:
        return f'<div class="{legacy_grid_class}">{"".join(card_items)}</div>'

    cols = "".join(
        f'<div class="ecl-col-s-6 ecl-col-l-4">{card}</div>' for card in card_items
    )
    return f'<div class="ecl-row">{cols}</div>'


def build_index_page(nav_sections: list[dict], summaries: dict,
                     about_ch: dict | None = None) -> str:
    """Homepage: hero + three grouped card sections (About / Reference Guide / Annexes)."""

    # Sections 2–10 go to Reference Guide; section 11+ go to Annexes
    guide_sections = [c for c in nav_sections if 0 < c["section_number"] <= 10]
    annex_sections = [c for c in nav_sections if c["section_number"] == 0 or c["section_number"] > 10]

    about_cards  = (
        [
            _make_about_subcard(
                "1.1", "Overview", "about.html#11-overview",
                "The EU's implementation of CITES, the Regulations currently in force, "
                "and the EU action plan against wildlife trafficking.",
                icon_path=_ICON_COMPASS_PATH, icon_color="#696984",  # grey-600 -- compass
            ),
            _make_about_subcard(
                "1.2", "How do I use this guide?", "about.html#12-how-do-i-use-this-guide",
                "Who this guide is for, how its sections and annexes are organised, "
                "and tips for finding what you need.",
                icon_path=_CARD_ICON_PATHS[1], icon_color=_CARD_ICON_COLORS[1],  # book-open
            ),
        ]
    ) if about_ch else []
    guide_cards  = [_make_card(c, summaries) for c in guide_sections]
    annex_cards  = [_make_card(c, summaries) for c in annex_sections]

    def section_block(heading: str, cards: list[str]) -> str:
        return (
            f'<h2 style="margin-top:0;border-top:none;padding-top:0">{heading}</h2>'
            f'{_make_cards_grid(cards, "chapter-grid")}'
        )

    # Only called after generate_pdf(), so the size is known on a full build.
    size = _pdf_size_label()
    pdf_meta = f"PDF, {size}" if size else "PDF"
    # ECL File component (no translations, so no File JS needed).
    download_block = f"""
<h2 style="margin-top:0;border-top:none;padding-top:0">Download the full guide</h2>
<div class="ecl-file home-download">
  <div class="ecl-file__container">
    <div class="ecl-file__info">
      <div class="ecl-file__icon-box">{_ecl_icon("file", "ecl-file__icon home-download__icon")}</div>
      <div class="ecl-file__content">
        <div class="ecl-file__title">EU Wildlife Trade Regulations Reference Guide</div>
        <div class="ecl-file__description">The complete guide, including all annexes, as a single PDF for offline reading and printing.</div>
      </div>
    </div>
    <div class="ecl-file__footer">
      <div class="ecl-file__language">English</div>
      <div class="ecl-file__action">
        <div class="ecl-file__meta">({pdf_meta})</div>
        <a href="{PDF_FILENAME}" class="ecl-link ecl-link--standalone ecl-link--icon ecl-file__download"><span class="ecl-link__label">Download</span>{_ecl_icon("download", "ecl-icon--s ecl-link__icon")}</a>
      </div>
    </div>
  </div>
</div>
"""

    hero = f"""
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
    <p class="hero__download">or <a href="{PDF_FILENAME}">download the full guide as a PDF</a>{f" ({size})" if size else ""}</p>
  </div>
</div>
"""
    content = f"""
<div class="home-sections" style="padding-top:30px">
  {section_block(ABOUT_TITLE, about_cards) if about_cards else ""}
  {"<hr>" if about_cards else ""}
  {section_block("Reference Guide", guide_cards)}
  <hr>
  {section_block("Annexes", annex_cards) if annex_cards else ""}
  <hr>
  {download_block}
</div>
"""
    return base_html(
        title="Home",
        content=content,
        breadcrumbs=[],
        depth=0,
        hero_html=hero,
    )


def build_search_page() -> str:
    content = """
<h1>Search</h1>
<div class="search-header">
  <form class="search-form" action="search.html" method="get"
        role="search" style="margin-bottom:16px">
    <label for="search-input" class="skip-link">Search</label>
    <input type="search" id="search-input" name="q"
           placeholder="Search the guide&hellip;" aria-label="Search the guide"
           aria-describedby="search-tips" autocomplete="off" autofocus>
    <button type="submit">
      <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M13.6 12.2h-.7l-.3-.3c1-1.1 1.6-2.6 1.6-4.2C14.2 4.1 11.6 1.5 8.1 1.5S2 4.1 2 7.6s2.6 6.1 6.1 6.1c1.6 0 3.1-.6 4.2-1.6l.3.3v.7l4.4 4.4 1.3-1.3-4.4-4.4zm-5.5 0c-2.5 0-4.5-2-4.5-4.5s2-4.5 4.5-4.5 4.5 2 4.5 4.5-2 4.5-4.5 4.5z" fill="currentColor"/></svg>
      <span class="sr-only">Search</span>
    </button>
  </form>
  <p class="search-tips" id="search-tips">Finds pages containing all of your words, in any order.
    Put words in quotes for an exact phrase, e.g. "personal effects".</p>
  <p class="search-count" id="search-count" aria-live="polite"></p>
</div>
<div id="search-results"></div>
<noscript><p>JavaScript is required for search.</p></noscript>
"""
    return base_html(
        title="Search",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Search", None)],
        depth=0,
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
    page = base_html(
        title="Page not found",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Page not found", None)],
        depth=0,
    )
    # GitHub Pages serves 404.html at whatever URL missed (e.g. /<repo>/chapters/
    # old.html), so the page's root-relative-to-itself links and assets would
    # resolve one folder too deep. Pin a <base> at the site root before anything
    # loads, and keep in-page "#…" links pointing at this page, not the root.
    base_script = """<script>
    (function () {
      var seg = location.pathname.split('/');
      var root = /\\.github\\.io$/.test(location.hostname) && seg.length > 2 ? '/' + seg[1] + '/' : '/';
      document.write('<base href="' + root + '">');
      document.addEventListener('DOMContentLoaded', function () {
        var here = location.href.split('#')[0];
        document.querySelectorAll('a[href^="#"]').forEach(function (a) {
          a.setAttribute('href', here + a.getAttribute('href'));
        });
      });
    })();
  </script>"""
    return page.replace("<head>", "<head>\n  " + base_script, 1)


# ==============================================================================
# SECTION 8 -- Search index builder
# ==============================================================================

# Headings the page sidebar also links to (see extract_headings) -- each one
# starts a new search section, so a result can deep-link to its anchor.
_SEARCH_HEADING_RE = re.compile(
    r'<(h[234])\b[^>]*\bid="([^"]*)"[^>]*>(.*?)</\1>',
    re.DOTALL | re.IGNORECASE,
)
# Markup whose text is not reading content: footnote reference numbers, the
# footnote "jump back" arrows, and any inline script/style.
_SEARCH_DROP_RE = re.compile(
    r'<sup class="footnote-ref".*?</sup>'
    r'|<a [^>]*class="footnoteBackLink"[^>]*>.*?</a>'
    r'|<(script|style)\b.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)


def _html_to_search_text(fragment: str) -> str:
    """Rendered HTML -> plain reading text (tags become spaces so table cells
    and list items don't run together)."""
    s = _SEARCH_DROP_RE.sub(" ", fragment)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _search_sections(html: str) -> list[dict]:
    """Split a rendered page at its h2-h4 headings: one entry per heading
    (id = its anchor, h = heading text, l = level, x = text up to the next
    heading), plus the untitled intro before the first heading (id "")."""
    sections = []
    cur = {"id": "", "h": "", "l": 1}
    pos = 0
    for m in _SEARCH_HEADING_RE.finditer(html):
        cur["x"] = _html_to_search_text(html[pos:m.start()])
        sections.append(cur)
        cur = {"id": m.group(2), "h": _html_to_search_text(m.group(3)), "l": int(m.group(1)[1])}
        pos = m.end()
    cur["x"] = _html_to_search_text(html[pos:])
    sections.append(cur)
    return [s for s in sections if s["h"] or s["x"]]


def build_search_index(all_pages: list[dict], summaries: dict,
                       by_slug: dict[str, dict], about_ch: dict | None) -> list[dict]:
    """One entry per page, each split into heading-level sections. Text comes
    from the same render pipeline as the pages themselves, so anchors match
    the live heading ids and no markup leaks into results."""
    def page_html(ch: dict, depth: int) -> str:
        if ch["sub_pages"]:   # parent landing page: only its intro is page content
            body = _landing_intro_body(ch["body"])
            return render_markdown(body) if body else ""
        return _link_figure_table_refs(
            _replace_figures(autolink_xrefs(render_markdown(ch["body"]), depth=depth), depth=depth),
            depth=depth,
        )

    entries = []
    if about_ch:
        entries.append({
            "url": "about.html", "title": ABOUT_TITLE, "label": "Section 1", "parent": "",
            "summary": summaries.get(about_ch["slug"], ""),
            "sections": _search_sections(page_html(about_ch, 0)),
        })
    for ch in all_pages:
        parent = by_slug.get(ch["parent"]) if ch["parent"] else None
        entries.append({
            "url": f"chapters/{ch['slug']}.html",
            "title": ch["title"],
            "label": _section_label_text(ch),
            "parent": parent["title"] if parent else "",
            "summary": summaries.get(ch["slug"], ""),
            "sections": _search_sections(page_html(ch, 1)),
        })
    return entries


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
  margin: 30mm 20mm 20mm 20mm;
  @top-left {
    content: url(assets/images/logo-ec-header.svg);
  }
  @top-right {
    content: string(chapter);
    width: 130mm;
    text-align: right;
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
  @top-left { content: ""; }
  @top-right { content: ""; }
  @bottom-center { content: ""; }
}
/* A handful of figures (annotated CITES forms) are close to full-page
   images. Rather than shrink those images, the page they land on gets
   slightly smaller margins so the caption fits alongside the image
   instead of being pushed onto its own page before it. The reduced-margin
   page is reset back to normal by _pdf_tall_figures_css()'s own "+ *"
   sibling rule right after the figure, plus a fallback `page: normal` on
   each chapter/subchapter's own leading <h1> (see below) in case the
   figure is the very last thing in its subchapter. That reset is scoped to
   just those two spots deliberately: WeasyPrint won't collapse an
   element's top margin into a natural (non-forced) page break once that
   element's own `page` value resolves to anything other than `auto` --
   setting `page: normal` on *every* .pdf-chapter/.pdf-subchapter (so it
   inherited into every heading inside them) used to do exactly that,
   leaving a stray gap above any X.X.X/X.X.X.X subheading that happened to
   land at the top of a page. */
@page figure-page {
  size: A4;
  margin: 22mm 20mm 12mm 20mm;
  @top-left {
    content: url(assets/images/logo-ec-header.svg);
  }
  @top-right {
    content: string(chapter);
    width: 130mm;
    text-align: right;
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
/* Same idea as figure-page, but for large tables that don't fit as one
   block under normal margins -- see _pdf_tall_tables_css(). Cut further
   than figure-page's margins: a text table has no fixed aspect ratio to
   stay visually balanced against (unlike a photo/scan), so there's more
   room to give before it looks cramped, and that extra room is exactly
   what lets borderline tables (a table whose rows fit but whose one-line
   Source/Note was landing on the next page) become a single block. */
@page table-page {
  size: A4;
  margin: 15mm 20mm 8mm 20mm;
  @top-left {
    content: url(assets/images/logo-ec-header.svg);
  }
  @top-right {
    content: string(chapter);
    width: 130mm;
    text-align: right;
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

* { box-sizing: border-box; }

body {
  font-family: "Inter", Arial, sans-serif;
  font-size: 9.5pt;
  line-height: 1.5;
  color: #00002e;
}

/* Heading sizes follow the guide's numbering DEPTH (X, X.X, X.X.X, X.X.X.X),
   not the raw HTML tag. A sub-page's own <h1> wrapper (e.g. "3.3") sits at
   the X.X level, so everything inside its body -- native h2/h3/h4 from its
   own markdown, e.g. "3.3.1", "3.3.9.1", "Step 1..." -- is one level deeper
   than the same tag means in a top-level chapter's own body (Section 2's
   native h2/h3, "2.1"/"2.2.1"), and has to be sized down a step to match.
   Without this, e.g. "3.3" (a wrapper h1) and "3.3.1" (a native h2 inside
   it) both render at h2 size and read as the same level. */
h1, h2, h3, h4 { color: #00002e; page-break-after: avoid; }
h1 { font-size: 20pt; margin: 0 0 10mm; }   /* X       */
h2 { font-size: 14pt; margin: 10mm 0 4mm; } /* X.X     */
h3 { font-size: 12pt; margin: 8mm 0 3mm; }  /* X.X.X   */
h4 { font-size: 11pt; margin: 6mm 0 3mm; }  /* X.X.X.X */

.pdf-subchapter > h1 { font-size: 14pt; margin: 10mm 0 4mm; }    /* X.X       */
.pdf-subchapter h2   { font-size: 12pt; margin: 8mm 0 3mm; }     /* X.X.X     */
.pdf-subchapter h3   { font-size: 11pt; margin: 6mm 0 3mm; }     /* X.X.X.X   */
.pdf-subchapter h4   { font-size: 10.25pt; margin: 5mm 0 2mm; }  /* X.X.X.X.X */
p, li { orphans: 3; widows: 3; margin: 0 0 3mm; }
a { color: #0046ff; text-decoration: none; }

.pdf-cover { text-align: center; margin-top: 40mm; }
.pdf-cover__logo { width: 55mm; margin-bottom: 25mm; }
.pdf-cover__kicker {
  text-transform: uppercase; letter-spacing: 0.08em;
  font-size: 10pt; color: #696984; margin-bottom: 12mm;
}
.pdf-cover h1 { font-size: 30pt; margin-bottom: 4mm; }
.pdf-cover__subtitle { font-size: 16pt; color: #696984; margin-bottom: 20mm; }
.pdf-cover__date { font-size: 10pt; color: #696984; }

.pdf-toc { break-before: page; break-after: page; }
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

.pdf-chapter { break-before: page; counter-reset: footnote; }
.pdf-chapter > h1 { page: normal; }
.pdf-subchapter { break-before: auto; }
/* Each Annex is a standalone document in its own right, so it starts on a
   fresh page like a top-level chapter. Its h1 keeps the X.X size (it's still
   listed under "Annexes" in the contents) but drops the top margin, which a
   forced break would otherwise preserve as a gap at the top of the page. */
.pdf-subchapter[id^="pdf-annexes_"] { break-before: page; }
.pdf-subchapter[id^="pdf-annexes_"] > h1 { margin-top: 0; }
/* A heading (or a bold one-line pseudo-heading like Annex VII's "FLORA") that
   introduces a table -- marked by _pdf_mark_heading_tables(). break-after
   alone isn't enough to keep it with the table (see that function), but it's
   the right default for the cases that don't need _pdf_heading_tables_css(). */
[data-pdf-lead], [data-pdf-lead-mid] { break-after: avoid; }

.pdf-footnote { float: footnote; font-size: 7.5pt; line-height: 1.35; }
::footnote-marker { content: counter(footnote) ". "; font-weight: 600; }
::footnote-call {
  content: counter(footnote);
  vertical-align: super;
  font-size: 0.7em;
  line-height: 0;
}
@footnote {
  border-top: 0.5pt solid #d4d4dc;
  padding-top: 2.5mm;
  margin-top: 8mm;
  font-size: 7.5pt;
  line-height: 1.35;
  color: #505a5f;
}

table { border-collapse: collapse; width: 100%; margin: 4mm 0 6mm; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 0.5pt solid #d4d4dc; padding: 2mm 3mm; text-align: left; vertical-align: top; }
th { background: #ededf0; font-weight: 600; }
tr:nth-child(even) td { background: #f6f6f8; }
/* Annex VII's taxonomy table: under auto layout its long citations and
   URLs set a minimum width wider than the page, pushing the reference
   column off the right edge. Fix the column widths instead, at a slightly
   smaller size so the longest Order/Family names still fit on one line. */
table.table--xwide { table-layout: fixed; font-size: 8pt; }
table.table--xwide th, table.table--xwide td { padding: 1.5mm 2mm; overflow-wrap: break-word; }
table.table--xwide col:nth-child(1) { width: 21% !important; }
table.table--xwide col:nth-child(2) { width: 17% !important; }
table.table--xwide col:nth-child(3) { width: 21% !important; }
table.table--xwide col:nth-child(4) { width: 41% !important; }
td a { overflow-wrap: anywhere; }
td p { margin: 0 0 1.5mm; }
td ul { margin: 0 0 1.5mm; padding-left: 4mm; }
td > :last-child { margin-bottom: 0; }
td.cell-center { text-align: center; }
/* A couple of table headers (Table 13's "Annex"/"Article" columns) use
   inline writing-mode:vertical-lr + rotate(180deg) to read sideways,
   bottom-to-top -- a combination browsers render correctly (confirmed on
   the site) but WeasyPrint renders upside-down. Plain rotation without
   writing-mode gives the same bottom-to-top reading and renders correctly
   in WeasyPrint, so override just for the PDF; !important is needed to
   beat the inline style itself, the most specific thing in the cascade. */
span[style*="writing-mode:vertical-lr"] {
  writing-mode: horizontal-tb !important;
  transform: rotate(-90deg) !important;
}
th:has(span[style*="writing-mode:vertical-lr"]) { vertical-align: middle !important; }
/* A table's trailing Source:/Note: text is relocated out of <caption> and
   into this paragraph by _pdf_relocate_table_captions() -- see that
   function for why (WeasyPrint doesn't keep a bottom <caption> reliably
   bound to its table across a page break, so it's treated as a plain
   paragraph pinned to the table above it instead). */
.table-source {
  break-before: avoid;
  font-size: 8pt;
  color: #696984;
}

.figure-block { margin: 6mm 0; page-break-inside: avoid; text-align: center; }
.figure-block img { max-width: 100%; }
.figure-block figcaption { font-size: 9pt; color: #696984; margin-top: 2mm; }

.summary-smallprint { font-size: 9pt; color: #696984; border-left: 2pt solid #d4d4dc; padding-left: 4mm; }
.context-box {
  background: #f6f6f8; border: 0.5pt solid #d4d4dc; border-radius: 1mm;
  padding: 3mm 4mm; margin: 4mm 0; page-break-inside: avoid;
}
.context-box__label {
  font-weight: 700; text-transform: uppercase; font-size: 7.5pt;
  letter-spacing: .05em; color: #696984; margin-bottom: 2mm;
}
ol.lettered-list { padding-left: 6mm; }
/* A Figure/Table caption is a separate sibling from the figure/table it
   labels (not a nested figcaption), so without this a caption can end up
   as the last line on one page while its figure/table starts the next. */
.table-label { font-weight: 600; margin-top: 4mm; break-after: avoid; }
"""
# Figures whose rendered height (at the content width) leaves no room for
# even a one-line caption above them get `page: figure-page` -- see
# _pdf_tall_figures_css(). Applying that to every figure indiscriminately
# would also force an unwanted page break before every *small* figure too,
# since switching the named page is itself an implicit break point.

# One pattern for both kinds of internal link -- a cross-page site link
# ("chapters/<slug>.html#frag", group 1/2) and a same-page "#frag" (group 3) --
# so each href is rewritten exactly once.
_PDF_HREF_RE = re.compile(r'href="(?:chapters/([a-zA-Z0-9_\-]+)\.html(?:#([^"]*))?|#([^"]*))"')
_PDF_ID_RE           = re.compile(r'\bid="([^"]*)"')

# A repeat citation's id carries a "-<n>" suffix (see _postprocess_footnote_refs).
_PDF_FN_REF_RE     = re.compile(r'<sup class="footnote-ref" id="fnref-(\d+)(?:-\d+)?"><a href="#fn-\d+">\d+</a></sup>')
_PDF_FN_BLOCK_RE   = re.compile(r'<div class="footnotes">.*?</div>\s*', re.DOTALL)
_PDF_FN_LI_RE      = re.compile(r'<li id="fn-(\d+)">(.*?)</li>', re.DOTALL)
_PDF_FN_BACKLINK_RE = re.compile(r'\s*(?:&#160;|&nbsp;)?\s*<a[^>]*class="footnoteBackLink"[^>]*>.*?</a>\s*')
_PDF_FN_POPEN_RE   = re.compile(r'<p[^>]*>')
_PDF_FN_PCLOSE_RE  = re.compile(r'</p>\s*')
_PDF_FN_TRAIL_BR_RE = re.compile(r'(?:<br\s*/?>\s*)+$')


def _pdf_relocate_footnotes(html: str) -> str:
    """Move markdown2's end-of-section footnote list back inline as CSS
    `float: footnote` spans, so each note prints at the bottom of the physical
    page it's cited on instead of the end of the (possibly much longer) section."""
    block_m = _PDF_FN_BLOCK_RE.search(html)
    if not block_m:
        return html

    notes: dict[str, str] = {}
    for li_m in _PDF_FN_LI_RE.finditer(block_m.group(0)):
        num, content = li_m.group(1), li_m.group(2)
        content = _PDF_FN_BACKLINK_RE.sub("", content)
        # A float:footnote span is inline -- a nested <p> would close the
        # *outer* <p> it's substituted into (block content isn't valid inside
        # a <span>), silently breaking the float and leaking the note into
        # normal flow. Flatten to inline content, keeping <br> between any
        # multi-paragraph notes.
        content = _PDF_FN_POPEN_RE.sub("", content)
        content = _PDF_FN_PCLOSE_RE.sub("<br>", content)
        content = _PDF_FN_TRAIL_BR_RE.sub("", content)
        notes[num] = content.strip()

    def _sub_ref(m: re.Match) -> str:
        content = notes.get(m.group(1))
        if content is None:
            return m.group(0)
        return f'<span class="pdf-footnote">{content}</span>'

    html = _PDF_FN_REF_RE.sub(_sub_ref, html)
    html = _PDF_FN_BLOCK_RE.sub("", html, count=1)
    return html


_PDF_TABLE_CAPTION_RE = re.compile(
    r'(<div class="table-wrap">\s*<table[^>]*>)\s*<caption>(.*?)</caption>(.*?</table>\s*</div>)',
    re.DOTALL,
)


def _pdf_relocate_table_captions(html: str) -> str:
    """Move a table's trailing Source:/Note: text out of its <caption> and
    into a plain paragraph straight after the table, with break-before:avoid
    binding it to the table above. `caption-side: bottom` alone isn't enough
    here: WeasyPrint's table fragmentation doesn't treat a bottom caption as
    protected by the table's own `page-break-inside: avoid` the way rows
    are, so on a tall table the caption alone could still be pushed onto the
    following page while every row stayed on the page before it."""
    def _sub(m: re.Match) -> str:
        open_tag, caption, rest = m.group(1), m.group(2), m.group(3)
        return f'{open_tag}{rest}<p class="table-source">{caption}</p>'

    return _PDF_TABLE_CAPTION_RE.sub(_sub, html)


_PDF_TABLE_GROUP_RE = re.compile(
    # `.table-label` is shared with figure captions (see _postprocess_table_labels),
    # and a figure caption is never immediately followed by a table-wrap --
    # `(?:(?!</p>).)*` stops the label group at its OWN first </p> instead of
    # `.*?`, which would happily backtrack past an unrelated figure (and any
    # other content) in search of the next table-wrap anywhere later in the
    # document, silently pairing the wrong label with a wrap.
    r'(<p class="table-label" id="([^"]+)">(?:(?!</p>).)*</p>\s*)'
    # A handful of complex tables (rowspan/vertical headers) are hand-authored
    # as raw HTML in the markdown source, e.g. <table class="govuk-table">
    # with real newlines/indentation around the tags, rather than produced by
    # _postprocess_tables() -- match any attributes on <table> (not just the
    # bare tag that function emits) and allow whitespace around the tags.
    r'(<div class="table-wrap">\s*<table[^>]*>.*?</table>\s*</div>)'
    r'(\s*<p class="table-source">.*?</p>)?',
    re.DOTALL,
)


def _pdf_mark_table_bounds(html: str) -> str:
    """Tag a table's wrapper div, its first row, and its source note (if
    any) with ids derived from the table's own label id, so
    _pdf_tall_tables_css() can later look up which physical page each part
    of the group landed on. Purely a measurement aid -- these ids don't
    affect rendering.

    The first row gets its own id specifically because the wrapper div's
    anchor isn't reliable for detecting a split: when `page-break-inside:
    avoid` on the <table> defers every row to the next page, WeasyPrint can
    still register the (now-empty-on-this-page) div's own box -- and thus
    its anchor -- on the *earlier* page, making the label and "body" look
    like they're on the same page even though every visible row moved. The
    first row's anchor always reflects where a row actually renders."""
    def _sub(m: re.Match) -> str:
        label_prefix, label_id, wrap, source = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        wrap = wrap.replace(
            '<div class="table-wrap">', f'<div class="table-wrap" id="{label_id}--body">', 1
        )
        wrap = re.sub(r'<tr(?![^>]*\bid=)', f'<tr id="{label_id}--row1"', wrap, count=1)
        if source:
            source = source.replace(
                '<p class="table-source">', f'<p class="table-source" id="{label_id}--src">', 1
            )
        return f'{label_prefix}{wrap}{source}'

    return _PDF_TABLE_GROUP_RE.sub(_sub, html)


def _css_str(text: str) -> str:
    """Escape a string for use inside a single-quoted CSS string literal."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _pdf_namespace_refs(html: str, slug: str) -> str:
    """Namespace ids/hrefs so this page's anchors stay unique once merged into
    one PDF document, and turn cross-page site links into in-document jumps."""

    def _href(m: re.Match) -> str:
        target_slug, target_frag, local_frag = m.groups()
        if target_slug is None:
            return f'href="#pdf-{slug}--{local_frag}"'
        anchor = f"--{target_frag}" if target_frag else ""
        return f'href="#pdf-{target_slug}{anchor}"'

    html = _PDF_HREF_RE.sub(_href, html)
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
    rendered = _pdf_relocate_footnotes(rendered)
    rendered = _pdf_relocate_table_captions(rendered)
    rendered = _pdf_mark_table_bounds(rendered)
    rendered = _pdf_namespace_refs(rendered, ch["slug"])
    return _wrap_pdf_section(ch, rendered, top_level=top_level)


def _prepare_pdf_parent_chunk(ch: dict) -> str:
    """Intro-only page for a parent section (3, 4, Annexes) -- its sub-pages
    follow immediately after as their own chunks, so no card grid is needed."""
    body = _landing_intro_body(ch["body"])
    intro_html = (
        _pdf_namespace_refs(
            _pdf_mark_table_bounds(
                _pdf_relocate_table_captions(_pdf_relocate_footnotes(render_markdown(body)))
            ),
            ch["slug"],
        )
        if body else ""
    )
    return _wrap_pdf_section(ch, intro_html, top_level=True)


def build_pdf_toc(
    about_ch: dict | None,
    guide_sections: list[dict],
    annex_sections: list[dict],
    by_slug: dict,
) -> str:
    def _entry(ch: dict, sub_chs: list[dict] | None = None, *, ref_override: str | None = None) -> str:
        ref = ref_override if ref_override is not None else _section_ref_number(ch)
        label = f"{ref} {ch['title']}" if ref else ch["title"]
        li = f'<li><a href="#pdf-{h(ch["slug"])}">{h(label)}</a>'
        if sub_chs:
            li += f'<ol>{"".join(_entry(sub) for sub in sub_chs)}</ol>'
        return li + "</li>"

    # About (section_number 1) gets no ref elsewhere (its own heading and running
    # header stay plain "About", matching the site) -- "1" here is purely for the
    # contents list to read consistently alongside 2, 3, 4...
    items = _entry(about_ch, ref_override="1") if about_ch else ""
    for ch in guide_sections + annex_sections:
        sub_chs = [by_slug[s] for s in (ch.get("sub_pages") or []) if s in by_slug]
        items += _entry(ch, sub_chs)

    return f'<nav class="pdf-toc"><h1>Contents</h1><ol>{items}</ol></nav>'


PDF_FILENAME = "EU-Wildlife-Trade-Reference-Guide.pdf"


def _pdf_size_label() -> str | None:
    """"6.7 MB" for the built PDF, or None when it hasn't been generated
    (WeasyPrint unavailable) -- EC practice is to state a download's size."""
    path = SITE_DIR / PDF_FILENAME
    if not path.exists():
        return None
    return f"{path.stat().st_size / (1024 * 1024):.1f} MB"


_LOGO_SVG_DIM_RE = re.compile(r'width="(\d+(?:\.\d+)?)"\s*\n?\s*height="(\d+(?:\.\d+)?)"')


def _make_pdf_header_logo(target_width_mm: float = 20.0) -> None:
    """Write a copy of the EC logo with a fixed mm width/height baked into the SVG
    file itself. WeasyPrint's page-margin-box `content: url()` ignores CSS
    width/height set on the margin box and just uses the image's own intrinsic
    size, so -- unlike the cover logo, a normal in-flow <img> where CSS sizing
    works fine -- the running-header logo has to be pre-sized on disk."""
    src = SITE_DIR / "assets" / "images" / "logo-ec-positive.svg"
    if not src.exists():
        return
    content = src.read_text(encoding="utf-8")
    m = _LOGO_SVG_DIM_RE.search(content)
    if not m:
        return
    w, h = float(m.group(1)), float(m.group(2))
    target_height_mm = target_width_mm * h / w
    resized = _LOGO_SVG_DIM_RE.sub(
        f'width="{target_width_mm}mm" height="{target_height_mm:.2f}mm"', content, count=1,
    )
    (SITE_DIR / "assets" / "images" / "logo-ec-header.svg").write_text(resized, encoding="utf-8")


# PDF content width = A4 width (210mm) minus the 20mm left/right margins.
_PDF_CONTENT_WIDTH_MM = 170.0
# A figure needs the reduced-margin page if its own rendered height leaves
# less than ~12mm for the caption above it on a normal-margin page
# (297mm - 30mm top - 20mm bottom = 247mm available).
_PDF_TALL_FIGURE_THRESHOLD_MM = 247.0 - 12.0


def _pdf_tall_figures_css() -> str:
    """CSS assigning `page: figure-page` to exactly the figures whose PNG,
    scaled to the content width, is too tall to leave room for its caption
    on a normal page -- computed from each image's real pixel dimensions,
    not guessed, so this stays correct as source images change."""
    from PIL import Image

    tall_nums: list[str] = []
    for path in sorted(Path("images").glob("Figure-*.png")):
        num = path.stem.split("-", 1)[1]
        with Image.open(path) as img:
            w, h = img.size
        rendered_height_mm = _PDF_CONTENT_WIDTH_MM * h / w
        if rendered_height_mm > _PDF_TALL_FIGURE_THRESHOLD_MM:
            tall_nums.append(num)

    if not tall_nums:
        return ""
    # $= (ends-with), not *= (contains) -- "--figure-1" is a substring of
    # "--figure-11", so a contains-match would wrongly also select figures
    # 11-19 whenever figure 1 is in the tall list. The caption paragraph and
    # the <figure> share the same id, so this rule and the reset below both
    # end up targeting the <figure> element twice -- harmless, since the
    # more specific `.figure-block[id$=...]` declaration always wins.
    rules: list[str] = []
    for n in tall_nums:
        rules.append(
            f'.table-label[id$="--figure-{n}"], .figure-block[id$="--figure-{n}"] '
            f"{{ page: figure-page; }}"
        )
        # Resets whatever immediately follows this figure back to normal
        # margins, so figure-page doesn't leak into the rest of the
        # subchapter. Doesn't help if the figure is the *last* thing in its
        # subchapter (nothing follows it to reset) -- the fallback
        # `page: normal` on each chapter/subchapter's own <h1> (see the CSS
        # above) covers that case at the next section boundary.
        rules.append(f'.figure-block[id$="--figure-{n}"] + * {{ page: normal; }}')
    return "\n".join(rules)


_PDF_TABLE_LABEL_ID_RE = re.compile(r'<p class="table-label" id="([^"]+)">')


def _pdf_anchor_pages(doc_html: str, css: str, base_url: str) -> dict[str, int]:
    """Lay the document out (no PDF written) and return the 0-based page
    index each element id first appears on."""
    import weasyprint

    document = weasyprint.HTML(string=doc_html, base_url=base_url).render(
        stylesheets=[weasyprint.CSS(string=css, base_url=base_url)]
    )
    pages: dict[str, int] = {}
    for i, page in enumerate(document.pages):
        for anchor_id in page.anchors:
            pages.setdefault(anchor_id, i)
    return pages


def _pdf_tall_tables_css(doc_html: str, base_css: str, base_url: str) -> str:
    """CSS assigning `page: table-page` (a reduced-margin page, see the CSS
    definition above) to exactly the tables whose label, body, or trailing
    source note land on different physical pages under normal margins --
    i.e. WeasyPrint has started splitting the group apart (orphaned label,
    or the source note pushed onto the following page) -- AND where the
    extra room actually fixes it.

    Row height depends on how much each cell's text wraps, so unlike figures
    (a fixed-size image) there's no way to compute a table's rendered height
    up front -- this renders the document (a layout pass only, no PDF
    written) and reads real per-anchor page numbers off the result, via the
    marker ids _pdf_mark_table_bounds() adds to every table's wrapper/source
    elements, to find out which tables need the extra room.

    A genuinely too-tall table (more rows than fit on any single page, even
    with table-page's reduced margins) would still split even after forcing
    it onto its own page -- so candidates are re-checked after tentatively
    applying the fix, and page:table-page is only kept where it verifiably
    worked. For the ones it doesn't fix, forcing the page-name switch
    anyway would strand the label alone at the top of an otherwise-blank
    page (see the comment below), so those get a different, targeted fix
    instead: dropping page-break-inside:avoid for just that one table."""
    def _anchor_pages(css: str) -> dict[str, int]:
        return _pdf_anchor_pages(doc_html, css, base_url)

    def _is_split(pages: dict[str, int], label_id: str, row1_id: str, src_id: str, has_src: bool) -> bool:
        # Compares the label against the first *row*, not the wrapper div --
        # see _pdf_mark_table_bounds() for why the div's own anchor can't be
        # trusted here.
        group_pages = {pages.get(label_id), pages.get(row1_id)}
        if has_src:
            group_pages.add(pages.get(src_id))
        return len(group_pages) > 1

    def _rules_for(label_id: str, body_id: str, src_id: str, has_src: bool) -> list[str]:
        group_ids = [label_id, body_id] + ([src_id] if has_src else [])
        selectors = ", ".join(f'[id="{i}"]' for i in group_ids)
        last_id = src_id if has_src else body_id
        return [
            f"{selectors} {{ page: table-page; }}",
            # Without this, everything after the table would keep rendering
            # on table-page's reduced margins too, all the way to the next
            # .pdf-chapter/.pdf-subchapter boundary (those are the only
            # other places the page name gets reset back to normal).
            f'[id="{last_id}"] + * {{ page: normal; }}',
        ]

    groups: dict[str, tuple[str, str, str, bool]] = {}
    for m in _PDF_TABLE_LABEL_ID_RE.finditer(doc_html):
        label_id = m.group(1)
        if "--table-" not in label_id:
            continue
        body_id = f"{label_id}--body"
        row1_id = f"{label_id}--row1"
        src_id = f"{label_id}--src"
        has_src = f'id="{src_id}"' in doc_html
        groups[label_id] = (body_id, row1_id, src_id, has_src)

    base_pages = _anchor_pages(base_css)
    candidates = {
        label_id: bounds for label_id, bounds in groups.items()
        if _is_split(base_pages, label_id, bounds[1], bounds[2], bounds[3])
    }
    if not candidates:
        return ""

    candidate_css = "\n".join(
        rule
        for label_id, (body_id, row1_id, src_id, has_src) in candidates.items()
        for rule in _rules_for(label_id, body_id, src_id, has_src)
    )
    fixed_pages = _anchor_pages(base_css + "\n" + candidate_css)

    kept_rules: list[str] = []
    for label_id, (body_id, row1_id, src_id, has_src) in candidates.items():
        if _is_split(fixed_pages, label_id, row1_id, src_id, has_src):
            # More room didn't fix it -- the table has more rows than fit on
            # any single page, even with table-page's reduced margins.
            # `table { page-break-inside: avoid }` (in the main PDF_CSS
            # block) can never be satisfied for a table like this, and
            # WeasyPrint's fallback when "avoid" can't be satisfied is to
            # defer the *whole table* to a fresh page and try again there --
            # which still doesn't fit, so it gives up and breaks inside on
            # that page, leaving the page it deferred *from* almost
            # entirely blank under just the stranded label. Dropping the
            # avoid for this one table lets it flow (and break across
            # pages) immediately after its label instead, exactly like a
            # normal multi-page table -- no wasted page, no orphaned label.
            kept_rules.append(f'[id="{body_id}"] table {{ page-break-inside: auto; break-inside: auto; }}')
            continue
        kept_rules.extend(_rules_for(label_id, body_id, src_id, has_src))

    return "\n".join(kept_rules)


_PDF_LEAD_RE = re.compile(
    # The heading: a real h2-h6, or a bold one-line paragraph acting as one
    # (Annex VII's "FLORA"/"FAUNA").
    r'(?P<lead><h(?P<lvl>[2-6])\b[^>]*>(?:(?!</h(?P=lvl)>).)*</h(?P=lvl)>'
    r'|<p><strong>[^<]{1,80}</strong></p>)'
    # Optionally one short intro paragraph between them (Annex XIII), and/or
    # the table's own "Table N" label.
    r'(?P<mid>\s*(?:<p>(?:(?!</p>).){0,400}</p>\s*)?'
    r'(?:<p class="table-label"[^>]*>(?:(?!</p>).)*</p>\s*)?)'
    r'(?P<wrap><div class="table-wrap"[^>]*>\s*<table)',
    re.DOTALL,
)
_PDF_OPEN_TAG_RE = re.compile(r'<(h[2-6]|p)\b([^>]*)>')
_PDF_TR_RE = re.compile(r'<tr\b([^>]*)>')


def _pdf_mark_heading_tables(doc_html: str) -> tuple[str, list[dict]]:
    """Tag every heading that directly introduces a table, so
    _pdf_heading_tables_css() can check where each one lands.

    `break-after: avoid` on the heading is not enough on its own: with
    `page-break-inside: avoid` on the table, WeasyPrint moves a table that
    doesn't fit the rest of the page to the next page *without* taking the
    heading along, leaving it stranded at the bottom of the page before
    (e.g. Annex XII's "2. Import and export permit criteria").

    Returns the tagged HTML plus one record per heading: the heading's
    anchor id, the table's first-row anchor id, and whether the heading is
    the first thing in its section (where a page break before it would only
    strand the section's own title instead)."""
    leads: list[dict] = []
    out: list[str] = []
    pos = 0
    for m in _PDF_LEAD_RE.finditer(doc_html):
        n = len(leads)
        lead, mid, wrap = m.group("lead"), m.group("mid"), m.group("wrap")

        tag = _PDF_OPEN_TAG_RE.match(lead)
        attrs = tag.group(2)
        id_m = re.search(r'\bid="([^"]+)"', attrs)
        lead_id = id_m.group(1) if id_m else f"pdf-lead-{n}"
        new_attrs = attrs + f' data-pdf-lead="{n}"' + ("" if id_m else f' id="{lead_id}"')
        lead = f"<{tag.group(1)}{new_attrs}>" + lead[tag.end():]
        mid = mid.replace("<p>", f'<p data-pdf-lead-mid="{n}">', 1)
        wrap = wrap[:-len("<table")] + f'<table data-pdf-lead-table="{n}"'

        # The first row comes after the match; reuse its id if
        # _pdf_mark_table_bounds() already gave it one.
        tr = _PDF_TR_RE.search(doc_html, m.end())
        tr_id_m = re.search(r'\bid="([^"]+)"', tr.group(1))
        row_id = tr_id_m.group(1) if tr_id_m else f"pdf-lead-{n}--row1"

        out.append(doc_html[pos:m.start()])
        out.append(lead + mid + wrap)
        out.append(doc_html[m.end():tr.start()])
        out.append(tr.group(0) if tr_id_m else f'<tr id="{row_id}"{tr.group(1)}>')
        pos = tr.end()

        leads.append({
            "n": n,
            "lead_id": lead_id,
            "row_id": row_id,
            "first_in_section": doc_html[:m.start()].rstrip().endswith("</h1>"),
            "text": re.sub(r"<[^>]+>", "", m.group("lead"))[:60],
        })
    out.append(doc_html[pos:])
    return "".join(out), leads


# Each fix shifts everything after it, which can split a heading further on
# that was fine before -- so re-check, but cap the number of layout passes
# (each is a full render of the guide).
_PDF_LEAD_MAX_PASSES = 4


def _pdf_heading_tables_css(doc_html: str, leads: list[dict], base_css: str, base_url: str) -> str:
    """CSS keeping each table-introducing heading on the same page as its
    table's first row, applied only to the headings that a real layout pass
    shows are split from their table.

    The fix is a page break before the heading, so heading and table move
    to the next page together. If the table is too tall to fit on a page
    even then (or the heading opens its section, where a break would only
    strand the section title), the table's `page-break-inside: avoid` is
    dropped instead, so it starts directly under its heading and continues
    onto the following page(s) like any long table."""
    def _break(n: int) -> str:
        return f'[data-pdf-lead="{n}"] {{ break-before: page; }}'

    def _flow(n: int) -> str:
        return f'[data-pdf-lead-table="{n}"] {{ page-break-inside: auto; break-inside: auto; }}'

    rules: dict[int, str] = {}
    stuck: list[dict] = []
    for _ in range(_PDF_LEAD_MAX_PASSES):
        pages = _pdf_anchor_pages(doc_html, base_css + "\n" + "\n".join(rules.values()), base_url)
        stuck = []
        changed = False
        for lead in leads:
            n = lead["n"]
            if pages.get(lead["lead_id"]) == pages.get(lead["row_id"]):
                continue
            current = rules.get(n)
            if current is None and not lead["first_in_section"]:
                rules[n] = _break(n)
            elif current != _flow(n):
                rules[n] = _flow(n)
            else:
                stuck.append(lead)
                continue
            changed = True
        if not changed:
            break
    for lead in stuck:
        console.print(f"  [yellow]![/yellow] PDF: heading still split from its table: {lead['text']!r}")
    if rules:
        console.print(f"  [green]+[/green] PDF: kept {len(rules)} heading(s) with their tables")
    return "\n".join(rules.values())


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

    _make_pdf_header_logo()

    if about_ch:
        # Display as "About", matching the site's nav/footer -- not the raw
        # frontmatter title ("How do I use this guide?").
        about_ch = {**about_ch, "title": ABOUT_TITLE}

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
  <img class="pdf-cover__logo" src="assets/images/logo-ec-positive.svg" alt="European Commission">
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
    doc_html, leads = _pdf_mark_heading_tables(doc_html)
    pdf_css = PDF_CSS + "\n" + _pdf_tall_figures_css()
    # A table can only be measured against a real layout pass (unlike a
    # figure's fixed image height), so lay the document out once first to
    # find which tables need extra room, then render the real PDF with that
    # CSS added.
    pdf_css += "\n" + _pdf_tall_tables_css(doc_html, pdf_css, base_url)
    # Last, since both of the passes above move content between pages.
    pdf_css += "\n" + _pdf_heading_tables_css(doc_html, leads, pdf_css, base_url)
    weasyprint.HTML(string=doc_html, base_url=base_url).write_pdf(
        out_path, stylesheets=[weasyprint.CSS(string=pdf_css, base_url=base_url)]
    )
    console.print(f"  [green]+[/green] {out_path.name} ({out_path.stat().st_size / 1024:.0f} KB)")


# ==============================================================================
# SECTION 9 -- Build orchestration
# ==============================================================================

# ECL v5.1.0 asset subset -- see brand/ECL-Migration-Spec.md section 3.
# Deliberately excludes ecl-reset.css (spec section 5), the print stylesheets,
# colour modes, easy-to-read, the ESM bundle, and 42 non-English logos.
ECL_SRC = Path("brand") / "ecl"
ECL_FILES = [
    "styles/ecl-ec.css",
    "styles/optional/ecl-ec-default.css",
    "styles/optional/ecl-ec-utilities.css",
    "scripts/ecl-ec.js",
    "fonts/InterVariable.woff2",
    "fonts/InterVariable-Italic.woff2",
    "images/logo/positive/logo-ec--en.svg",
    "images/logo/negative/logo-ec--en.svg",
    "images/logo/logo-ec--mute-negative.svg",
]


def copy_ecl_assets(dst: Path) -> int:
    """Copy the ECL subset into dst (docs/assets/ecl). Returns files copied."""
    copied = 0
    for rel in ECL_FILES:
        src = ECL_SRC / rel
        if not src.exists():
            console.print(f"  [yellow]![/yellow] missing ECL asset: {rel}")
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == "styles/ecl-ec.css":
            # ECL ships Inter with font-display:swap (zero block period), so
            # every page navigation first paints in Arial and then swaps to
            # Inter once the (cached) font file is decoded -- a visible font
            # flash on each link click. "fallback" gives a ~100ms block
            # period, long enough for a cached/preloaded font to be ready
            # before first paint, while still swapping in Inter (within 3s)
            # on a cold first visit. See also INTER_FONT_URLS preloads.
            css = src.read_text(encoding="utf-8")
            patched = css.replace("font-display:swap", "font-display:fallback")
            if patched == css:
                console.print("  [yellow]![/yellow] ecl-ec.css: font-display:swap not found to patch")
            target.write_text(patched, encoding="utf-8")
        else:
            shutil.copy2(src, target)
        copied += 1
    return copied


def build_site() -> tuple[list[dict], list[dict], dict]:
    global _GUIDE_NAV_ITEMS, _ANNEXES_SLUG
    console.rule("[bold blue]Building site[/bold blue]")

    # -- Read all markdown files --------------------------------------------------
    md_files = sorted(INPUT_DIR.glob("*.md"))
    if not md_files:
        console.print(f"[red]No .md files found in {INPUT_DIR}/[/red]")
        sys.exit(1)

    all_parsed = [parse_md_file(p) for p in md_files]

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

    _ecl_n = copy_ecl_assets(SITE_DIR / "assets" / "ecl")
    console.print(f"  [green]+[/green] assets/ecl/ ({_ecl_n} ECL v5.1.0 files)")

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
    index = build_search_index(nav_sections + all_sub, summaries, by_slug, about_ch)
    (SITE_DIR / "search_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    console.print("  [green]+[/green] search_index.json")

    # -- Root pages ---------------------------------------------------------------
    # index.html is written after the PDF, so it can show the PDF's size.
    (SITE_DIR / "search.html").write_text(build_search_page(),  encoding="utf-8")
    (SITE_DIR / "404.html"  ).write_text(build_404_page(),     encoding="utf-8")
    console.print("  [green]+[/green] search.html, 404.html")

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

    (SITE_DIR / "index.html").write_text(
        build_index_page(nav_sections, summaries, about_ch=about_ch), encoding="utf-8"
    )
    console.print("  [green]+[/green] index.html")

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
