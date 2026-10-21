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

External links (optional, for routine maintenance):
    python check_links.py --external
also visits every link to another website (EUR-Lex, CITES, Species+ ...) and
lists the ones that fail, in two groups: "possibly broken" (not found, moved,
no answer) and "refused" (sites such as cites.org that block automated checks,
which are usually fine). Some web applications (e.g. CIRCABC) only answer
properly in a browser, so confirm a "possibly broken" link by opening it before
changing anything. These results don't change the exit status.

Exit status: 0 if every internal link resolves, 1 if anything is broken.
"""

import argparse
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
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


def _fetch_status(url: str) -> str:
    """'' if the address answers normally, else a short reason."""
    headers = {"User-Agent": "Mozilla/5.0 (link check; EU Wildlife Trade Reference Guide)"}
    for method in ("HEAD", "GET"):          # some servers reject HEAD
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=20):
                return ""
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 501):
                continue
            return f"HTTP {e.code}"
        except Exception as e:                # timeouts, DNS, TLS ...
            if method == "HEAD":
                continue
            return type(e).__name__
    return "no answer"


def check_external(site: Path) -> tuple[int, list[str]]:
    """Visit every external http(s) link once. Returns (addresses, problems)."""
    where: dict[str, list[str]] = defaultdict(list)
    for page in sorted(site.rglob("*.html")):
        p = _PageParser()
        p.feed(page.read_text(encoding="utf-8"))
        for tag, url in p.links:
            if tag == "a" and urlsplit(url).scheme in ("http", "https"):
                where[url.split("#")[0]].append(page.relative_to(site).as_posix())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(where, pool.map(_fetch_status, where)))
    problems = []
    for url, reason in sorted(results.items()):
        if reason:
            pages = sorted(set(where[url]))
            shown = ", ".join(pages[:3]) + (f" and {len(pages) - 3} more" if len(pages) > 3 else "")
            problems.append(f"{reason}: {url}  (on {shown})")
    return len(where), problems


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
    ap.add_argument("--external", action="store_true",
                    help="also check links to other websites (slower; needs internet)")
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

    if args.external:
        print("\nChecking links to other websites...")
        n, problems = check_external(site)
        refused = [p for p in problems if p.split(":", 1)[0] in ("HTTP 401", "HTTP 403", "HTTP 429")]
        likely = [p for p in problems if p not in refused]
        print(f"External: {n:,} addresses checked.")
        print(f"\n{len(likely)} possibly broken -- open each in a browser to confirm, then fix or remove:")
        for pr in likely:
            print(f"  CHECK: {pr}")
        print(f"\n{len(refused)} on sites that refuse automated checks -- usually fine; spot-check a few by hand:")
        for pr in refused:
            print(f"  REFUSED: {pr}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
