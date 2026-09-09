# ECL Shell Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the site's hand-rolled EC-lookalike chrome with the real Europa Component Library v5.1.0 components, leaving the article body's tuned styling untouched and both contested decisions behind revertible flags.

**Architecture:** `build_site.py` is a single-file static generator that wipes and regenerates `docs/`. The chrome currently lives in one ~140-line f-string (`base_html()`, line 1840); it gets extracted into six named functions behind an `ECL_CHROME` flag, then converted region by region. A `CONTENT_CLASSES` lookup table is introduced so the declined "full ECL adoption" of the article body becomes a flag flip later rather than a rewrite. Card markup already funnels through two helpers, so `ECL_CARDS` needs no restructuring.

**Tech Stack:** Python 3 (`markdown2`, `weasyprint`, `rich`), vendored ECL `@ecl/preset-ec` v5.1.0 (CSS + UMD JS), no build tooling or package manager for front-end assets.

**Spec:** [ECL-Migration-Spec.md](ECL-Migration-Spec.md)

## Global Constraints

- **ECL version is exactly `@ecl/preset-ec` v5.1.0**, built 2026-07-23. Do not mix in assets from any other release.
- **`ecl-reset.css` is NOT loaded.** Spec §5. Stylesheet link order is exactly: `ecl-ec-default.css` → `ecl-ec.css` → `ecl-ec-utilities.css` → `assets/style.css`.
- **Print and PDF are out of scope.** Do not modify the `@media print` block (`build_site.py:1573`) or `PDF_CSS` (`build_site.py:2603`). Do not ship `ecl-ec-print.css`.
- **Article body output must not change** until `ECL_CONTENT` is deliberately enabled, which is out of scope for this plan. `git diff -- docs/chapters/` is the gate.
- **Verification harness:** run the build, then `git diff --stat -- docs/ ':(exclude)docs/EU-Wildlife-Trade-Reference-Guide.pdf'`. The build is deterministic (`generate_summaries()` reads `output/_summaries.json`, never calls an API) — a clean checkout must diff empty except for the PDF. The PDF is excluded because WeasyPrint embeds a creation timestamp.
- **How to actually run the build (PowerShell, not bash) — WeasyPrint's DLL loading is order-sensitive:** a plain `python build_site.py` from a bash PATH-prepend resolves a stale, incompatible `libgobject-2.0-0.dll` from `C:\Program Files\Inkscape\bin` ahead of the MSYS2 one, fails to import WeasyPrint, and *silently skips PDF generation* — which then falsely shows as a regression (`docs/EU-Wildlife-Trade-Reference-Guide.pdf` and `docs/assets/images/logo-ec-header.svg` disappearing from the diff). Always build via PowerShell, replicating `build.bat`'s exact PATH order:
  ```powershell
  $MainVenv = "D:\OneDrive - Traffic International\EC European Commission\Digitalisation\Claude Code\EU Reference Guide Website\venv"
  $MSYS2_BIN = "D:\Apps\MSYS2\mingw64\bin"
  $env:VIRTUAL_ENV = $MainVenv
  $env:PATH = "$MainVenv\Scripts;$MSYS2_BIN;$env:PATH"
  $env:WEASYPRINT_DLL_DIRECTORIES = $MSYS2_BIN
  python build_site.py
  ```
  This worktree has no local `venv/` (git-ignored) — the main checkout's venv is reused via absolute path; no dependency install is needed. If a build ever shows the PDF and `logo-ec-header.svg` both missing from the diff and nothing in this plan should have touched either, re-run with the invocation above before concluding anything regressed.
- **How to preview the site for visual/browser verification — do not use `preview_start` with the `docs-site` launch.json config.** It reliably serves stale or wrong content for this worktree (confirmed independently twice: it served pre-Task-4 markup — no `assets/ecl/*` requests at all — even immediately after a rebuild, and even after editing `launch.json`'s directory to an absolute worktree path and changing its port). The cause wasn't isolated further since it's a tooling quirk, not a project bug, and isolating it further wasn't worth the time against the actual migration work. Instead, start a plain server directly and point the Browser pane at it:
  ```bash
  python -m http.server 8199 --directory docs
  ```
  Run this via the Bash tool with `run_in_background: true` (a `&`-backgrounded process inside a foreground Bash call dies when the call returns — it must use the tool's own background flag), then use `tabs_create`/`navigate` to open `http://localhost:8199` in the Browser pane. Pick a port unlikely to collide with anything else already running. Verified working: `assets/ecl/*` requests return 200 and the served HTML matches the current worktree's `docs/` exactly.
- **Never write planning or scratch files under `docs/`** — it is the published GitHub Pages output and `main()` deletes it on every build.
- **Flag defaults:** `ECL_CHROME = True`, `ECL_CARDS = True`, `ECL_CONTENT = False`.
- **`brand/ecl/quick_example.html` must survive until Task 10 completes** — it is the only source of the footer, globan and header markup.
- Commit after every task. Do not push.

---

### Task 0: Establish the baseline

No production code changes. This task builds the harness every later task depends on. Skipping it makes every subsequent gate meaningless.

**Files:**
- Create: `brand/baseline/` (screenshots; git-ignored)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: a confirmed-clean `docs/` baseline and reference screenshots at `brand/baseline/*.png`.

- [ ] **Step 1: Confirm the working tree is clean before touching anything**

```bash
git status --porcelain
```

Expected: only `?? brand/example_template/` and the two new `brand/ECL-Migration-*.md` files. If `docs/` shows modifications, **stop** — resolve that first, because the harness assumes a clean baseline.

- [ ] **Step 2: Prove the build is deterministic (this is the failing-test equivalent)**

```bash
python build_site.py
git diff --stat -- docs/ ':(exclude)docs/EU-Wildlife-Trade-Reference-Guide.pdf'
```

Expected: **empty output.** If any HTML/CSS/JS file differs, the harness is invalid — investigate before proceeding. A modified PDF is expected and correct.

- [ ] **Step 3: Ignore the baseline screenshot directory**

Append to `.gitignore`:

```
# Baseline screenshots for the ECL migration (local only)
brand/baseline/
```

- [ ] **Step 4: Capture baseline screenshots**

Serve `docs/` in the browser preview and capture, at desktop width and at 375 px, saving into `brand/baseline/`:

- `index.html` → `home-desktop.png`, `home-mobile.png`
- `chapters/03_2_what_document_for_what_purpose.html` → `article-desktop.png`, `article-mobile.png` (dense text, tables, cross-references)
- `chapters/03_what_are_the_rules_governing_trade_into_and_from_the_eu_fo.html` → `landing-desktop.png` (subpage card grid)
- `about.html` → `about-desktop.png`
- `search.html` → `search-desktop.png`

These are the reference for spec §8.7. The article shots matter most — they are the evidence that omitting `ecl-reset.css` was safe.

- [ ] **Step 5: Record the PDF's page count for later comparison**

```bash
python -c "import pypdf; print(len(pypdf.PdfReader('docs/EU-Wildlife-Trade-Reference-Guide.pdf').pages))"
```

Write the number into `brand/baseline/pdf-pages.txt`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore brand/ECL-Migration-Spec.md brand/ECL-Migration-Plan.md
git commit -m "docs: add ECL migration spec and plan, ignore baseline screenshots"
```

---

### Task 1: Vendor the ECL asset subset

**Files:**
- Rename: `brand/example_template/` → `brand/ecl/`
- Delete: `brand/ecl/settings.svg`
- Modify: `build_site.py` — the static-assets block inside `build_site()`, immediately after the `assets/fonts/InterVariable.woff2` copy (currently around line 3380)

**Interfaces:**
- Consumes: nothing.
- Produces: `copy_ecl_assets(dst: Path) -> int` returning the number of files copied; ECL files present under `docs/assets/ecl/`.

- [ ] **Step 1: Rename the folder and drop the stray demo asset**

```bash
git mv brand/example_template brand/ecl 2>/dev/null || mv brand/example_template brand/ecl
rm brand/ecl/settings.svg
rm -f brand/ecl/styles/*.css.map brand/ecl/styles/optional/*.css.map
```

`git mv` fails if the folder is still untracked, hence the fallback. Do **not** delete `quick_example.html` — Global Constraints.

- [ ] **Step 2: Write the assertion, and confirm it currently fails**

```bash
test -f docs/assets/ecl/styles/ecl-ec.css && echo PRESENT || echo ABSENT
```

Expected now: `ABSENT`.

- [ ] **Step 3: Add the copy helper**

Insert above `def build_site()` in `build_site.py`:

```python
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
        shutil.copy2(src, target)
        copied += 1
    return copied
```

- [ ] **Step 4: Call it from the static-assets block**

In `build_site()`, directly after the `assets/fonts/InterVariable.woff2` copy block, add:

```python
    _ecl_n = copy_ecl_assets(SITE_DIR / "assets" / "ecl")
    console.print(f"  [green]+[/green] assets/ecl/ ({_ecl_n} ECL v5.1.0 files)")
```

- [ ] **Step 5: Build and verify the assertion now passes**

```bash
python build_site.py
test -f docs/assets/ecl/styles/ecl-ec.css && echo PRESENT || echo ABSENT
ls docs/assets/ecl/styles docs/assets/ecl/scripts docs/assets/ecl/fonts docs/assets/ecl/images/logo
```

Expected: `PRESENT`, nine files total, and the console line reporting `(9 ECL v5.1.0 files)` with no `missing ECL asset` warnings.

- [ ] **Step 6: Verify no page output changed**

```bash
git diff --stat -- docs/ ':(exclude)docs/EU-Wildlife-Trade-Reference-Guide.pdf' ':(exclude)docs/assets/ecl'
```

Expected: **empty.** Assets were added; no HTML was touched yet.

- [ ] **Step 7: Commit**

```bash
git add brand/ecl build_site.py docs/assets/ecl
git commit -m "feat: vendor ECL v5.1.0 asset subset into docs/assets/ecl"
```

---

### Task 2: Add the `CONTENT_CLASSES` indirection

Spec §5.1.2. Pure refactor — output must be byte-identical. This is the cheapest possible insurance against the article-body decision being reversed.

**Files:**
- Modify: `build_site.py` — new constants near the config block (line ~36); `_postprocess_tables()` (line 283); `render_markdown()` (line 527)

**Interfaces:**
- Consumes: nothing.
- Produces: `ECL_CONTENT: bool`; `cc(key: str) -> str` returning a class name or `""`; `cc_attr(key: str) -> str` returning ` class="…"` or `""`.

- [ ] **Step 1: Write the assertion, and confirm it currently fails**

```bash
python -c "import build_site; print(build_site.cc('h2'))" 2>&1 | tail -1
```

Expected now: `AttributeError: module 'build_site' has no attribute 'cc'`.

- [ ] **Step 2: Add the flag, the map and the accessors**

Insert after the `ABOUT_TITLE` config line (~line 33):

```python
# -- Reversibility flags -- see brand/ECL-Migration-Spec.md section 5.1 --------
ECL_CHROME  = True    # False: Drupal / oe_theme supplies globan, header, footer
ECL_CARDS   = True    # False: revert to the bespoke chapter/subpage card design
ECL_CONTENT = False   # True:  article body adopts ECL type/table classes

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
```

- [ ] **Step 3: Route the table wrapper through the map**

In `_postprocess_tables()` (line 283), the wrapper currently hard-codes `table-wrap`. Replace the literal with `cc("table_wrap")`. Locate it first:

```bash
grep -n 'table-wrap' build_site.py
```

Change only occurrences inside `_postprocess_tables()` that build the wrapper `<div>`. **Leave the `table-wrap` references inside `CSS` and `PDF_CSS` alone** — those are stylesheet rules, not generated markup.

- [ ] **Step 4: Emit `data-ecl-table-header` on every `<td>` unconditionally**

Spec §5.1.2 caveat: the attribute is inert without ECL's table CSS, so emitting it now means enabling `ECL_CONTENT` later never requires reopening table generation. In `_postprocess_tables()`, where cells are rewritten, ensure each `<td>` carries `data-ecl-table-header=""`.

This **will** change `docs/chapters/` output — the one intentional exception in this task. It is a no-op visually and inert to the existing CSS.

- [ ] **Step 5: Build and verify the change is confined to `<td>` attributes**

Build, then verify by stripping the new attribute from the new content and comparing against the old committed content — **not** by counting unmatched diff lines. (A naive `git diff | grep -vc 'data-ecl-table-header'` looks tempting but is not a valid gate: every removed `-` line lacks the new attribute by definition, so it always reports a large nonzero count regardless of correctness — this was caught during implementation and is recorded here so it isn't rediscovered.)

```bash
python build_site.py
```

For every file `git diff --name-only -- docs/chapters/` lists, confirm that stripping every ` data-ecl-table-header=""` occurrence (globally, not just the first per line — some lines carry several `<td>`s) from the new content reproduces `git show HEAD:<file>` exactly, and that the count of `<td` occurrences in old and new match the count of the new attribute. Expected: identical after stripping, for every changed file, with zero files that contain `<td` left unchanged.

- [ ] **Step 6: Verify the accessors work and the default is off**

```bash
python -c "import build_site as b; print(repr(b.cc('h2')), repr(b.cc_attr('h2')), b.ECL_CONTENT)"
```

Expected: `'' '' False`.

- [ ] **Step 7: Confirm the article renders unchanged**

Serve `docs/`, screenshot `chapters/03_2_what_document_for_what_purpose.html` at desktop width, and compare against `brand/baseline/article-desktop.png`. Tables must be visually identical.

- [ ] **Step 8: Commit**

```bash
git add build_site.py docs/
git commit -m "refactor: route article-body classes through CONTENT_CLASSES map"
```

---

### Task 3: Extract the chrome into named functions

Spec §4. Pure refactor — every byte of `docs/` output must stay identical. Splitting this from Task 4 is deliberate: it isolates a large mechanical change so that when real markup changes land, the diff is readable.

**Files:**
- Modify: `build_site.py` — `base_html()` (line 1840, through ~line 2035)

**Interfaces:**
- Consumes: `ECL_CHROME` from Task 2.
- Produces: `ecl_head_links(root: str) -> str`, `ecl_globan() -> str`, `ecl_site_header(root: str) -> str`, `ecl_breadcrumb(bc_items: str) -> str`, `ecl_site_footer(root: str) -> str`, `ecl_scripts(root: str) -> str`. Each returns an HTML fragment; each returns `""` when `ECL_CHROME` is `False`, except `ecl_head_links()` which always returns the site's own stylesheet link.

- [ ] **Step 1: Snapshot the current output as the reference**

```bash
python build_site.py
cp docs/index.html /tmp/ecl-ref-index.html
cp docs/chapters/03_2_what_document_for_what_purpose.html /tmp/ecl-ref-article.html
```

- [ ] **Step 2: Read the whole function before cutting it up**

```bash
sed -n '1840,2035p' build_site.py
```

Note every f-string interpolation it uses — `root`, `title`, `page_title`, `bc_items`, `page_header_html`, `grid_open`, `sidebar_col`, `article_open`, `content`, `guide_dropdown_items`, `annexes_href`, `footer_extra`, and **`extra_js`**. Each extracted function must receive exactly what it needs and nothing more.

**`extra_js` needs care.** It is a `base_html()` parameter (default `""`), used by exactly one caller — `build_search_page()` passes `extra_js='<script src="assets/search.js"></script>'` to load `search.js` only on `search.html`. It is interpolated on its own line, between `<script src="{root}assets/main.js">` and `<script src="{root}assets/chatbot.js">`. `ecl_scripts()` must **not** swallow this into a fixed string — keep `{extra_js}` as its own separate interpolation slot in `base_html()`'s template, positioned before the single call to `ecl_scripts()` (see Step 4's exact ordering), so `search.html` keeps loading `search.js`. Do not add `extra_js` as a parameter to `ecl_scripts()` — the function extracted in this task never needs page-specific script content, and folding it in would let a later task's edits to `ecl_scripts()` silently break `search.html`.

- [ ] **Step 3: Extract the six functions, emitting the CURRENT markup verbatim**

Insert above `def base_html(` a section like the following. Copy the existing markup across **unchanged** — this task changes structure, not output.

```python
# ==============================================================================
# Site chrome -- see brand/ECL-Migration-Spec.md section 4.
# Each function returns "" when ECL_CHROME is False, so a move into Drupal /
# oe_theme (which supplies its own chrome) is one flag rather than a rewrite.
# ==============================================================================

def ecl_head_links(root: str) -> str:
    """Stylesheet, font and favicon links for <head>."""
    # Task 4 replaces the body of this function with the ECL link order.
    return (
        f'  <link rel="stylesheet" href="{root}assets/style.css">\n'
        f'  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg">\n'
        f'  <link rel="icon" type="image/x-icon" href="{root}favicon.ico">\n'
        f'  <link rel="apple-touch-icon" href="{root}apple-touch-icon.png">\n'
        f'  <meta name="theme-color" content="#00002e">'
    )


def ecl_globan() -> str:
    """Official-status strip. Task 5 replaces this with the webtools globan."""
    if not ECL_CHROME:
        return ""
    return CURRENT_OFFICIAL_STRIP_HTML   # move the existing block here verbatim


def ecl_site_header(root: str) -> str:
    if not ECL_CHROME:
        return ""
    return CURRENT_HEADER_AND_TOPNAV_HTML   # existing <header> + <nav class="top-nav">


def ecl_breadcrumb(bc_items: str) -> str:
    if not ECL_CHROME or not bc_items:
        return ""
    return f'<nav class="breadcrumbs" aria-label="Breadcrumb"><ol>{bc_items}</ol></nav>'


def ecl_site_footer(root: str) -> str:
    if not ECL_CHROME:
        return ""
    return CURRENT_FOOTER_HTML   # move the existing <footer> here verbatim


def ecl_scripts(root: str) -> str:
    """Scripts after {extra_js}, before </body>. Task 4 adds ecl-ec.js + ECL.autoInit();
    Task 5 adds the webtools loader. Deliberately excludes assets/main.js and {extra_js}
    itself -- both stay as separate lines in base_html()'s own template, in their
    original position, so search.html keeps loading assets/search.js. See Step 2's
    note on extra_js."""
    return CURRENT_TAIL_SCRIPT_TAGS_HTML   # existing chatbot.js tag + its init <script>
```

The `CURRENT_*` names above are placeholders for *the markup you are moving*, not variables to define — paste the actual existing HTML into each function body as an f-string. Keep the `top-nav` dropdown inside `ecl_site_header()` for now; Task 6 separates it.

- [ ] **Step 4: Reduce `base_html()` to composition**

`base_html()`'s template becomes a thin assembly of the six fragments plus the existing content/grid interpolations. The script region keeps three parts in their original order: `<script src="{root}assets/main.js"></script>`, then `{extra_js}`, then `{ecl_scripts(root)}`. Leave whitespace exactly as it was, so the diff stays empty.

- [ ] **Step 5: Build and verify byte-identical output — the gate for this task**

```bash
python build_site.py
diff /tmp/ecl-ref-index.html docs/index.html && echo "INDEX IDENTICAL"
diff /tmp/ecl-ref-article.html docs/chapters/03_2_what_document_for_what_purpose.html && echo "ARTICLE IDENTICAL"
git diff --stat -- docs/ ':(exclude)docs/EU-Wildlife-Trade-Reference-Guide.pdf'
```

Expected: both `IDENTICAL` lines, and an **empty** `git diff --stat`. If anything differs it is almost always stray whitespace from the extraction — fix it rather than accepting the diff.

- [ ] **Step 6: Verify the `ECL_CHROME = False` path does not crash**

```bash
python -c "
import build_site as b
b.ECL_CHROME = False
print('globan:', repr(b.ecl_globan()))
print('header:', repr(b.ecl_site_header('')))
print('footer:', repr(b.ecl_site_footer('')))
"
```

Expected: three empty strings. Do not commit with the flag set to `False`.

- [ ] **Step 7: Commit**

```bash
git add build_site.py
git commit -m "refactor: extract site chrome into ecl_* functions behind ECL_CHROME"
```

---

### Task 4: Swap the asset stack and head plumbing

Spec §6.1. First task with visible output changes.

**Files:**
- Modify: `build_site.py` — `ecl_head_links()`, `ecl_scripts()`, the `<html>` tag, and the meta block in `base_html()`'s own template (not inside `ecl_head_links()` — see Step 5)

**Interfaces:**
- Consumes: `ecl_head_links()`, `ecl_scripts()` from Task 3; `docs/assets/ecl/` from Task 1.
- Produces: unchanged signatures, ECL-aware bodies.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'assets/ecl/styles/ecl-ec.css' docs/index.html
grep -c 'ECL.autoInit' docs/index.html
grep -c 'class="no-js"' docs/index.html
```

Expected now: `0`, `0`, `0`.

- [ ] **Step 2: Replace `ecl_head_links()` with the ECL link order**

Order is fixed by Global Constraints — `ecl-reset.css` is absent by design.

```python
def ecl_head_links(root: str) -> str:
    e = f"{root}assets/ecl"
    return (
        f'  <link rel="stylesheet" href="{e}/styles/optional/ecl-ec-default.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{e}/styles/ecl-ec.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{e}/styles/optional/ecl-ec-utilities.css" media="screen">\n'
        f'  <link rel="stylesheet" href="{root}assets/style.css">\n'
        f'  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg">\n'
        f'  <link rel="icon" type="image/x-icon" href="{root}favicon.ico">\n'
        f'  <link rel="apple-touch-icon" href="{root}apple-touch-icon.png">'
    )
```

`<meta name="theme-color">` is dropped — not an ECL convention (spec §6.1).

- [ ] **Step 3: Add the `no-js` → `has-js` swap**

In `base_html()`, change `<html lang="en">` to `<html lang="en" class="no-js">` and add this as the **first** element in `<head>`, before the stylesheet links, so it runs before paint:

```html
  <script>
    var cl = document.querySelector('html').classList;
    cl.remove('no-js');
    cl.add('has-js');
  </script>
```

- [ ] **Step 4: Load the ECL bundle in `ecl_scripts()`**

Append to the existing script tags, keeping `main.js` / `search.js` / `chatbot.js` as they are:

```python
    f'<script src="{root}assets/ecl/scripts/ecl-ec.js"></script>\n'
    f'<script>ECL.autoInit();</script>\n'
```

`ecl-ec.js` must load **before** the `autoInit()` call and after any markup it initialises.

- [ ] **Step 5: Add the Open Graph and Twitter card meta block**

Spec §6.1. This step edits `base_html()` directly (not `ecl_head_links()`) — `base_html()` currently *composes* its description inline with no standalone variable:

```python
  <meta name="description" content="EU Wildlife Trade Regulations Reference Guide -- {h(title)}">
```

First factor that string out to a local variable, in `base_html()`'s body, just above its `return f"""..."""`:

```python
    description = f"EU Wildlife Trade Regulations Reference Guide -- {h(title)}"
```

Then change the existing meta line to use it: `<meta name="description" content="{description}">`. Immediately after that line, add:

```python
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="EU Wildlife Trade Reference Guide">
  <meta property="og:title" content="{page_title}">
  <meta property="og:description" content="{description}">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{page_title}">
  <meta name="twitter:description" content="{description}">
```

`page_title` and `title` are both already in scope as `base_html()` locals/parameters (`page_title = f"{h(title)} -- EU Wildlife Trade Reference Guide"`, defined earlier in the function) — use `page_title` for the og/twitter title fields since it already carries the full page-specific title `h()`-escaped; `description` is already escaped via the `h(title)` inside it. Do **not** copy the template's `og:url` or `og:image` values — they point at DG ENV's own pages and fallback image (spec §1.1). Omit `og:image` entirely until an asset exists for this guide; a broken image reference is worse than none.

- [ ] **Step 6: Point `@font-face` at the ECL fonts and add the italic**

In `CSS`, find the `@font-face` block and repoint `src` to `ecl/fonts/InterVariable.woff2` (**not** `assets/ecl/fonts/...`). `CSS` is written to `docs/assets/style.css`, and its `url()` values are relative to that file's own location, not the page — confirmed by the existing `background-image: url("images/hero-elephants-line.jpg")` rule a few hundred lines down, which resolves to `docs/assets/images/...`. `assets/ecl/fonts/...` would resolve to the nonexistent `docs/assets/assets/ecl/fonts/...` and silently 404 the font (no build-time signal — the diff-empty check doesn't validate resource resolution). Then add a second `@font-face` block for `ecl/fonts/InterVariable-Italic.woff2` with `font-style: italic`. Then remove the now-redundant copy step:

Delete the `static/fonts/InterVariable.woff2` copy block in `build_site()` (the `_font_src` lines, ~line 3383) and remove `docs/assets/fonts/` from the tree.

- [ ] **Step 7: Build and verify the assertions now pass**

```bash
python build_site.py
grep -c 'assets/ecl/styles/ecl-ec.css' docs/index.html
grep -c 'ECL.autoInit' docs/index.html
grep -c 'class="no-js"' docs/index.html
grep -c 'theme-color' docs/index.html
test -d docs/assets/fonts && echo "STALE FONTS DIR" || echo "fonts deduped"
```

Expected: `1`, `1`, `1`, `0`, `fonts deduped`.

- [ ] **Step 8: Verify nothing broke visually — the key check for spec §5**

Serve `docs/` and confirm:
- Browser console shows **no errors** (`read_console_messages`). ECL's JS will warn about missing components; errors are the failure signal.
- `chapters/03_2_what_document_for_what_purpose.html` at desktop matches `brand/baseline/article-desktop.png`.
- Body text still renders in Inter, and *italic* text is genuinely italic rather than synthesised.

If the article body has visibly shifted, ECL's cascade is reaching content it shouldn't — record exactly what changed and add a scoped override under `.article` rather than loading `ecl-reset.css`.

- [ ] **Step 9: Commit**

```bash
git add build_site.py docs/
git commit -m "feat: load ECL stylesheet stack, JS bundle and fonts"
```

---

### Task 5: Replace the hand-drawn official strip with the webtools globan

Spec §6.2.

**Files:**
- Modify: `build_site.py` — `ecl_globan()`, `ecl_scripts()`, and the `.official-strip` rules in `CSS` (line ~920)

**Interfaces:**
- Consumes: `ecl_globan()`, `ecl_scripts()` from Task 3.
- Produces: unchanged signatures.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'official-strip' docs/index.html
grep -c 'globan' docs/index.html
grep -c 'cck_here' docs/index.html
```

Expected now: `3` or more, `0`, `0`.

- [ ] **Step 2: Replace the body of `ecl_globan()`**

```python
def ecl_globan() -> str:
    if not ECL_CHROME:
        return ""
    return (
        '<div id="cck_here"><!-- cookie consent --></div>\n'
        '<script type="application/json">'
        '{"utility":"globan","theme":"light","logo":true,"link":true,"mode":false}'
        '</script>'
    )
```

The hand-coded 12-star flag SVG and the wording "A reference guide developed by the European Commission and TRAFFIC" are deleted outright. TRAFFIC attribution reappears in the footer in Task 10 — do not leave it stranded.

- [ ] **Step 3: Add the webtools loader and cookie-consent utility to `ecl_scripts()`**

```python
    '<script defer src="https://webtools.europa.eu/load.js"></script>\n'
    '<script type="application/json">{"utility":"cck"}</script>\n'
```

- [ ] **Step 4: Remove the dead `.official-strip` CSS**

```bash
grep -n 'official-strip' build_site.py
```

Delete the `.official-strip`, `.official-strip__inner` and `.official-strip__flag` rules from `CSS`, and remove `.official-strip` from the `@media print` hide list (line ~1574). That print edit is a **deletion of a now-dead selector**, not a print-behaviour change, so it does not breach the print constraint.

- [ ] **Step 5: Build and verify the assertions now pass**

```bash
python build_site.py
grep -c 'official-strip' docs/index.html docs/assets/style.css
grep -c 'globan' docs/index.html
grep -c 'webtools.europa.eu/load.js' docs/index.html
```

Expected: `0` in both files, then `1`, `1`.

- [ ] **Step 6: Verify, and expect the documented limitation**

Serve `docs/` and screenshot the top of `index.html`. **Expect a blank gap where the globan should be** — spec §8.1: webtools is injected at runtime and will not render off a europa.eu origin. Confirm the page below it is undisturbed and the console shows no *page* errors. A blocked/failed request to `webtools.europa.eu` is expected and is not a defect.

- [ ] **Step 7: Commit**

```bash
git add build_site.py docs/
git commit -m "feat: replace hand-drawn EU strip with webtools globan and cookie consent"
```

---

### Task 6: Convert the site header, logo, search and main navigation

Spec §6.3 and §7.1. The largest markup task. The mega menu and search markup are **not in the supplied folder** and must be fetched first.

**Files:**
- Modify: `build_site.py` — `ecl_site_header()`; `.site-header*`, `.header-search`, `.top-nav*` rules in `CSS`
- Modify: `build_site.py` — `SITE_NAME_BANNER` constant added in Task 2's flag block

**Interfaces:**
- Consumes: `ecl_site_header(root)` from Task 3; `_GUIDE_NAV_ITEMS` and `_ANNEXES_SLUG` module globals (set in `build_site()`).
- Produces: unchanged signature.

- [ ] **Step 1: Fetch the canonical ECL markup**

Fetch `ecl-menu` (mega menu) and `ecl-search-form` markup for v5.1.0 from `https://ec.europa.eu/component-library`. Save the raw reference into `brand/ecl/reference-menu-search.html` so the next person can see where it came from. If the fetched version is not 5.1.0, record the actual version in that file — a mismatch is a header-only risk (spec §10) but must be visible.

- [ ] **Step 2: Add the site-name constant**

In the Task 2 flag block:

```python
# Header banner text. ECL convention is the PARENT site name, so if this lands
# under environment.ec.europa.eu this becomes "Environment" -- spec section 6.3.
SITE_NAME_BANNER = "EU Wildlife Trade Regulations"
```

- [ ] **Step 3: Write the assertions, and confirm they currently fail**

```bash
grep -c 'ecl-site-header' docs/index.html
grep -c 'logo-ec--en.svg' docs/index.html
grep -c 'class="top-nav"' docs/index.html
```

Expected now: `0`, `0`, `1`.

- [ ] **Step 4: Rebuild `ecl_site_header()` on ECL markup**

Model the outer structure on `brand/ecl/quick_example.html` (the `<header class="ecl-site-header …">` block) and the menu/search on the Step 1 reference. Required properties:

- Root classes `ecl-site-header ecl-site-header-with-logo-l ecl-site-header--has-mega-menu`, with `data-ecl-auto-init="SiteHeader"`.
- Nesting `__inner` → `__background` → `__header` → `__container ecl-container` → `__top`, then a sibling `__banner`.
- Logo in a `<picture class="ecl-picture ecl-site-header__picture">` with a `<source … media="(min-width: 996px)">` and an `<img class="ecl-site-header__logo-image ecl-site-header__logo-image--l">`, both pointing at `{root}assets/ecl/images/logo/positive/logo-ec--en.svg`, `alt="European Commission"`.
- **The logo links to `{root}index.html`, not `commission.europa.eu`, and carries no `target="_blank"`.** This is a deliberate correction (spec §6.3).
- Search converted to `.ecl-site-header__search-container` + `.ecl-search-form`, keeping `action="{root}search.html"`, `method="get"` and `name="q"` so `search.js` and `search.html` keep working untouched.
- Main navigation converted to `.ecl-menu`, still driven by `_GUIDE_NAV_ITEMS` for the Reference Guide items and `_ANNEXES_SLUG` for the Annexes link. Preserve the existing Home / About / Reference Guide / Annexes structure.
- Banner: `<div class="ecl-site-header__banner"><div class="ecl-container"><div class="ecl-site-header__site-name">{h(SITE_NAME_BANNER)}</div></div></div>`.
- Add the eTranslation JSON block inside `.ecl-site-header__action`, copied from `quick_example.html`.

Icons will reference `icons/icons.svg#…` in ECL markup. Per spec §7.2 that sprite was not supplied — leave the references as ECL emits them; webtools supplies the equivalents in production. Note any visibly missing icon in the commit message rather than inventing substitutes.

- [ ] **Step 5: Remove the superseded CSS**

Delete the `.site-header*`, `.header-search*` and `.top-nav*` rule blocks from `CSS`, and drop `.site-header`, `.top-nav` from the `@media print` hide list. Keep `.skip-link` — it is not ECL-supplied and is still referenced.

- [ ] **Step 6: Build and verify the assertions now pass**

```bash
python build_site.py
grep -c 'ecl-site-header' docs/index.html
grep -c 'logo-ec--en.svg' docs/index.html
grep -c 'class="top-nav"' docs/index.html
grep -c 'commission.europa.eu/index_en' docs/index.html
```

Expected: `1` or more, `2` (source + img), `0`, `0`.

- [ ] **Step 7: Verify interactively at three widths**

Serve `docs/` and check:
- Desktop: logo, site name band, search field, and the mega menu all render; the Reference Guide item opens and lists every guide section.
- **995 px and 997 px**: the `<picture>` breakpoint switches without layout jumping.
- 375 px: the header collapses to ECL's mobile treatment and the menu is reachable by keyboard.
- Console: no errors. Submitting a search still lands on `search.html?q=…` with results.

- [ ] **Step 8: Commit**

```bash
git add build_site.py brand/ecl/reference-menu-search.html docs/
git commit -m "feat: convert site header, logo, search and nav to ECL components"
```

---

### Task 7: Convert the breadcrumb and page header

Spec §6.4.

**Files:**
- Modify: `build_site.py` — `ecl_breadcrumb()`; `make_page_header()` (line 2120); `.breadcrumbs*` and `.page-header*` rules in `CSS`

**Interfaces:**
- Consumes: `ecl_breadcrumb(bc_items: str)` from Task 3; `make_page_header()` as it stands.
- Produces: **`ecl_breadcrumb(segments: list[tuple[str, str | None]]) -> str`** — a
  breaking signature change from Task 3. Each tuple is `(label, href)`, with
  `href=None` marking the current page. `make_page_header()` keeps its signature.
- **Callers to update in the same commit:** every site that builds `bc_items`.
  Find them with `grep -n 'bc_items' build_site.py` before editing anything.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'ecl-breadcrumb' docs/chapters/03_2_what_document_for_what_purpose.html
grep -c 'ecl-page-header__title' docs/chapters/03_2_what_document_for_what_purpose.html
```

Expected now: `0`, `0`.

- [ ] **Step 2: Rebuild `ecl_breadcrumb()`**

Wrap in `<div class="ecl-container">`, then `<nav class="ecl-breadcrumb ecl-page-header__breadcrumb" aria-label="You are here:" data-ecl-breadcrumb="true" data-ecl-auto-init="Breadcrumb">` containing `<ol class="ecl-breadcrumb__container">`.

Each intermediate segment: `<li class="ecl-breadcrumb__segment" data-ecl-breadcrumb-item="expandable">` with an `ecl-link ecl-link--standalone ecl-link--no-visited ecl-breadcrumb__link` anchor followed by the separator icon span. The first segment uses `data-ecl-breadcrumb-item="static"`. The last: `<li class="ecl-breadcrumb__segment ecl-breadcrumb__current-page" data-ecl-breadcrumb-item="static" aria-current="page">`.

Include the ellipsis `<li>` with `data-ecl-breadcrumb-ellipsis` and its button — copy from `quick_example.html`. Without it the collapse behaviour has nothing to expand.

The callers pass a pre-built `bc_items` string, so `ecl_breadcrumb()` must now build the `<li>`s itself. Change its parameter to the structured list the callers already have available rather than string-patching `bc_items` — grep for `bc_items` to find every construction site and update them together.

- [ ] **Step 3: Convert `make_page_header()`**

`.page-header__title` → `.ecl-page-header__title`, wrapped in `<div class="ecl-container">`. Keep `.page-header__meta` and its items as they are — they have no ECL equivalent and belong to content styling.

- [ ] **Step 4: Remove the superseded CSS**

Delete `.breadcrumbs` rules; keep `.page-header__meta*`. Drop `.breadcrumbs` from the print hide list.

- [ ] **Step 5: Build and verify**

```bash
python build_site.py
grep -c 'ecl-breadcrumb' docs/chapters/03_2_what_document_for_what_purpose.html
grep -c 'ecl-page-header__title' docs/chapters/03_2_what_document_for_what_purpose.html
grep -rc 'class="breadcrumbs"' docs/ | grep -v ':0' || echo "no stale breadcrumbs"
```

Expected: `1` or more, `1`, `no stale breadcrumbs`.

- [ ] **Step 6: Verify interactively**

On a deep sub-page: breadcrumb renders with correct segments and a non-linked current page; at 375 px the ellipsis collapses intermediate segments and the button expands them. Console clean.

- [ ] **Step 7: Commit**

```bash
git add build_site.py docs/
git commit -m "feat: convert breadcrumb and page header to ECL components"
```

---

### Task 8: Convert the content grid to ECL columns and in-page navigation

Spec §6.5. Removes the bespoke mobile contents block.

**Files:**
- Modify: `build_site.py` — `make_sidebar()` (line 2103), `make_mobile_contents()` (line 2091), `base_html()`'s grid interpolations, and `.page-grid` / `.sidebar*` / `.mobile-contents*` rules in `CSS`
- Modify: call sites of `make_mobile_contents()`

**Interfaces:**
- Consumes: `make_sidebar(headings)`, `heading_items(headings)`, `_contents_li(...)`.
- Produces: `make_sidebar(headings: list[dict]) -> str` returning ECL in-page navigation markup; `make_mobile_contents()` **removed**.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'ecl-inpage-navigation' docs/chapters/03_2_what_document_for_what_purpose.html
grep -c 'mobile-contents' docs/chapters/03_2_what_document_for_what_purpose.html
```

Expected now: `0`, `1` or more.

- [ ] **Step 2: Rebuild `make_sidebar()` as ECL in-page navigation**

Structure per `quick_example.html`: `<nav class="ecl-inpage-navigation" data-ecl-auto-init="InpageNavigation" data-ecl-inpage-navigation="true" aria-labelledby="…">` → `__title` → `__body` → `__trigger-wrapper` (with the trigger button and its icon) → `<ul class="ecl-inpage-navigation__list" data-ecl-inpage-navigation-list="true">` of `<li class="ecl-inpage-navigation__item">` each holding an `ecl-link ecl-inpage-navigation__link` with `data-ecl-inpage-navigation-link`.

Keep sourcing items from `heading_items(headings)` so heading extraction and anchor prediction stay untouched. Title text: keep whatever the current sidebar label says (`grep -n 'sidebar__label' build_site.py`).

- [ ] **Step 3: Delete `make_mobile_contents()` and its call sites**

ECL's trigger provides the mobile affordance, making the `<details>` block redundant (spec §6.5).

```bash
grep -n 'make_mobile_contents' build_site.py
```

Remove the function and every call. Also delete `_contents_li()` if nothing else uses it — check first.

- [ ] **Step 4: Convert the grid**

In `base_html()`, `grid_open` becomes `<div class="ecl-container"><div class="ecl-row" data-ecl-inpage-navigation-container>`; `sidebar_col` wraps the sidebar in `<div class="ecl-col-l-3">`; `article_open` becomes `<div class="ecl-col-l-9">` carrying the existing `.article` class for content styling (`<div class="ecl-col-l-9 article">`). Keep `.article` — Global Constraints require content styling intact.

Pages with no sidebar must still render: verify `about.html`, `search.html` and `404.html`, which take the non-grid path.

- [ ] **Step 5: Remove the superseded CSS**

Delete `.page-grid`, `.sidebar`, `.sidebar__label`, `.sidebar__nav`, `.mobile-contents*` rules and their `@media` variants. Drop `.sidebar` and `.mobile-contents` from the print hide list. **Keep `.article` and `.article-body`.**

- [ ] **Step 6: Build and verify**

```bash
python build_site.py
grep -c 'ecl-inpage-navigation' docs/chapters/03_2_what_document_for_what_purpose.html
grep -rc 'mobile-contents' docs/ | grep -v ':0' || echo "mobile-contents gone"
grep -c 'ecl-col-l-9' docs/chapters/03_2_what_document_for_what_purpose.html
```

Expected: `1` or more, `mobile-contents gone`, `1`.

- [ ] **Step 7: Verify interactively — and re-check the article body**

- Desktop: two-column layout holds; in-page nav highlights the active heading on scroll.
- 375 px: the trigger button expands and collapses the list; no orphaned `<details>`.
- `about.html`, `search.html`, `404.html` all render without a broken grid.
- **Article body compared against `brand/baseline/article-desktop.png`** — the column wrapper changed, so line length may shift. A modest width change is acceptable; changed type size, spacing or table rendering is not.

- [ ] **Step 8: Commit**

```bash
git add build_site.py docs/
git commit -m "feat: convert content grid and sidebar to ECL row and in-page navigation"
```

---

### Task 9: Convert cards behind the `ECL_CARDS` flag

Spec §5.1.1. The two card helpers already exist, so this is genuinely a two-function change — the structural requirement is already satisfied.

**Files:**
- Modify: `build_site.py` — `_make_card()` (line 2414), `_make_about_subcard()` (line 2441), and the grid wrappers in `build_index_page()` (line 2463) / `build_parent_landing()` (line 2228)

**Interfaces:**
- Consumes: `ECL_CARDS` from Task 2; `_CARD_ICON_PATHS`, `_CARD_ICON_COLORS`.
- Produces: `_make_card(ch, summaries, href=None) -> str` and `_make_about_subcard(ref, title, href, summary, icon_path, icon_color) -> str` — **signatures unchanged**, branching internally on `ECL_CARDS`.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'ecl-card' docs/index.html
grep -c 'chapter-card' docs/index.html
```

Expected now: `0`, `1` or more.

- [ ] **Step 2: Branch `_make_card()` on the flag**

Keep the existing body as the `ECL_CARDS = False` branch, renamed internally, and add the ECL branch:

```python
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
```

Extract the current body verbatim into `_make_card_legacy(ch, label, summary, actual_href, icon_path, icon_color) -> str`. Apply the same pattern to `_make_about_subcard()` with `_make_about_subcard_legacy()`.

Note the semantic change: the legacy card is a whole-card `<a>`; `ecl-card` puts the link on the title. Both are valid; the ECL form is more accessible.

- [ ] **Step 3: Keep the legacy CSS dormant, not deleted**

**Do not remove** `.chapter-card*`, `.subpage-card*`, `.chapter-grid` or `.subpage-grid` from `CSS`. Add a comment above them:

```css
/* Legacy card design -- retained dormant so ECL_CARDS=False reverts cleanly.
   See brand/ECL-Migration-Spec.md section 5.1.1. Do not delete. */
```

This is the one place in the plan where dead-looking CSS is correct.

- [ ] **Step 4: Convert the grid wrappers, conditionally**

`.chapter-grid` / `.subpage-grid` become `.ecl-row` with `.ecl-col-s-6 .ecl-col-l-4` children under `ECL_CARDS = True`, and stay as they are under `False`. Both branches must be reachable from one place per grid.

- [ ] **Step 5: Build and verify both flag states**

```bash
python build_site.py
grep -c 'ecl-card' docs/index.html
python - <<'PY'
import re, pathlib, build_site as b
b.ECL_CARDS = False
print("legacy card renders:", "chapter-card" in b._make_card(
    {"section_number": 2, "slug": "x", "title": "T"}, {"x": "S"}))
PY
```

Expected: a non-zero `ecl-card` count, then `legacy card renders: True`. **Leave the flag at `True` in the committed file.**

- [ ] **Step 6: Verify interactively at all breakpoints**

`index.html` and a parent landing page at desktop, tablet and 375 px. Cards must be keyboard-reachable via the title link. Compare against `brand/baseline/home-desktop.png` and `landing-desktop.png` — this is the change most likely to draw feedback, so capture fresh screenshots into `brand/baseline/after-` prefixed files to show side by side.

- [ ] **Step 7: Commit**

```bash
git add build_site.py docs/
git commit -m "feat: convert cards to ecl-card behind revertible ECL_CARDS flag"
```

---

### Task 10: Convert the footer, then retire the demo file

Spec §6.6. The footer's `__row--common` is mandatory EC furniture the site has never had.

**Files:**
- Modify: `build_site.py` — `ecl_site_footer()`; `.site-footer*` rules in `CSS`
- Delete: `brand/ecl/quick_example.html` (only after this task's markup is verified)

**Interfaces:**
- Consumes: `ecl_site_footer(root)` from Task 3; `FOOTER_TEXT` module global.
- Produces: unchanged signature.

- [ ] **Step 1: Write the assertions, and confirm they currently fail**

```bash
grep -c 'ecl-site-footer' docs/index.html
grep -c 'ecl-site-footer__row--common' docs/index.html
grep -c 'Legal notice' docs/index.html
grep -c 'TRAFFIC' docs/index.html
```

Expected now: `0`, `0`, `0`, `1` or more (existing smallprint).

- [ ] **Step 2: Build the footer from the template's markup**

Copy the `<footer class="ecl-site-footer">` block out of `brand/ecl/quick_example.html` and adapt:

**`__row--specific`** — keep DG ENV's structure and links verbatim per the approved decision: site-info section titled "Environment" linking to `environment.ec.europa.eu/index_en`; the five DG ENV social accounts; "Contact us" → Contact DG Environment; "About us" → Accessibility; "Related links" → Agriculture, Fisheries, Climate action, Energy, European Climate Pact.

**TRAFFIC attribution** goes in `__section--site-info` `__description`:

```html
<div class="ecl-site-footer__description">
  This site is managed by:<br />Directorate-General for Environment<br />
  A reference guide developed by the European Commission and TRAFFIC.
</div>
```

**`__row--common`** — copy in full: the `logo-ec--mute-negative.svg` link (repointed to `{root}assets/ecl/images/logo/logo-ec--mute-negative.svg`), the "Follow the European Commission" social row, and the legal navigation (Languages on our websites, Cookies, Privacy policy, Legal notice). **Do not reproduce the template's empty trailing `<a href="">`** — spec §1.1.

**Feedback link** — fold the old `.site-footer__cta` mailto into the "Contact us" list as an extra `<li>`; drop the standalone CTA bar.

**Existing smallprint** — keep `FOOTER_TEXT` rendering (citation, disclaimer, revision note) below the specific row, wrapped in `.ecl-container`. Do not lose it; grep `FOOTER_TEXT` to find the current rendering at line ~1878.

- [ ] **Step 3: Remove the superseded CSS**

Delete `.site-footer*` rule blocks from `CSS`, keeping `.footer-smallprint` (still used by `FOOTER_TEXT`). Drop `.site-footer` from the print hide list.

- [ ] **Step 4: Build and verify the assertions now pass**

```bash
python build_site.py
grep -c 'ecl-site-footer' docs/index.html
grep -c 'ecl-site-footer__row--common' docs/index.html
grep -c 'Legal notice' docs/index.html
grep -c 'TRAFFIC' docs/index.html
grep -c 'href=""' docs/index.html
grep -c 'site-footer__cta' docs/index.html
```

Expected: non-zero, `1`, `1`, `2` or more (site-info + smallprint), `0`, `0`.

- [ ] **Step 5: Verify interactively**

Footer renders both rows at desktop and 375 px; the mute-negative logo resolves (not a broken image); every legal link resolves; TRAFFIC attribution is visible; smallprint paragraphs are all present. Social icons may be missing pending spec §7.2 — note which, do not substitute.

- [ ] **Step 6: Retire the demo file**

Now that every fragment has been extracted, the ordering constraint in spec §3 is satisfied:

```bash
rm brand/ecl/quick_example.html
```

- [ ] **Step 7: Commit**

```bash
git add build_site.py brand/ecl docs/
git commit -m "feat: convert footer to ECL with mandatory common row; retire demo template"
```

---

### Task 11: Full verification pass

Spec §8. No new features. This task exists so the whole migration gets one gate that a fresh reviewer can run.

**Files:**
- Modify: `brand/ECL-Migration-Spec.md` (record verification outcomes)
- Modify: `build_site.py` only if defects are found

**Interfaces:**
- Consumes: everything above.
- Produces: a completed verification record.

- [ ] **Step 1: Clean rebuild from scratch**

```bash
python build_site.py
git status --porcelain docs/ | head -20
```

- [ ] **Step 2: Confirm no stale bespoke chrome classes survive anywhere**

```bash
for c in official-strip 'class="site-header' 'class="top-nav' 'class="breadcrumbs' \
         'class="page-grid' 'class="sidebar' mobile-contents 'class="site-footer'; do
  printf '%s: ' "$c"; grep -rl "$c" docs/ --include='*.html' | wc -l
done
```

Expected: `0` for every one. Any non-zero means a page builder was missed — `build_simple_section`, `build_parent_landing`, `build_sub_page`, `build_about_page`, `build_index_page`, `build_search_page` and `build_404_page` all need checking.

- [ ] **Step 3: Confirm the article body is unchanged apart from Task 2's attribute**

```bash
git diff HEAD~9 -- docs/chapters/ | grep '^[+-]' | grep -v '^[+-][+-]' \
  | grep -vE 'data-ecl-table-header|ecl-|breadcrumb|inpage-navigation|col-l-|ecl-container' \
  | head -30
```

Review whatever prints. Article prose, footnotes, figures and table *contents* must not appear. If they do, that is a spec §5 regression and must be fixed before this task closes.

- [ ] **Step 4: Verify the PDF survived untouched**

```bash
python -c "import pypdf; print(len(pypdf.PdfReader('docs/EU-Wildlife-Trade-Reference-Guide.pdf').pages))"
cat brand/baseline/pdf-pages.txt
```

Page counts must match. Spot-check three pages visually — a chapter opening, a wide table, a full-page figure — against the pre-migration PDF in git history. Byte differences are expected (WeasyPrint timestamps) and are not a defect.

- [ ] **Step 5: Run the full interactive checklist**

Serve `docs/` and confirm every item in spec §8: no console errors from `autoInit()`; header at desktop / 995 px / mobile; breadcrumb ellipsis; in-page nav trigger; card grids at all breakpoints; footer common row complete; browser print preview (Ctrl-P) matching the baseline. Remember §8.1 — globan, cookie consent and eTranslation will be blank locally, and that is expected.

- [ ] **Step 6: Record the outcome in the spec**

Append a "Verification record" section to `brand/ECL-Migration-Spec.md` stating the date, what passed, what is deferred to the europa.eu move (globan, cookie consent, eTranslation, `wt-icon--*` icons), and any overrides that had to be added because `ecl-reset.css` was omitted. Be specific — this is the evidence for the §5 decision.

- [ ] **Step 7: Commit**

```bash
git add brand/ECL-Migration-Spec.md build_site.py docs/
git commit -m "docs: record ECL migration verification outcomes"
```

---

## Deferred work (not in this plan)

- **Enabling `ECL_CONTENT = True`** — full ECL adoption of the article body. Spec §5.1.2 warns this likely requires loading `ecl-reset.css`, which invalidates the §5 decision; it needs its own verification pass. **Known gap to fix first:** the PDF-postprocessing functions `_pdf_relocate_table_captions()` and `_pdf_mark_table_bounds()` hardcode the literal string `"table-wrap"` instead of calling `cc("table_wrap")` (found during Task 2's implementation, `build_site.py`, PDF section). Harmless while `ECL_CONTENT` is `False` — `cc()` returns the same literal — but will silently stop matching once the flag flips, breaking PDF table-caption/bounds logic. Route these through `cc("table_wrap")` as part of enabling the flag.
- **Reverting `ECL_CARDS = False`** — a one-line flag change plus a rebuild, should feedback go that way.
- **Setting `SITE_NAME_BANNER`** once the domain is confirmed.
- **Resolving the icon sprite** (spec §7.2) — request `icons/` from the EC contact; expect webtools to supply it in production.
- **375px breadcrumb collapse on 3-segment pages** — every `build_sub_page()` page (Home > Parent > Current) renders an invisible breadcrumb nav at mobile widths. Confirmed inherent to vendored ECL v5.1.0 itself (its `docs/assets/ecl/scripts/ecl-ec.js` hardcodes a `this.breakpoint=995` nav-width check; the untouched `brand/ecl/quick_example.html` reference shows the identical collapse), not something this plan's code introduced or can fix without patching vendored third-party JS. Degrades gracefully (H1 + mobile contents dropdown remain visible). Worth raising with the EC contact alongside the icon-sprite gap — a newer ECL release may have already fixed this.
- **`ECL_CHROME = False`** — only relevant if the site moves into DG ENV's Drupal / `oe_theme`.
