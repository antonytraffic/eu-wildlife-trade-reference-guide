"""
check_links.py -- check every internal link in the built site and its PDF.

Run after a build, before publishing:
    python check_links.py              (checks docs/)
    python check_links.py <folder>     (checks another built copy of the site)

Site: every link, script, stylesheet and image on every page must point to a
file that exists, and every "#fragment" to an id on that page. Ids must be
unique within a page.

PDF: every internal link must point to a destination that exists. WeasyPrint
drops a link whose target is missing *without any warning*, so the script also
prints how many internal links the PDF has. A big drop after an edit means
cross-references have stopped resolving. --min-pdf-links N turns that into a
failure.

Exit status: 0 if everything resolves, 1 if anything is broken.
"""

import argparse
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

PDF_FILENAME = "EU-Wildlife-Trade-Reference-Guide.pdf"

# Attributes that point at another file or anchor.
LINK_ATTRS = {("a", "href"), ("link", "href"), ("script", "src"), ("img", "src"),
              ("source", "src"), ("iframe", "src")}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.links: list[tuple[str, str]] = []   # (tag, url)

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
        if tag == "a" and d.get("name"):
            self.ids.append(d["name"])
        for t, attr in LINK_ATTRS:
            if tag == t and d.get(attr):
                self.links.append((tag, d[attr]))


def _is_external(url: str) -> bool:
    parts = urlsplit(url)
    return bool(parts.scheme or parts.netloc) or url.startswith(("mailto:", "tel:", "javascript:", "data:"))


def check_site(site: Path) -> tuple[int, list[str], list[str]]:
    """Returns (links checked, errors, warnings)."""
    pages = sorted(site.rglob("*.html"))
    parsed: dict[Path, _PageParser] = {}
    for page in pages:
        p = _PageParser()
        p.feed(page.read_text(encoding="utf-8"))
        parsed[page.resolve()] = p

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    for page in pages:
        rel = page.relative_to(site).as_posix()
        p = parsed[page.resolve()]
        for dup, n in Counter(p.ids).items():
            if n > 1:
                errors.append(f"{rel}: id \"{dup}\" is used {n} times")
        for tag, url in p.links:
            if _is_external(url):
                continue
            checked += 1
            if url.startswith("/"):
                warnings.append(f"{rel}: <{tag}> \"{url}\" is relative to the server root, so not checked")
                continue
            parts = urlsplit(url)
            target = page if not parts.path else (page.parent / unquote(parts.path))
            if parts.path.endswith("/"):
                target = target / "index.html"
            target = target.resolve()
            if not target.exists():
                errors.append(f"{rel}: <{tag}> \"{url}\" -- file not found")
                continue
            frag = unquote(parts.fragment)
            if frag and frag != "top" and target.suffix == ".html":
                if frag not in parsed.get(target, _PageParser()).ids:
                    errors.append(f"{rel}: <{tag}> \"{url}\" -- no id \"{frag}\" on that page")
    return checked, errors, warnings


def check_pdf(pdf: Path) -> tuple[int, list[str]]:
    """Returns (internal links found, errors)."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject

    reader = PdfReader(str(pdf))
    names = set(reader.named_destinations)
    page_ids = {page.indirect_reference.idnum for page in reader.pages}
    count = 0
    errors: list[str] = []
    for page_no, page in enumerate(reader.pages, start=1):
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            action = annot.get("/A")
            action = action.get_object() if action is not None else None
            if action is not None and action.get("/S") == "/URI":
                continue                                   # external web link
            dest = annot.get("/Dest")
            if dest is None and action is not None and action.get("/S") == "/GoTo":
                dest = action.get("/D")
            if dest is None:
                continue
            dest = dest.get_object() if hasattr(dest, "get_object") else dest
            count += 1
            if isinstance(dest, ArrayObject):
                target = dest[0]
                if getattr(target, "idnum", None) not in page_ids:
                    errors.append(f"PDF page {page_no}: link to a page that doesn't exist")
            elif str(dest) not in names:
                errors.append(f"PDF page {page_no}: link to missing destination \"{dest}\"")
    return count, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Check internal links in the built site and PDF.")
    ap.add_argument("site", nargs="?", default="docs", help="built site folder (default: docs)")
    ap.add_argument("--min-pdf-links", type=int, default=0,
                    help="fail if the PDF has fewer internal links than this")
    args = ap.parse_args()

    site = Path(args.site)
    if not (site / "index.html").exists():
        print(f"No built site found in {site}/ -- run the build first.")
        return 1

    checked, errors, warnings = check_site(site)
    print(f"Site: {checked:,} internal links checked, {len(errors)} broken.")

    pdf = site / PDF_FILENAME
    if pdf.exists():
        pdf_links, pdf_errors = check_pdf(pdf)
        print(f"PDF:  {pdf_links:,} internal links, {len(pdf_errors)} broken.")
        errors += pdf_errors
        if pdf_links < args.min_pdf_links:
            errors.append(f"PDF has only {pdf_links} internal links (expected at least "
                          f"{args.min_pdf_links}) -- cross-references may have stopped resolving")
    else:
        errors.append(f"{PDF_FILENAME} is missing -- the PDF wasn't built")

    for w in warnings:
        print(f"  note: {w}")
    for e in errors:
        print(f"  BROKEN: {e}")
    print("OK -- all links resolve." if not errors else f"{len(errors)} problem(s) found.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
