"""
build_site.py — GOV.UK-styled static site generator.

Reads:  output/*.md  (YAML frontmatter + markdown body)
Writes: site/        (flat HTML/CSS/JS for GitHub Pages)

Run:
    python build_site.py
"""

import html as html_mod
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import markdown2
import yaml
from rich.console import Console
from rich.table import Table as RichTable
from rich import box
from slugify import slugify as py_slugify

# ── Paths ─────────────────────────────────────────────────────────────────────

INPUT_DIR = Path("output")
SITE_DIR  = Path("site")

console = Console()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Markdown parsing helpers
# ══════════════════════════════════════════════════════════════════════════════

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^\s*#\s+[^\n]+\n*", re.MULTILINE)


def parse_md_file(path: Path) -> dict:
    """Parse a markdown file into frontmatter dict + body string."""
    raw = path.read_text(encoding="utf-8")
    fm_match = _FM_RE.match(raw)
    if fm_match:
        frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        body = raw[fm_match.end():]
    else:
        frontmatter = {}
        body = raw
    # Strip the leading # h1 — the template renders its own <h1>
    body = _H1_RE.sub("", body, count=1).lstrip("\n")
    return {
        "slug":           path.stem,
        "path":           path,
        "title":          str(frontmatter.get("title", path.stem)),
        "chapter_number": int(frontmatter.get("chapter_number", 0)),
        "page_start":     frontmatter.get("page_start"),
        "page_end":       frontmatter.get("page_end"),
        "has_tables":     bool(frontmatter.get("has_tables", False)),
        "summary":        str(frontmatter.get("summary", "")),
        "body":           body,
    }


def render_markdown(content: str) -> str:
    """Render markdown to HTML (tables, fenced blocks, heading anchors)."""
    return markdown2.markdown(
        content,
        extras=["tables", "fenced-code-blocks", "header-ids", "smarty-pants"],
    )


def extract_headings(html: str) -> list[dict]:
    """
    Pull every h2/h3/h4 with its id from rendered HTML.
    Returns [{level, id, text}, ...] in document order.
    """
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
    """Convert markdown to plain text suitable for search indexing."""
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
    """HTML-escape a value for safe embedding in HTML attributes / text."""
    return html_mod.escape(str(text))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Stylesheet
# ══════════════════════════════════════════════════════════════════════════════

CSS = """\
/* ════════════════════════════════════════════════════
   EU Wildlife Trade Reference Guide
   GOV.UK-inspired stylesheet
   ════════════════════════════════════════════════════ */

:root {
  --green:        #00703c;
  --dark-green:   #004e2a;
  --black:        #0b0c0c;
  --text:         #0b0c0c;
  --secondary:    #505a5f;
  --border:       #b1b4b6;
  --light-grey:   #f3f2f1;
  --mid-grey:     #dee0e2;
  --white:        #ffffff;
  --focus:        #ffdd00;
  --visited:      #4c2c92;
  --max-width:    1060px;
  --font:         "GDS Transport", Arial, sans-serif;
}

*, *::before, *::after { box-sizing: border-box; }
html { font-size: 16px; scroll-behavior: smooth; }

body {
  font-family: var(--font);
  font-size: 1rem;
  line-height: 1.6;
  color: var(--text);
  background: var(--white);
  margin: 0;
  -webkit-font-smoothing: antialiased;
}

/* ── Skip link ─────────────────────────────────── */
.skip-link {
  position: absolute; left: -999em; top: 0; z-index: 9999;
  padding: 8px 14px; background: var(--focus); color: var(--black);
  font-weight: 700; text-decoration: none;
}
.skip-link:focus { left: 0; }

/* ── Container ─────────────────────────────────── */
.container {
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 0 20px;
}

/* ── Links ─────────────────────────────────────── */
a                { color: var(--green); }
a:hover          { color: var(--dark-green); }
a:visited        { color: var(--visited); }
a:visited:hover  { color: var(--dark-green); }
a:focus {
  outline: 3px solid var(--focus);
  outline-offset: 0;
  background: var(--focus);
  color: var(--black);
  text-decoration: none;
}

/* ── Site header — black bar ────────────────────── */
.site-header {
  background: var(--black);
  border-bottom: 8px solid var(--green);
}
.site-header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 54px;
  padding: 10px 20px;
  max-width: var(--max-width);
  margin: 0 auto;
  flex-wrap: wrap;
}
.site-header__title a {
  color: var(--white); text-decoration: none;
  font-size: 1rem; font-weight: 700; letter-spacing: .01em;
}
.site-header__title a:hover     { text-decoration: underline; color: var(--white); }
.site-header__title a:visited   { color: var(--white); }
.site-header__title a:focus     { background: var(--focus); color: var(--black); }

/* ── Header search ──────────────────────────────── */
.header-search { display: flex; flex-shrink: 0; }
.header-search input[type="search"] {
  padding: 6px 10px; border: 2px solid var(--white); border-right: none;
  font: inherit; font-size: .875rem; min-width: 190px; background: var(--white);
  color: var(--black);
}
.header-search input[type="search"]:focus { outline: 3px solid var(--focus); }
.header-search button {
  padding: 6px 14px; background: var(--green); color: var(--white);
  border: 2px solid var(--green); font: inherit; font-size: .875rem;
  font-weight: 700; cursor: pointer; white-space: nowrap;
}
.header-search button:hover { background: var(--dark-green); border-color: var(--dark-green); }
.header-search button:focus { outline: 3px solid var(--focus); }

/* ── Breadcrumbs ────────────────────────────────── */
.breadcrumbs {
  border-bottom: 1px solid var(--mid-grey);
  padding: 10px 0;
  background: var(--white);
}
.breadcrumbs ol {
  list-style: none; margin: 0; padding: 0 20px;
  display: flex; flex-wrap: wrap; gap: 0 4px; font-size: .875rem;
  max-width: var(--max-width); margin: 0 auto;
}
.breadcrumbs li { display: flex; align-items: center; gap: 4px; }
.breadcrumbs li + li::before { content: "›"; color: var(--secondary); }
.breadcrumbs a   { color: var(--green); font-size: .875rem; }
.breadcrumbs [aria-current="page"] { color: var(--secondary); }

/* ── Phase banner ───────────────────────────────── */
.phase-banner {
  background: var(--light-grey);
  border-bottom: 1px solid var(--border);
  padding: 8px 0;
}
.phase-banner .container { display: flex; align-items: center; gap: 12px; font-size: .875rem; }
.phase-tag {
  padding: 2px 8px; background: var(--green); color: var(--white);
  font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
}

/* ── Main wrapper ───────────────────────────────── */
.main-content { padding: 30px 0 70px; }

/* ── Page grid (sidebar + article) ─────────────── */
.page-grid { display: flex; gap: 40px; align-items: flex-start; }

/* ── Sidebar (desktop, sticky) ─────────────────── */
.sidebar {
  flex: 0 0 230px;
  max-width: 230px;
  position: sticky;
  top: 24px;
  max-height: calc(100vh - 48px);
  overflow-y: auto;
}
.sidebar__label {
  font-size: .75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: var(--secondary);
  border-bottom: 2px solid var(--black); padding-bottom: 8px; margin-bottom: 6px;
}
.sidebar__nav { list-style: none; padding: 0; margin: 0; }
.sidebar__nav li { border-bottom: 1px solid var(--light-grey); }
.sidebar__nav a {
  display: block; padding: 7px 0; color: var(--text);
  text-decoration: none; font-size: .875rem; line-height: 1.35;
}
.sidebar__nav a:hover { color: var(--green); text-decoration: underline; }
.sidebar__nav a.is-active {
  font-weight: 700; color: var(--green);
  border-left: 4px solid var(--green); padding-left: 10px; margin-left: -14px;
}
.sidebar__nav .sidebar-h3 a { padding-left: 14px; color: var(--secondary); font-size: .8125rem; }
.sidebar__nav .sidebar-h3 a.is-active { padding-left: 24px; margin-left: -14px; }

/* ── Article area ───────────────────────────────── */
.article { flex: 1 1 auto; min-width: 0; }

/* ── Contents box (top of article on desktop) ───── */
.contents-box {
  border: 1px solid var(--border);
  padding: 20px 24px 16px;
  margin-bottom: 30px;
}
.contents-box__title { font-size: 1rem; font-weight: 700; margin: 0 0 10px; }
.contents-box ol { margin: 0; padding-left: 1.4em; }
.contents-box li { margin: 5px 0; font-size: .9375rem; }
.contents-box a { color: var(--green); }

/* ── Mobile contents dropdown ───────────────────── */
.mobile-contents { display: none; margin-bottom: 20px; }
.mobile-contents summary {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; background: var(--light-grey); border: 1px solid var(--border);
  cursor: pointer; font-weight: 700; font-size: .9375rem; list-style: none;
}
.mobile-contents summary::-webkit-details-marker { display: none; }
.mobile-contents[open] summary { border-bottom: none; }
.mobile-contents__body {
  border: 1px solid var(--border); border-top: none;
  padding: 12px 16px 16px; background: var(--white);
}
.mobile-contents__body ol { padding-left: 1.4em; margin: 0; }
.mobile-contents__body li { margin: 6px 0; font-size: .9375rem; }

/* ── Typography ─────────────────────────────────── */
h1 { font-size: 2rem;     font-weight: 700; line-height: 1.2; margin: 0 0 24px; }
h2 {
  font-size: 1.5rem;   font-weight: 700; line-height: 1.25;
  margin: 44px 0 18px; padding-top: 12px; border-top: 1px solid var(--border);
}
h3 { font-size: 1.1875rem; font-weight: 700; line-height: 1.3;  margin: 30px 0 14px; }
h4 { font-size: 1rem;     font-weight: 700; line-height: 1.4;  margin: 24px 0 12px; }
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

/* ── Tables ─────────────────────────────────────── */
.article table {
  border-collapse: collapse; width: 100%; margin: 20px 0 30px;
  font-size: .9375rem; display: block; overflow-x: auto;
}
.article table th {
  background: var(--black); color: var(--white); padding: 10px 14px;
  text-align: left; font-weight: 700; border: 1px solid #333; white-space: nowrap;
}
.article table td {
  padding: 9px 14px; border: 1px solid var(--border); vertical-align: top;
}
.article table tr:nth-child(even) td { background: var(--light-grey); }
.article table tr:hover td { background: var(--mid-grey); }

/* ── Chapter meta line ──────────────────────────── */
.chapter-meta {
  display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
  font-size: .875rem; color: var(--secondary); margin-bottom: 24px;
}
.badge {
  display: inline-block; padding: 2px 8px; background: var(--green);
  color: var(--white); font-size: .75rem; font-weight: 700; border-radius: 2px;
}

/* ── Prev / Next navigation ─────────────────────── */
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
.chapter-nav__next { text-align: right; margin-left: auto; }

/* ── Back to top ────────────────────────────────── */
.back-to-top {
  display: block; text-align: right; font-size: .875rem;
  margin-top: 10px; color: var(--green);
}

/* ── Homepage intro ─────────────────────────────── */
.hero {
  background: var(--light-grey); border-bottom: 1px solid var(--border);
  padding: 40px 0;
}
.hero h1 { margin-bottom: 12px; }
.hero__lead {
  font-size: 1.1875rem; max-width: 680px; line-height: 1.6; margin-bottom: 24px;
}

/* ── Homepage search ────────────────────────────── */
.search-form {
  display: flex; max-width: 580px; gap: 0;
}
.search-form input[type="search"] {
  flex: 1; padding: 10px 14px; font: inherit; font-size: 1rem;
  border: 2px solid var(--black); border-right: none; color: var(--black); background: var(--white);
}
.search-form input[type="search"]:focus { outline: 3px solid var(--focus); }
.search-form button {
  padding: 10px 20px; background: var(--green); color: var(--white);
  border: 2px solid var(--green); font: inherit; font-size: 1rem; font-weight: 700; cursor: pointer;
}
.search-form button:hover { background: var(--dark-green); }

/* ── Chapter card grid ──────────────────────────── */
.chapter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 20px; margin-top: 30px;
}
.chapter-card {
  display: flex; flex-direction: column;
  border: 1px solid var(--border); padding: 20px;
  text-decoration: none; color: inherit;
  transition: border-color .1s, box-shadow .1s;
}
.chapter-card:hover {
  border-color: var(--green); box-shadow: 0 2px 8px rgba(0,0,0,.07);
  text-decoration: none;
}
.chapter-card:visited { color: inherit; }
.chapter-card__num {
  font-size: .75rem; color: var(--secondary);
  text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px;
}
.chapter-card__title {
  font-size: 1rem; font-weight: 700; color: var(--green);
  margin-bottom: 8px; line-height: 1.3;
}
.chapter-card:hover .chapter-card__title { text-decoration: underline; }
.chapter-card__summary {
  font-size: .9rem; color: var(--secondary); flex-grow: 1;
  margin-bottom: 12px; line-height: 1.45;
}
.chapter-card__foot {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: .8125rem; color: var(--secondary);
  padding-top: 10px; border-top: 1px solid var(--light-grey); margin-top: auto;
}

/* ── Search results page ────────────────────────── */
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

/* ── Footer ─────────────────────────────────────── */
.site-footer {
  background: var(--light-grey); border-top: 1px solid var(--border);
  padding: 30px 0; margin-top: 60px;
}
.site-footer p { font-size: .875rem; color: var(--secondary); margin-bottom: 6px; }
.site-footer a { color: var(--secondary); font-size: .875rem; }
.site-footer a:hover { color: var(--green); }

/* ── Responsive ─────────────────────────────────── */
@media screen and (max-width: 768px) {
  .site-header__inner { flex-wrap: wrap; gap: 8px; }
  .header-search { width: 100%; }
  .header-search input[type="search"] { flex: 1; min-width: 0; }

  .page-grid { flex-direction: column; }
  .sidebar { display: none; }
  .mobile-contents { display: block; }
  .contents-box { display: none; }

  h1 { font-size: 1.5rem; }
  h2 { font-size: 1.25rem; }
  h3 { font-size: 1.0625rem; }

  .chapter-grid { grid-template-columns: 1fr; }
  .chapter-nav a { max-width: 100%; }
  .search-form { flex-wrap: wrap; }
  .search-form input[type="search"] { border-right: 2px solid var(--black); width: 100%; }
  .search-form button { width: 100%; }
}
@media screen and (min-width: 769px) {
  .mobile-contents { display: none; }
  .contents-box { display: block; }
}

/* ── Print ──────────────────────────────────────── */
@media print {
  .site-header, .breadcrumbs, .sidebar, .contents-box,
  .mobile-contents, .chapter-nav, .back-to-top,
  .site-footer, .phase-banner, .hero .search-form { display: none !important; }

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
  .article table { display: table; }
  .article table th {
    background: #222 !important; color: #fff !important;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
  .article table tr:nth-child(even) td {
    background: #f5f5f5 !important;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
}
"""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — JavaScript
# ══════════════════════════════════════════════════════════════════════════════

MAIN_JS = """\
/* main.js — sidebar highlight + scroll utilities */
(function () {
  'use strict';

  function initSidebarHighlight() {
    var nav = document.querySelector('.sidebar__nav');
    if (!nav) return;
    var links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    if (!links.length) return;
    var targets = links.map(function (l) {
      return document.getElementById(l.getAttribute('href').slice(1));
    }).filter(Boolean);
    if (!targets.length) return;

    var current = 0;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var idx = targets.indexOf(e.target);
          if (idx !== -1) current = idx;
        }
      });
      links.forEach(function (l) { l.classList.remove('is-active'); });
      if (links[current]) links[current].classList.add('is-active');
    }, { rootMargin: '-15% 0px -75% 0px', threshold: 0 });

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

  document.addEventListener('DOMContentLoaded', function () {
    initSidebarHighlight();
    initBackToTop();
  });
}());
"""

SEARCH_JS = """\
/* search.js — client-side full-text search for search.html */
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

  function render(results, q) {
    var container = document.getElementById('search-results');
    var countEl   = document.getElementById('search-count');
    if (!container) return;
    if (!q) { container.innerHTML = ''; if (countEl) countEl.textContent = ''; return; }
    if (!results.length) {
      if (countEl) countEl.textContent = '0 results';
      container.innerHTML = '<div class="no-results"><p>No results found for <strong>' +
        esc(q) + '</strong>.</p><p>Try different terms or <a href="index.html">browse chapters</a>.</p></div>';
      return;
    }
    if (countEl) countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '');
    container.innerHTML = results.map(function (item) {
      var pages = (item.page_start && item.page_end)
        ? ' &middot; Pages ' + item.page_start + '&ndash;' + item.page_end : '';
      return '<div class="search-result">' +
        '<div class="search-result__title"><a href="chapters/' + esc(item.slug) + '.html">' + esc(item.title) + '</a></div>' +
        '<div class="search-result__meta">Chapter ' + esc(item.chapter_number) + pages + '</div>' +
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

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — HTML base template
# ══════════════════════════════════════════════════════════════════════════════

def base_html(
    *,
    title: str,
    content: str,
    breadcrumbs: list[tuple[str, str | None]],  # [(label, url|None), ...]
    depth: int = 0,          # 0 = site root, 1 = site/chapters/
    sidebar_html: str = "",
    extra_js: str = "",
) -> str:
    """Assemble a complete HTML page from parts."""
    root     = "../" * depth
    page_title = f"{h(title)} — EU Wildlife Trade Reference Guide"

    # Breadcrumb HTML
    bc_items = ""
    for i, (label, url) in enumerate(breadcrumbs):
        is_last = i == len(breadcrumbs) - 1
        if is_last:
            bc_items += f'<li><span aria-current="page">{h(label)}</span></li>\n'
        else:
            bc_items += f'<li><a href="{h(url)}">{h(label)}</a></li>\n'

    # Sidebar column (present only when sidebar_html is provided)
    if sidebar_html:
        grid_open  = '<div class="page-grid">'
        sidebar_col = f'<aside class="sidebar" aria-label="Page contents">{sidebar_html}</aside>'
        grid_close = '</div>'
        article_open  = '<div class="article">'
        article_close = '</div>'
    else:
        grid_open = grid_close = sidebar_col = ""
        article_open = article_close = ""

    return f"""<!DOCTYPE html>
<html lang="en" class="govuk-template">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <meta name="description" content="EU Wildlife Trade Regulations Reference Guide — {h(title)}">
  <link rel="stylesheet" href="{root}assets/style.css">
  <meta name="theme-color" content="#0b0c0c">
</head>
<body id="top">
<a href="#main-content" class="skip-link">Skip to main content</a>

<!-- BLACK HEADER -->
<header class="site-header" role="banner">
  <div class="site-header__inner">
    <div class="site-header__title">
      <a href="{root}index.html">EU Wildlife Trade Regulations&nbsp;&mdash; Reference Guide</a>
    </div>
    <form class="header-search" action="{root}search.html" method="get" role="search">
      <label for="header-search-input" class="skip-link">Search</label>
      <input type="search" id="header-search-input" name="q"
             placeholder="Search the guide" aria-label="Search the guide">
      <button type="submit">Search</button>
    </form>
  </div>
</header>

<!-- PHASE BANNER -->
<div class="phase-banner">
  <div class="container">
    <strong class="phase-tag">Beta</strong>
    <span>This is a new service &mdash; your <a href="mailto:wildlife.trade@ec.europa.eu">feedback</a> will help us improve it.</span>
  </div>
</div>

<!-- BREADCRUMBS -->
<nav class="breadcrumbs" aria-label="Breadcrumb">
  <ol>{bc_items}</ol>
</nav>

<!-- MAIN -->
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

<!-- FOOTER -->
<footer class="site-footer" role="contentinfo">
  <div class="container">
    <p>Published by the European Commission &mdash; DG Environment.</p>
    <p>Content extracted from the <em>Reference Guide to the European Union Wildlife Trade Regulations</em> (November 2025).</p>
    <p><a href="{root}index.html">Home</a> &middot; <a href="{root}search.html">Search</a></p>
  </div>
</footer>

<script src="{root}assets/main.js"></script>
{extra_js}
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Page builders
# ══════════════════════════════════════════════════════════════════════════════

def build_chapter_page(chapter: dict, all_chapters: list[dict]) -> str:
    """Generate the full HTML for a single chapter page."""
    rendered  = render_markdown(chapter["body"])
    headings  = extract_headings(rendered)

    # ── Contents box (desktop, top of article) ────────────────────────────
    if headings:
        items = "\n".join(
            f'<li><a href="#{h(hd["id"])}">{h(hd["text"])}</a></li>'
            for hd in headings if hd["level"] == 2
        )
        contents_box = (
            '<div class="contents-box">'
            '<p class="contents-box__title">Contents</p>'
            f'<ol>{items}</ol>'
            '</div>'
        ) if items else ""

        # Mobile dropdown (same links)
        mob_items = "\n".join(
            f'<li><a href="#{h(hd["id"])}">{h(hd["text"])}</a></li>'
            for hd in headings if hd["level"] == 2
        )
        mobile_contents = (
            '<details class="mobile-contents">'
            '<summary>Contents <span aria-hidden="true">▾</span></summary>'
            f'<div class="mobile-contents__body"><ol>{mob_items}</ol></div>'
            '</details>'
        ) if mob_items else ""
    else:
        contents_box    = ""
        mobile_contents = ""

    # ── Sidebar (desktop, sticky) ─────────────────────────────────────────
    if headings:
        nav_items = ""
        for hd in headings:
            level_class = "sidebar-h3" if hd["level"] >= 3 else ""
            nav_items += (
                f'<li class="{level_class}">'
                f'<a href="#{h(hd["id"])}">{h(hd["text"])}</a>'
                f'</li>\n'
            )
        sidebar_html = (
            f'<p class="sidebar__label">On this page</p>'
            f'<ul class="sidebar__nav">{nav_items}</ul>'
        )
    else:
        sidebar_html = ""

    # ── Chapter metadata line ─────────────────────────────────────────────
    ch   = chapter
    meta_parts = [f"Chapter {h(ch['chapter_number'])}"]
    if ch["page_start"] and ch["page_end"]:
        meta_parts.append(f"Pages {h(ch['page_start'])}–{h(ch['page_end'])}")
    if ch["has_tables"]:
        meta_parts.append('<span class="badge">Contains tables</span>')
    meta_html = f'<div class="chapter-meta">{"&ensp;&middot;&ensp;".join(meta_parts)}</div>'

    # ── Previous / Next navigation ────────────────────────────────────────
    idx  = all_chapters.index(chapter)
    prev = all_chapters[idx - 1] if idx > 0              else None
    next = all_chapters[idx + 1] if idx < len(all_chapters) - 1 else None

    nav_html = '<div class="chapter-nav">'
    if prev:
        nav_html += (
            f'<a href="{h(prev["slug"])}.html" class="chapter-nav__prev">'
            f'<span class="chapter-nav__label">Previous</span>'
            f'<span class="chapter-nav__title">{h(prev["title"])}</span>'
            f'</a>'
        )
    if next:
        nav_html += (
            f'<a href="{h(next["slug"])}.html" class="chapter-nav__next">'
            f'<span class="chapter-nav__label">Next</span>'
            f'<span class="chapter-nav__title">{h(next["title"])}</span>'
            f'</a>'
        )
    nav_html += '</div>'

    # ── Assemble article body ─────────────────────────────────────────────
    article_content = f"""
{mobile_contents}
{contents_box}
<article class="article-body">
  <h1>{h(ch['title'])}</h1>
  {meta_html}
  {rendered}
</article>
{nav_html}
<a href="#top" class="back-to-top">Back to top</a>
"""

    breadcrumbs = [
        ("Home", "../index.html"),
        (ch["title"], None),
    ]

    return base_html(
        title=ch["title"],
        content=article_content,
        breadcrumbs=breadcrumbs,
        depth=1,
        sidebar_html=sidebar_html,
    )


def build_index_page(chapters: list[dict]) -> str:
    """Generate the homepage with search bar and chapter card grid."""
    # Build chapter cards
    cards = ""
    for ch in chapters:
        pages = ""
        if ch["page_start"] and ch["page_end"]:
            pages = f"Pages {h(ch['page_start'])}–{h(ch['page_end'])}"
        badge = '<span class="badge">Tables</span>' if ch["has_tables"] else ""
        summary = h(ch["summary"]) if ch["summary"] else ""
        cards += f"""
<a class="chapter-card" href="chapters/{h(ch['slug'])}.html">
  <div class="chapter-card__num">Chapter {h(ch['chapter_number'])}</div>
  <div class="chapter-card__title">{h(ch['title'])}</div>
  <div class="chapter-card__summary">{summary}</div>
  <div class="chapter-card__foot">
    {f'<span>{pages}</span>' if pages else ''}
    {badge}
  </div>
</a>"""

    content = f"""
<div class="hero">
  <div class="container">
    <h1>EU Wildlife Trade Regulations<br>Reference Guide</h1>
    <p class="hero__lead">
      A comprehensive reference guide for EU Member State Management Authorities,
      enforcement agencies, traders, and the public on the rules governing the
      trade of wildlife into, out of, and within the European Union.
    </p>
    <form class="search-form" action="search.html" method="get" role="search">
      <label for="home-search" class="skip-link">Search</label>
      <input type="search" id="home-search" name="q"
             placeholder="Search the guide&hellip;" aria-label="Search the guide">
      <button type="submit">Search</button>
    </form>
  </div>
</div>

<div class="container" style="padding-top:30px">
  <h2 style="margin-top:0;border-top:none;padding-top:0">All chapters</h2>
  <div class="chapter-grid">{cards}</div>
</div>
"""

    return base_html(
        title="Home",
        content=content,
        breadcrumbs=[("Home", None)],
        depth=0,
    )


def build_search_page() -> str:
    """Generate the search results page."""
    content = """
<h1>Search</h1>
<div class="search-header">
  <form class="search-form" action="search.html" method="get"
        role="search" style="margin-bottom:16px">
    <label for="search-input" class="skip-link">Search</label>
    <input type="search" id="search-input" name="q"
           placeholder="Search the guide&hellip;" aria-label="Search the guide"
           autofocus>
    <button type="submit">Search</button>
  </form>
  <p class="search-count" id="search-count" aria-live="polite"></p>
</div>
<div id="search-results" aria-live="polite"></div>
<noscript>
  <p>JavaScript is required for search. Please enable it in your browser.</p>
</noscript>
"""
    return base_html(
        title="Search",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Search", None)],
        depth=0,
        extra_js='<script src="assets/search.js"></script>',
    )


def build_404_page() -> str:
    """Generate a GOV.UK-style 404 page."""
    content = """
<div style="padding:40px 0">
  <h1>Page not found</h1>
  <p>If you typed the web address, check it is correct.</p>
  <p>If you pasted the web address, check you copied the entire address.</p>
  <p>
    <a href="index.html">Go to the homepage</a> or
    <a href="search.html">search the guide</a>.
  </p>
</div>
"""
    return base_html(
        title="Page not found",
        content=content,
        breadcrumbs=[("Home", "index.html"), ("Page not found", None)],
        depth=0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Search index builder
# ══════════════════════════════════════════════════════════════════════════════

def build_search_index(chapters: list[dict]) -> list[dict]:
    """Return the list of records to be written to search_index.json."""
    return [
        {
            "slug":           ch["slug"],
            "title":          ch["title"],
            "chapter_number": ch["chapter_number"],
            "summary":        ch["summary"],
            "page_start":     ch["page_start"],
            "page_end":       ch["page_end"],
            "body":           strip_markdown(ch["body"]),
        }
        for ch in chapters
    ]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Build orchestration
# ══════════════════════════════════════════════════════════════════════════════

def build_site() -> list[dict]:
    """
    Full build:  read markdown → generate HTML → write assets.
    Returns the list of chapter dicts for reporting.
    """
    console.rule("[bold blue]Building site[/bold blue]")

    # ── Read and sort chapters ────────────────────────────────────────────
    md_files = sorted(INPUT_DIR.glob("*.md"))
    if not md_files:
        console.print(f"[red]No .md files found in {INPUT_DIR}/[/red]")
        sys.exit(1)

    chapters = [parse_md_file(p) for p in md_files]
    chapters.sort(key=lambda c: c["chapter_number"])
    console.print(f"  Loaded [bold]{len(chapters)}[/bold] chapters from [cyan]{INPUT_DIR}/[/cyan]")

    # ── Create output directories ─────────────────────────────────────────
    (SITE_DIR / "assets").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "chapters").mkdir(parents=True, exist_ok=True)

    # ── Write static assets ───────────────────────────────────────────────
    (SITE_DIR / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (SITE_DIR / "assets" / "main.js").write_text(MAIN_JS, encoding="utf-8")
    (SITE_DIR / "assets" / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    console.print("  [green]+[/green] assets/style.css, main.js, search.js")

    # ── GitHub Pages config ───────────────────────────────────────────────
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (SITE_DIR / "_config.yml").write_text(
        "# Disable Jekyll processing — site is pre-built plain HTML\ntheme: null\n",
        encoding="utf-8",
    )
    console.print("  [green]+[/green] .nojekyll, _config.yml")

    # ── Search index ──────────────────────────────────────────────────────
    index = build_search_index(chapters)
    (SITE_DIR / "search_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    console.print("  [green]+[/green] search_index.json")

    # ── Special pages ─────────────────────────────────────────────────────
    (SITE_DIR / "index.html").write_text(build_index_page(chapters), encoding="utf-8")
    (SITE_DIR / "search.html").write_text(build_search_page(), encoding="utf-8")
    (SITE_DIR / "404.html").write_text(build_404_page(), encoding="utf-8")
    console.print("  [green]+[/green] index.html, search.html, 404.html")

    # ── Chapter pages ─────────────────────────────────────────────────────
    console.print("\n  Generating chapter pages...")
    generated = []
    for ch in chapters:
        out_path = SITE_DIR / "chapters" / f"{ch['slug']}.html"
        out_path.write_text(build_chapter_page(ch, chapters), encoding="utf-8")
        generated.append(out_path)
        console.print(f"  [green]+[/green] chapters/{ch['slug']}.html")

    return chapters


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Rich report
# ══════════════════════════════════════════════════════════════════════════════

def print_report(chapters: list[dict]) -> None:
    console.print("\n")
    console.rule("[bold green]Build Report[/bold green]")

    total_words   = sum(len(ch["body"].split()) for ch in chapters)
    table_chapters = [ch for ch in chapters if ch["has_tables"]]
    all_files = (
        list((SITE_DIR / "chapters").glob("*.html"))
        + [SITE_DIR / "index.html", SITE_DIR / "search.html", SITE_DIR / "404.html"]
    )

    # Summary stats
    console.print(f"  Pages generated : [bold]{len(all_files)}[/bold] HTML files")
    console.print(f"  Total word count: [bold]{total_words:,}[/bold] words across all chapters")
    console.print(
        f"  Chapters with tables: [bold]{len(table_chapters)}[/bold] / {len(chapters)}"
    )

    # Per-chapter table
    console.print("")
    tbl = RichTable(box=box.SIMPLE_HEAD, show_lines=False, expand=False)
    tbl.add_column("Chapter file",        style="cyan",    min_width=35, max_width=58)
    tbl.add_column("Pages",               style="default", justify="center", min_width=9)
    tbl.add_column("Words",               style="default", justify="right",  min_width=7)
    tbl.add_column("Tables",              style="default", justify="center", min_width=7)

    for ch in chapters:
        fname  = f"chapters/{ch['slug']}.html"
        pages  = (f"{ch['page_start']}-{ch['page_end']}"
                  if ch["page_start"] and ch["page_end"] else "-")
        words  = f"{len(ch['body'].split()):,}"
        tables = "yes" if ch["has_tables"] else "no"
        tbl.add_row(fname, pages, words, tables)

    console.print(tbl)

    # Full file list
    console.print("\n[bold]All output files:[/bold]")
    for f in sorted(all_files):
        rel = f.relative_to(SITE_DIR)
        console.print(f"  site/{rel}")
    console.print(f"\n  + site/assets/style.css")
    console.print(f"  + site/assets/main.js")
    console.print(f"  + site/assets/search.js")
    console.print(f"  + site/search_index.json")
    console.print(f"  + site/.nojekyll")
    console.print(f"  + site/_config.yml")

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Static site written to [cyan]{SITE_DIR}/[/cyan]"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    console.rule(
        "[bold blue]EU Wildlife Trade Reference Guide — Static Site Builder[/bold blue]"
    )

    if not INPUT_DIR.exists():
        console.print(f"[red]Error: {INPUT_DIR}/ not found. Run extract_content.py first.[/red]")
        sys.exit(1)

    # Clean previous build
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
        console.print(f"  Removed previous [cyan]{SITE_DIR}/[/cyan]")

    chapters = build_site()
    print_report(chapters)


if __name__ == "__main__":
    main()
