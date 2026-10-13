#!/usr/bin/env python3
"""
fix_annex_tables.py — Rebuild the Annex tables in output/annexes_*.md from the
Word source so their rows, columns and merged cells match the original guide.

The earlier extraction lost the Word table structure in several ways:
vertically merged cells came out as runs of blank cells, rows Word split across
a page break became two half-empty rows, multi-paragraph cells (and the bullet
lists inside them) were flattened onto one line by the GFM pipe-table format,
and superscripts (m³, footnote markers) were lost.

This script reads every table straight from the DOCX (honouring gridSpan /
vMerge), applies a small, explicit set of per-table fixups, and splices the
result into the matching markdown file as an HTML table. Cell text and inline
formatting (italic, bold, superscript, in-cell lists) come from the DOCX.

Usage:
    python fix_annex_tables.py

Safe to re-run: each table is regenerated from the DOCX every time.
"""

import html as _html
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

DOCX_PATH = Path("data/CITES Reference Guide_Nov 2025 FIN_clean.docx")
OUTPUT_DIR = Path("output")

_URL_RE = re.compile(r"(https?://[^\s<>()“”‘’]+[^\s<>().,;:“”‘’'\"])")
_DASH_ITEM_RE = re.compile(r"^\s*[-•]\s+")
_TABLE_BLOCK_RE = re.compile(
    r'<div class="table-wrap">.*?</table>\s*</div>'   # embedded HTML table
    r"|(?:^\|[^\n]*\|[ \t]*\n?)+",                    # run of GFM pipe rows
    re.S | re.M,
)


# ── DOCX cell content → HTML ──────────────────────────────────────────────────

def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _run_props(r):
    rpr = r.find(qn("w:rPr"))
    b = i = False
    va = None
    if rpr is not None:
        el = rpr.find(qn("w:b"))
        b = el is not None and el.get(qn("w:val")) not in ("0", "false")
        el = rpr.find(qn("w:i"))
        i = el is not None and el.get(qn("w:val")) not in ("0", "false")
        v = rpr.find(qn("w:vertAlign"))
        if v is not None:
            va = v.get(qn("w:val"))
    return b, i, va


def _para_segments(p) -> list:
    """[text, bold, italic, vertAlign] runs of a paragraph, in order."""
    segs = []

    def walk(el):
        for ch in el:
            tag = ch.tag.split("}")[1]
            if tag == "r":
                b, i, va = _run_props(ch)
                for rc in ch:
                    rt = rc.tag.split("}")[1]
                    if rt == "t":
                        segs.append([rc.text or "", b, i, va])
                    elif rt == "tab":
                        segs.append([" ", b, i, va])
                    elif rt == "br":
                        segs.append(["\n", False, False, None])
                    elif rt == "noBreakHyphen":
                        segs.append(["-", b, i, va])
                    elif rt == "footnoteReference":
                        segs.append([f"[^FN{rc.get(qn('w:id'))}]", False, False, "fn"])
            elif tag in ("hyperlink", "smartTag", "fldSimple", "ins", "sdt", "sdtContent", "customXml"):
                walk(ch)

    walk(p)
    return segs


def _segs_to_html(segs: list) -> str:
    merged = []
    for s in segs:
        if merged and merged[-1][1:] == s[1:]:
            merged[-1][0] += s[0]
        else:
            merged.append(list(s))
    out = []
    for text, b, i, va in merged:
        if va == "fn":
            out.append(text)
            continue
        if text == "\n":
            out.append("<br>")
            continue
        h = _esc(text)
        core = h.strip()
        if not core:
            out.append(h)
            continue
        lead = h[: len(h) - len(h.lstrip())]
        trail = h[len(h.rstrip()):]
        if va == "superscript":
            core = f"<sup>{core}</sup>"
        elif va == "subscript":
            core = f"<sub>{core}</sub>"
        if i:
            core = f"<em>{core}</em>"
        if b:
            core = f"<strong>{core}</strong>"
        out.append(lead + core + trail)
    s = "".join(out)
    s = re.sub(r"[  ]{2,}", " ", s)
    for a, b in (("</em> <em>", " "), ("</strong> <strong>", " "), ("</em><em>", ""), ("</strong><strong>", "")):
        s = s.replace(a, b)
    return s.strip()


def _is_list_para(p) -> bool:
    ppr = p.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn("w:numPr")) is not None


def _cell_html(tc) -> str:
    """Paragraphs of a table cell → HTML. Word list paragraphs, and runs of
    paragraphs typed with a leading '- ' (the source's manual bullets), become
    a <ul>; a lone plain paragraph stays bare text."""
    paras = []
    for p in tc.findall(qn("w:p")):
        h = _segs_to_html(_para_segments(p))
        plain = re.sub(r"<[^>]+>", "", h).strip()
        if not plain:
            continue
        is_item = _is_list_para(p)
        if _DASH_ITEM_RE.match(plain):
            is_item = True
            h = re.sub(r"^((?:<[^>]+>)*)\s*[-•]\s+", r"\1", h)
        paras.append((h, is_item))
    if not paras:
        return ""
    if len(paras) == 1 and not paras[0][1]:
        return paras[0][0]
    out, in_list = [], False
    for h, is_item in paras:
        if is_item and not in_list:
            out.append("<ul>")
            in_list = True
        elif not is_item and in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<li>{h}</li>" if is_item else f"<p>{h}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _linkify(h: str) -> str:
    parts = re.split(r"(<[^>]+>)", h)
    return "".join(
        p if p.startswith("<") else _URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', p)
        for p in parts
    )


# ── Logical grid ──────────────────────────────────────────────────────────────

class Cell:
    __slots__ = ("html",)

    def __init__(self, html: str = ""):
        self.html = html

    @property
    def text(self) -> str:
        return _html.unescape(re.sub(r"<[^>]+>", "", self.html)).replace("\xa0", " ").strip()


class Grid:
    """rows × cols matrix of shared Cell objects: a merged cell is the same
    object in every position it covers, so rowspan/colspan fall out of
    comparing neighbours at render time."""

    def __init__(self, tbl):
        grid = tbl.find(qn("w:tblGrid"))
        self.ncols = len(grid.findall(qn("w:gridCol")))
        self.rows = []
        for tr in tbl.findall(qn("w:tr")):
            g = [None] * self.ncols
            col = 0
            trpr = tr.find(qn("w:trPr"))
            if trpr is not None and trpr.find(qn("w:gridBefore")) is not None:
                col = int(trpr.find(qn("w:gridBefore")).get(qn("w:val")))
            for tc in tr.findall(qn("w:tc")):
                pr = tc.find(qn("w:tcPr"))
                span, cont = 1, False
                if pr is not None:
                    gs = pr.find(qn("w:gridSpan"))
                    if gs is not None:
                        span = int(gs.get(qn("w:val")))
                    vm = pr.find(qn("w:vMerge"))
                    cont = vm is not None and vm.get(qn("w:val")) != "restart"
                if cont and self.rows:
                    cell = self.rows[-1][col]
                else:
                    cell = Cell(_cell_html(tc))
                for k in range(col, min(col + span, self.ncols)):
                    g[k] = cell
                col += span
            self.rows.append([c if c is not None else Cell() for c in g])

    # -- queries ---------------------------------------------------------
    def is_full(self, r: int) -> bool:
        return all(c is self.rows[r][0] for c in self.rows[r])

    def find(self, needle: str, col: int = None, start: int = 0) -> int:
        """First row whose cell (in `col`, if given) starts with `needle`."""
        for r in range(start, len(self.rows)):
            for k, c in enumerate(self.rows[r]):
                if (col is None or k == col) and c.text.startswith(needle):
                    return r
        raise KeyError(needle)

    def _shared(self, r: int, other: int) -> bool:
        return 0 <= other < len(self.rows) and any(
            self.rows[r][k] is self.rows[other][k] for k in range(self.ncols))

    # -- fixups ----------------------------------------------------------
    def drop_empty_rows(self):
        self.rows = [g for r, g in enumerate(self.rows)
                     if any(c.text for c in g) or self._shared(r, r - 1) or self._shared(r, r + 1)]

    def compact(self):
        """Drop rows whose every cell is shared with the row above."""
        r = 1
        while r < len(self.rows):
            if not self.is_full(r) and all(self.rows[r][k] is self.rows[r - 1][k] for k in range(self.ncols)):
                del self.rows[r]
            else:
                r += 1

    def fold(self, r: int, joiner: str = " "):
        """Row r is the second half of a row Word split at a page break:
        append its own cells to the cells above, then delete it."""
        g, up = self.rows[r], self.rows[r - 1]
        seen = set()
        for k in range(self.ncols):
            c = g[k]
            if c is up[k] or id(c) in seen:
                continue
            seen.add(id(c))
            if c.text:
                up[k].html = f"{up[k].html}{joiner}{c.html}" if up[k].text else c.html
        del self.rows[r]

    def combine(self, r1: int, r2: int, col: int, joiner: str = "<br>"):
        """Make rows r1..r2 share one cell in `col` (a list the source split
        one-line-per-cell), then drop rows left fully redundant."""
        parts, seen = [], set()
        for r in range(r1, r2 + 1):
            c = self.rows[r][col]
            if id(c) not in seen and c.text:
                parts.append(c.html)
            seen.add(id(c))
        cell = Cell(joiner.join(parts))
        for r in range(r1, r2 + 1):
            self.rows[r][col] = cell
        self.compact()

    def hier_merge(self, cols: list, start: int = 1):
        """Merge hierarchical columns (Order, then Family) into vertical runs.

        A non-empty value starts a run; blank cells below it extend it. For the
        outermost column a blank always extends the run above (a blank Order
        cell means "same Order"). Deeper columns also end a run wherever a
        shallower column's run ends, merge identical consecutive values, and
        start a separate blank run after a named one rather than inheriting it.
        Full-width rows (class sub-headers) reset every run."""
        boundaries = None
        for depth, col in enumerate(cols):
            runs, cur, named = [], None, False
            for r in range(start, len(self.rows)):
                c = self.rows[r][col]
                spans_cols = (col + 1 < self.ncols and self.rows[r][col + 1] is c) or \
                    (col > 0 and self.rows[r][col - 1] is c)
                if self.is_full(r) or spans_cols:
                    if cur:
                        runs.append(cur)
                    cur = None
                    continue
                if cur is None or (boundaries is not None and r in boundaries):
                    if cur:
                        runs.append(cur)
                    cur, named = [r, r], bool(c.text)
                    continue
                if self.rows[r - 1][col] is c:
                    cur[1] = r
                elif c.text:
                    if named and c.text == self.rows[cur[0]][col].text:
                        cur[1] = r
                    else:
                        runs.append(cur)
                        cur, named = [r, r], True
                elif depth == 0 or not named:
                    cur[1] = r
                else:
                    runs.append(cur)
                    cur, named = [r, r], False
            if cur:
                runs.append(cur)
            for a, b in runs:
                cell = Cell(self.rows[a][col].html)
                for r in range(a, b + 1):
                    self.rows[r][col] = cell
            boundaries = {a for a, _ in runs}

    def map_html(self, fn):
        seen = set()
        for g in self.rows:
            for c in g:
                if id(c) not in seen:
                    seen.add(id(c))
                    c.html = fn(c.html)

    # -- output ----------------------------------------------------------
    def body_rows(self, start: int = 0, subheader: bool = True) -> list:
        out = []
        for r in range(start, len(self.rows)):
            if subheader and self.is_full(r):
                if r == start or self.rows[r - 1][0] is not self.rows[r][0]:
                    out.append(f'<tr><td colspan="{self.ncols}" class="table-subheader">'
                               f'{_linkify(self.rows[r][0].html)}</td></tr>')
                continue
            out.append(self.row_html(r, start))
        return out

    def row_html(self, r: int, start: int) -> str:
        g, cells, k = self.rows[r], [], 0
        while k < self.ncols:
            c = g[k]
            span = 1
            while k + span < self.ncols and g[k + span] is c:
                span += 1
            if r > start and self.rows[r - 1][k] is c:
                k += span
                continue
            rs = 1
            while r + rs < len(self.rows) and self.rows[r + rs][k] is c:
                rs += 1
            attrs = (f' colspan="{span}"' if span > 1 else "") + (f' rowspan="{rs}"' if rs > 1 else "")
            cells.append(f"<td{attrs}>{_linkify(c.html)}</td>")
            k += span
        return "<tr>" + "".join(cells) + "</tr>"


def table_html(head: list, body: list, widths: list = None, cls: str = "") -> str:
    """Assemble the markdown-embedded HTML table block (same wrapper/class as
    the hand-built tables already in output/). cls "table--wide" /
    "table--xwide" give the table a minimum width on the website (see
    build_site.py) so it scrolls on narrow screens instead of squeezing
    columns until long words break mid-word."""
    lines = ['<div class="table-wrap">', f'<table class="govuk-table{" " + cls if cls else ""}">']
    if widths:
        lines.append("<colgroup>" + "".join(
            f'<col style="width:{w}%">' if w else "<col>" for w in widths) + "</colgroup>")
    lines += ["<thead>", *head, "</thead>", "<tbody>", *body, "</tbody>", "</table>", "</div>"]
    return "\n".join(lines)


def head_text(h: str) -> str:
    """A source header cell as plain header text: <th> is already bold, and
    the cell's line breaks only reflect Word's column width."""
    h = re.sub(r"</?strong>", "", h)
    h = re.sub(r"</p>\s*<p>", " ", h)
    h = re.sub(r"</?p>|<br>", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def th_row(*cells, sub: bool = False) -> str:
    """cells: str or (str, colspan)."""
    out = []
    for c in cells:
        text, span = (c, 1) if isinstance(c, str) else c
        attrs = (f' colspan="{span}"' if span > 1 else "") + (' class="table-subheader"' if sub else "")
        out.append(f"<th{attrs}>{text}</th>")
    return "<tr>" + "".join(out) + "</tr>"


# ── Markdown splicing ─────────────────────────────────────────────────────────

def table_spans(text: str) -> list:
    """(start, end) character spans of every table block in a markdown file."""
    return [(m.start(), m.end()) for m in _TABLE_BLOCK_RE.finditer(text)]


def splice(path: Path, replacements: dict):
    """replacements: {table_index: new_block}; the index counts table blocks
    in the file in order."""
    text = path.read_text(encoding="utf-8")
    spans = table_spans(text)
    for idx in sorted(replacements, reverse=True):
        a, b = spans[idx]
        text = text[:a] + replacements[idx] + "\n\n" + text[b:].lstrip("\n")
    path.write_text(text, encoding="utf-8")


# ── Per-annex builders ────────────────────────────────────────────────────────

def annex_v(T):
    g = T[18]
    g.drop_empty_rows()
    # Literal superscript note markers in the source → the page's footnotes.
    g.map_html(lambda h: re.sub(r"<sup>([123])</sup>", r"[^\1]", h))
    head = [th_row(*(head_text(g.rows[0][k].html) for k in range(g.ncols)))]
    body = []
    for r in range(1, len(g.rows)):
        row = g.row_html(r, 1)
        row = row.replace("<td>X</td>", '<td class="cell-center">X</td>')
        row = re.sub(r'<td( colspan="2")?>\(see note 3\)</td>', r'<td\1 class="cell-center">(see note 3)</td>', row)
        body.append(row)
    t1 = table_html(head, body, [24, 10, 12, 13, 17, 11, 13], "table--wide")

    # The second "table" is a borderless three-column layout of territory
    # names, not tabular data -- render it as a plain list in reading order.
    names = []
    for k in range(T[19].ncols):
        for row in T[19].rows:
            if row[k].text and row[k].text not in names:
                names.append(row[k].text)
    return t1, "\n".join(f"- {n}" for n in names)


def annex_vi(T):
    g = T[20]
    g.drop_empty_rows()
    head = [th_row(*(g.rows[0][k].text for k in range(g.ncols)))]
    body = g.body_rows(1)
    # The veneer row stacks two sub-types in one cell, with their codes and
    # units on separate lines of the neighbouring cells -- give each its own row.
    i = next(k for k, row in enumerate(body) if row.startswith("<tr><td><p>veneer sheets"))
    expl = g.rows[g.find("veneer sheets", 0)][4].html
    body[i:i + 1] = [
        f'<tr><td>veneer sheets — rotary veneer</td><td>VEN</td><td>m3</td><td>kg</td><td rowspan="2">{expl}</td></tr>',
        "<tr><td>veneer sheets — sliced veneer</td><td>VEN</td><td>m2</td><td>kg</td></tr>",
    ]
    t1 = table_html(head, body, [17, 10, 11, 11, 51], "table--wide")

    u = T[21]
    u.drop_empty_rows()
    head = [th_row(*(u.rows[0][k].text for k in range(u.ncols)))]
    body = u.body_rows(1)
    body[-1] = body[-1].replace(' class="table-subheader"', "")  # the NB note row
    return t1, table_html(head, body, [70, 30])


def annex_vii(T):
    g = T[22]
    g.drop_empty_rows()
    # Rows Word split across a page break.
    g.fold(g.find("RHYNCHOCEPHALIA", 0))
    g.fold(g.find("phylogeny of", 3))
    g.fold(g.find("(http://www.senckenberg", 3))
    g.fold(g.find("https://cites.org/sites/default/files/eng/cop/17/WorkingDocs", 3))
    g.fold(g.find("(2017): A new species of Andinobates", 3))
    for _ in range(2):
        r = g.find("in combination with", 3)
        g.fold(r, "<br>")
        g.fold(r, "<br>")
    # One statement the source spread one-line-per-cell.
    for a, b in (("All MAMMALIA taxa", "— with the exception of the taxa noted"),
                 ("Loxocemidae spp.", "and except for the species mentioned below"),
                 ("Anura: Microhylidae", "Telmatobius culeus"),
                 ("Elasmobranchii: Carcharhiniformes", "Actinopteri: Perciformes"),
                 ("Elasmobranchii: Lamniformes: Lamnidae", "Rhinidae spp.")):
        g.combine(g.find(a, 2), g.find(b, 2), 2)
    # Source-document error: these mouse/dwarf lemurs are Cheirogaleidae, not
    # Cercopithecidae (see SOURCE-DOCUMENT-ISSUES.md).
    for sp in ("Cheirogaleus andysabini", "Cheirogaleus lavasoensis", "Cheirogaleus chethi",
               "Microcebus gerpi", "Microcebus marohita"):
        g.rows[g.find(sp, 2)][1].html = "Cheirogaleidae"
    # Order/family names are not italicised; the source italicised a
    # separator along with the genus after it.
    g.map_html(lambda h: h.replace("Lamniformes<em>: Alopiidae: Alopias</em>",
                                   "Lamniformes: Alopiidae: <em>Alopias</em>"))
    # URLs the source broke with a stray space or a missing slash.
    g.map_html(lambda h: h.replace("_CoP18lr_CITES. pdf", "_CoP18lr_CITES.pdf")
               .replace("_CoP18lr_CITE S.pdf", "_CoP18lr_CITES.pdf")
               .replace("http://zoobank.orgurn:lsid:", "http://zoobank.org/urn:lsid:"))
    # The source's last class heading (ANTHOZOA & HYDROZOA) sits in the Order
    # column under an empty full-width bar; make it a sub-header like the rest.
    r = g.find("ANTHOZOA & HYDROZOA", 0)
    head_cell = Cell(f"<strong>{_esc(g.rows[r][0].text)}</strong>")
    g.rows.insert(r, [head_cell] * g.ncols)
    g.rows[r + 1][0] = Cell()
    g.hier_merge([0, 1])
    fauna = table_html(
        [th_row("Order", "Family", "Taxon concerned", "Taxonomic reference")],
        g.body_rows(1), [19, 16, 22, 43], "table--wide table--xwide")

    f = T[23]
    f.drop_empty_rows()
    # "Accessible at: <url>" lines the source put in a row of their own.
    while True:
        r = next((r for r in range(1, len(f.rows))
                  if f.rows[r][2].text.startswith(("Accessible", "This reference can be found"))
                  and f.rows[r][0] is f.rows[r - 1][0]), None)
        if r is None:
            break
        f.fold(r, "<br>")
    f.hier_merge([0])
    seen = set()
    for row in f.rows[1:]:
        c = row[0]
        if id(c) not in seen and c.text and not c.html.startswith("<strong>"):
            c.html = f"<strong>{c.html}</strong>"
        seen.add(id(c))
    flora = table_html([th_row("Family", "Taxon concerned", "Taxonomic reference")],
                       f.body_rows(1), [22, 24, 54], "table--wide")
    return fauna, flora


_ART_HEAD = th_row("Article", "Duty", "Relevant considerations", sub=True)
_ART_WIDTHS = [18, 62, 20]


def _unwrap(h: str) -> str:
    """Join a cell's paragraphs into one line (they were only line wraps)."""
    if not h.startswith("<p>"):
        return h
    return re.sub(r"\s+", " ", re.sub(r"</?p>", " ", h)).strip()


def _body(g, start):
    g.drop_empty_rows()
    return g.body_rows(start)


def annex_xii(T):
    tables = []
    # 1. Establishment
    tables.append(table_html(
        [th_row(("REGULATION (EC) No 338/97", 2))],
        ['<tr><td colspan="2" class="table-subheader">ESTABLISHMENT</td></tr>',
         th_row("Article", "Duty", sub=True)] + _body(T[25], 3),
        [18, 82]))
    # 2. Import/export permits -- one logical table the source broke into
    # three Word tables (27, a one-cell "ANNEX A-EXPORTS" bar, 29).
    # Source typos the site had already corrected.
    T[28].map_html(lambda h: h.replace("Article5.2 (d)", "Article 5.2 (d)")
                   .replace("the competent Sa that", "the competent SA that"))
    body = _body(T[26], 2)
    body.append(f'<tr><td colspan="3" class="table-subheader">{T[27].rows[0][0].html}</td></tr>')
    body += _body(T[28], 0)
    tables.append(table_html([th_row(("IMPORT/EXPORT PERMITS", 3)), _ART_HEAD], body, _ART_WIDTHS))
    tables.append(table_html([th_row(("INTRA-EU MOVEMENT OF ANNEX A LIVE SPECIMENS", 3)), _ART_HEAD],
                             _body(T[29], 1), _ART_WIDTHS))
    # 3. Confiscations
    tables.append(table_html([th_row(("CONFISCATIONS", 3)), _ART_HEAD], _body(T[30], 2), _ART_WIDTHS))
    # 4. SRG view on Commission import restrictions
    tables.append(table_html([th_row(("SRG VIEW ON PROPOSED COMMISSION IMPORT RESTRICTIONS", 3)), _ART_HEAD],
                             _body(T[31], 1), _ART_WIDTHS))
    # 5. Provisions under Regulation (EC) No 865/2006
    T[32].map_html(lambda h: _unwrap(h) if h.startswith("<p>Criteria in Articles 54, 55</p>") else h)
    tables.append(table_html([th_row(("Provisions under Commission Regulation (EC) No 865/2006", 3)), _ART_HEAD],
                             _body(T[32], 2), _ART_WIDTHS))
    # Table 17: purpose codes -- "Purpose" spans the code and its name. Its
    # cells' paragraph breaks only reproduce Word's line wrapping.
    T[35].map_html(_unwrap)
    tables.append(table_html([th_row(("Purpose", 2), "Treatment")], _body(T[35], 1), [8, 42, 50]))
    # Attachment J benefit/concern tables.
    for n, title in ((37, "Maintenance of the individuals in captivity"),
                     (38, "Returning the individuals in question to some form of life in the wild"),
                     (39, "Euthanasia")):
        g = T[n]
        g.drop_empty_rows()
        # "trade12." -- a stray footnote number pasted into the cell text.
        g.map_html(lambda h: h.replace("unsustainable trade12.", "unsustainable trade."))
        heads = [g.rows[1][0].text, g.rows[1][1].text]
        tables.append(table_html([th_row((f"{title}[^11]", 2)), th_row(*heads, sub=True)],
                                 g.body_rows(2), [50, 50]))
    return tables


def annex_xvi(path: Path):
    """The 865/2006 table puts each chapter's title in the Article column with
    an empty Content cell, leaving a narrow Chapter column of mostly blank
    cells. Turn chapter rows into full-width sub-headers instead."""
    text = path.read_text(encoding="utf-8")
    a, b = table_spans(text)[1]
    block = text[a:b]
    if "CHAPTER</th>" not in block:
        return  # already converted
    body, annexes_done = [], False
    for chap, art, content in re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", block):
        art_plain = re.sub(r"<[^>]+>", "", art)
        content = re.sub(r"</?strong>", "", content)
        if chap:
            body.append(f'<tr><td colspan="2" class="table-subheader">{chap} — {art_plain}</td></tr>')
            continue
        if art_plain.startswith("Annex") and not annexes_done:
            body.append('<tr><td colspan="2" class="table-subheader">Annexes</td></tr>')
            annexes_done = True
        body.append(f"<tr><td>{art_plain}</td><td>{content}</td></tr>")
    new = table_html(
        [th_row(("<strong>Regulation (EC) No 865/2006 as amended</strong>", 2)),
         th_row("ARTICLE", "CONTENT", sub=True)],
        body, [18, None])
    path.write_text(text[:a] + new + text[b:], encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    doc = Document(DOCX_PATH)
    T = [Grid(t) for t in doc.element.body.iter(qn("w:tbl"))]

    # Annex V
    p = OUTPUT_DIR / "annexes_v.md"
    t1, oct_list = annex_v(T)
    splice(p, {0: t1})
    text = p.read_text(encoding="utf-8")
    spans = table_spans(text)
    if len(spans) > 1:  # the territories layout table, on first run only
        a, b = spans[1]
        p.write_text(text[:a] + oct_list + "\n" + text[b:], encoding="utf-8")

    # Annex VI
    t1, t2 = annex_vi(T)
    splice(OUTPUT_DIR / "annexes_vi.md", {0: t1, 1: t2})

    # Annex VII
    fauna, flora = annex_vii(T)
    splice(OUTPUT_DIR / "annexes_vii.md", {0: fauna, 1: flora})

    # Annex XII: the md has 10 table blocks in the same order as built here
    # (sections 1-5 with two in section 2, Table 17, three in Attachment J).
    splice(OUTPUT_DIR / "annexes_xii.md", dict(enumerate(annex_xii(T))))

    # Annex XVI
    annex_xvi(OUTPUT_DIR / "annexes_xvi.md")


if __name__ == "__main__":
    main()
