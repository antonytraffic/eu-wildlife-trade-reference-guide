# ECL Migration Design Spec

**Date:** 2026-09-08
**Status:** awaiting review
**Scope:** align the EU Wildlife Trade Reference Guide website with the Europa Component Library distribution supplied by the European Commission.

Companion to [EC-Brand-Guidelines.md](EC-Brand-Guidelines.md), which covers tokens (colour, type, spacing). This document covers **components and markup**.

---

## 1. What we were given

`brand/example_template/` is the complete self-hosted ECL distribution, **`@ecl/preset-ec` v5.1.0, built 2026-07-23**:

| Path | Contents |
|---|---|
| `styles/ecl-ec.css` | 415 KB minified component library |
| `styles/ecl-ec-print.css` | 244 KB print stylesheet |
| `styles/ecl-ec-color-modes.css` | 16 section palettes |
| `styles/optional/` | `ecl-reset`, `ecl-ec-default`, `ecl-ec-utilities`, `ecl-ec-easy-to-read`, `ecl-ec-default-print` |
| `scripts/ecl-ec.js` | 341 KB UMD bundle, initialised via `ECL.autoInit()` |
| `scripts/ecl-esm-ec.js` | ESM equivalent |
| `fonts/` | `InterVariable.woff2` + `InterVariable-Italic.woff2` (v4.1) |
| `images/logo/{positive,negative}/` | `logo-ec--<lang>.svg` x 43 languages, plus two mute variants |
| `quick_example.html` | Demo reference page |
| `settings.svg` | Stray demo asset |

**The version matches prior work.** `EC-Brand-Guidelines.md` was derived from ECL 5.1.0 tokens, and `build_site.py`'s `:root` already carries the exact ECL hexes (`#0046ff`, `#00002e`, `#696984`, `#d4d4dc`, `#f6f6f8`, `#ededf0`, `#ffce00`, `#66439a`). Colour and typeface are already correct. **The gap is components and markup, not palette.** Existing manual brand work is preserved, not discarded.

### 1.1 `quick_example.html` is a demo, not a specification

It must be read as a markup reference only. It contains: the title "DEMO BLANK PAGE"; Lorem ipsum; leftover DataTables float rules; a stray `.ecl-banner{height:196px!important}` override; mismatched heading utilities (`<h2 class="ecl-u-type-heading-3">`); an empty `<a href="">` in the footer legal navigation; and DG-Environment-specific links and social accounts. None of these are to be reproduced.

---

## 2. Decisions taken

| Question | Decision |
|---|---|
| Hosting target | An `ec.europa.eu` domain |
| Deployment shape | Standalone static build; chrome generation kept separable for a possible later move into DG ENV's Drupal / `oe_theme` |
| Fidelity | **ECL shell** — chrome verbatim ECL; article body keeps existing tuned styling |
| TRAFFIC co-branding | Removed from the globan; retained in footer site-info and the About page |
| Print and PDF | **Unchanged.** Neither the `@media print` block nor the WeasyPrint pipeline is touched |
| Missing mega menu / search markup | Fetch canonical markup from `ec.europa.eu/component-library` |
| Header banner site name | Build constant; value decided once the domain is confirmed |
| Footer specific row | Copy DG ENV's contact / about / related links and social accounts from the template |
| Card grids | **Convert** `.chapter-card` / `.subpage-card` to `article.ecl-card` — behind a revertible flag, see section 5.1 |
| Reversibility | Card conversion and article-body styling are both **flagged, not baked in** — see section 5.1 |

Hosting on `ec.europa.eu` resolves the two largest risks outright: the globan's "official website of the European Union" assertion becomes accurate, and webtools (globan, cookie consent, eTranslation, `wt-icon--*` classes) is served same-origin.

---

## 3. Asset vendoring

Rename `brand/example_template/` to `brand/ecl/`. Delete `settings.svg` (stray demo asset).

**Ordering constraint:** `quick_example.html` is the only source for the footer markup in section 6.6 and the globan/header markup in 6.2–6.3. Extract those first; delete it only once the chrome functions in section 4 are written and verified. It also remains recoverable from git history.

`build_site.py` copies a **subset** to `docs/assets/ecl/`:

**Ship**
- `styles/ecl-ec.css`
- `styles/optional/ecl-ec-default.css`
- `styles/optional/ecl-ec-utilities.css`
- `scripts/ecl-ec.js`
- `fonts/InterVariable.woff2`, `fonts/InterVariable-Italic.woff2`
- `images/logo/positive/logo-ec--en.svg`
- `images/logo/negative/logo-ec--en.svg`
- `images/logo/logo-ec--mute-negative.svg`

**Do not ship**
- `ecl-ec-print.css`, `optional/ecl-ec-default-print.css` — print is out of scope (section 2)
- `ecl-ec-color-modes.css` — single-domain guide, no section theming
- `optional/ecl-ec-easy-to-read.css` — not adopted; revisit separately
- `optional/ecl-reset.css` — deliberately excluded, see section 5
- `scripts/ecl-esm-ec.js` — the UMD build is used
- The 42 non-English logo variants
- All `.css.map` files

Payload: roughly 5.7 MB down to roughly 1.5 MB.

**Dedupe:** `docs/assets/fonts/InterVariable.woff2` (352 KB) becomes redundant against the ECL copy. Remove it and repoint the `@font-face` reference.

**Font hosting:** ECL's CSS declares both a local `../fonts/` source and a `https://webtools.europa.eu/fonts/inter/` source. Keep the local copies as primary so the build stays deterministic and works offline.

---

## 4. Chrome extraction

The header, navigation and footer are currently a single ~140-line f-string beginning at `build_site.py:1890`. Extract into named functions behind one flag:

```python
ECL_CHROME = True          # False -> Drupal / oe_theme supplies the chrome
SITE_NAME_BANNER = "EU Wildlife Trade Regulations"   # section 6.3

def ecl_head_links(root):   ...   # stylesheet + font + favicon links
def ecl_globan():           ...   # webtools globan JSON + #cck_here
def ecl_site_header(root):  ...   # ecl-site-header, logo, search, menu, banner
def ecl_breadcrumb(items):  ...   # ecl-breadcrumb with ellipsis
def ecl_site_footer(root):  ...   # __row--specific + __row--common
def ecl_scripts():          ...   # ecl-ec.js + ECL.autoInit() + cck JSON
```

This satisfies the "keep chrome separable" requirement — a Drupal migration becomes one switch rather than a rewrite — and reduces a 149 KB single-file build script.

---

## 5. The scoping boundary

**`ecl-reset.css` is deliberately NOT loaded.** ECL's component CSS is fully BEM-namespaced (`.ecl-*`) and barely touches unclassed elements, so omitting the reset leaves the article body's tuned typography, tables, figures and footnotes untouched and keeps the diff small.

**Known risk:** some ECL components may assume reset defaults for list margins and native button appearance.

**Mitigation:** verify header, mega menu, breadcrumb, in-page navigation, cards and footer in the browser preview after the swap; add targeted overrides in a small `ecl-overrides` block only where something demonstrably breaks. Do not pre-emptively add overrides.

**Boundary definition:**
- **Chrome (becomes ECL):** globan, site header, logo, header search, main navigation, breadcrumb, page header title, in-page navigation, grid columns, card grids, footer.
- **Content (stays as-is):** article body typography, tables, figures, footnotes, hero, chatbot, back-to-top, search results page.

### 5.1 Reversibility requirements

Two decisions in this spec are expected to come under review once the result is seen and feedback arrives. Both must therefore be **flags, not baked-in rewrites**. Building them this way is cheap now and expensive to retrofit.

```python
ECL_CHROME  = True    # section 4 — False: Drupal / oe_theme supplies the chrome
ECL_CARDS   = True    # 5.1.1     — False: revert to the current bespoke card design
ECL_CONTENT = False   # 5.1.2     — True:  article body adopts ECL type/table classes
```

#### 5.1.1 Card grids must be revertible

This **supersedes the statement in section 6.5 that the bespoke card grid CSS is retired.** The legacy `.chapter-card`, `.subpage-card`, `.chapter-grid` and `.subpage-grid` rules stay in `style.css`, dormant but intact. `ECL_CARDS` selects which markup the card-rendering helper emits; no CSS is deleted.

Requirement: card markup is generated by a **single helper per card type** (`render_chapter_card()`, `render_subpage_card()`), not inlined into the page templates. Flipping the flag then changes two functions, not every call site.

#### 5.1.2 Article-body styling must be switchable to ECL

The declined "full ECL adoption" option (article-body headings, paragraphs, lists and tables carrying `ecl-u-type-*` / `ecl-table` classes) must remain reachable without rewriting `render_markdown()`.

Requirement: `render_markdown()` and its post-processors must not emit bare tags directly. Route every content-element class through one lookup table:

```python
CONTENT_CLASSES = {
    False: {  # current — bare tags, existing style.css governs
        "h2": "", "h3": "", "h4": "", "p": "", "ul": "", "li": "",
        "table": "", "table_wrap": "table-wrap", "thead": "", "th": "", "td": "",
    },
    True: {   # ECL — full adoption
        "h2": "ecl-u-type-heading-2", "h3": "ecl-u-type-heading-3",
        "h4": "ecl-u-type-heading-4", "p": "ecl-u-type-paragraph-m",
        "ul": "ecl-unordered-list", "li": "ecl-unordered-list__item",
        "table": "ecl-table", "table_wrap": "ecl-table-responsive",
        "thead": "ecl-table__head", "th": "ecl-table__header",
        "td": "ecl-table__cell",
    },
}
```

With `ECL_CONTENT = False` the map emits empty strings, so **the generated HTML is byte-identical to today's** — which is what makes section 8.7's screenshot diff a meaningful gate rather than a formality.

Two caveats to record now:
- ECL's responsive table also needs `data-ecl-table-header` on every `<td>`. Emit it unconditionally (it is inert without ECL's table CSS) so switching the flag does not require revisiting table generation.
- Under `ECL_CONTENT = True`, `ecl-reset.css` (section 5) may become necessary after all, since ECL's type utilities assume it. Treat enabling that flag as its own scoped piece of work with its own verification pass — not a one-line change.

---

## 6. Element mapping

### 6.1 Page plumbing

| Current | Target |
|---|---|
| `<html lang="en">` | `<html lang="en" class="no-js">` + inline `no-js` -> `has-js` swap before paint |
| Single `assets/style.css` | ECL link order: `ecl-ec-default.css` -> `ecl-ec.css` -> `ecl-ec-utilities.css` -> `assets/style.css` |
| No ECL JS | `scripts/ecl-ec.js` + `ECL.autoInit()` before `</body>` |
| Self-hosted `InterVariable.woff2` only | Both ECL font files, italic included |
| `<meta name="theme-color">` | Removed — not an ECL convention |
| `description` meta only | Add an `og:` / `twitter:` block, authored for this guide |
| Self-hosted favicons | Retained — correct for a distinct site |

### 6.2 Globan

Delete `.official-strip` and its hand-coded 12-star EU flag SVG (`build_site.py:1900`) entirely. Replace with:

```html
<div id="cck_here"><!-- cookie consent --></div>
<script type="application/json">{"utility":"globan","theme":"light","logo":true,"link":true,"mode":false}</script>
```

Plus `<script defer src="https://webtools.europa.eu/load.js">` and `<script type="application/json">{"utility":"cck"}</script>`.

The hand-drawn flag and the custom wording "A reference guide developed by the European Commission and TRAFFIC" were the single largest brand-compliance divergence in the site. TRAFFIC attribution relocates per section 6.6.

### 6.3 Site header

| Current | Target |
|---|---|
| `.site-header` | `.ecl-site-header.ecl-site-header-with-logo-l.ecl-site-header--has-mega-menu` + `data-ecl-auto-init="SiteHeader"`, with `__inner / __background / __header / __container / __top / __banner` |
| `logo-ec-positive.svg` in a plain `<img>`, linked to `commission.europa.eu` with `target="_blank"` | `images/logo/positive/logo-ec--en.svg` in a `<picture>` with a `min-width:996px` source and `ecl-site-header__logo-image--l`; **links to the site's own home**, no `target="_blank"` |
| `.site-header__banner` / `__site-name` | `.ecl-site-header__banner` / `.ecl-site-header__site-name`, value from `SITE_NAME_BANNER` |
| `.header-search` custom form | `.ecl-site-header__search-container` + `.ecl-search-form` (markup fetched, section 7) |
| `.top-nav` custom dropdown | `.ecl-menu` mega menu (markup fetched, section 7) |
| — | eTranslation JSON block in `.ecl-site-header__action` (new; appropriate on europa.eu) |

`SITE_NAME_BANNER` defaults to the current value, per commit `1615f0c`. If the guide lands under `environment.ec.europa.eu` rather than its own subdomain, ECL convention is the parent site name ("Environment") — hence the constant.

### 6.4 Breadcrumb and page header

- `.breadcrumbs > ol` becomes `.ecl-breadcrumb` with `__container / __segment / __link / __icon / __current-page`, the ellipsis collapse button, `data-ecl-breadcrumb="true"` and `data-ecl-auto-init="Breadcrumb"`.
- `.page-header__title` becomes `.ecl-page-header__title` inside `.ecl-container`.

### 6.5 Content layout

- `.page-grid` becomes `.ecl-container` > `.ecl-row` with `data-ecl-inpage-navigation-container`.
- `.sidebar` becomes `.ecl-col-l-3` containing `.ecl-inpage-navigation` with `data-ecl-auto-init="InpageNavigation"`.
- `.article` becomes `.ecl-col-l-9`, contents otherwise unchanged.
- `.mobile-contents` `<details>` block is **removed** — ECL's in-page navigation has a built-in mobile trigger, making it redundant.
- `.chapter-card` and `.subpage-card` become `article.ecl-card`; `.chapter-grid` / `.subpage-grid` become `.ecl-row` + `.ecl-col-*`. **The bespoke card CSS is retained dormant, not retired** — see section 5.1.1, which governs.

### 6.6 Footer

Replace `.site-footer` with `.ecl-site-footer`:

- **`__row--specific`** — copy DG ENV's structure and links from the template: site-info section, contact section ("Contact DG Environment"), about section ("Accessibility"), related links (Agriculture, Fisheries, Climate action, Energy, European Climate Pact), and the five DG ENV social accounts.
- **TRAFFIC attribution** goes in `__section--site-info` `__description`, alongside "This site is managed by:".
- **`__row--common`** — currently absent from the site entirely. Add in full: EC mute-negative logo, "Follow the European Commission" social row, and the legal navigation (**Report an IT vulnerability**, Languages on our websites, Cookies, Privacy policy, Legal notice — five items; an earlier draft of this section omitted the first by oversight, not by design). The template's empty trailing `<a href="">` is not reproduced.
- **`.site-footer__cta`** feedback bar is removed as standalone furniture; the feedback address folds into the contact section.
- Existing smallprint (citation, disclaimer, revision note) is retained below the specific row.

---

## 7. Missing dependencies

### 7.1 Mega menu and header search markup

Not present in the supplied folder, though the header declares `ecl-site-header--has-mega-menu`. Fetch canonical ECL 5.1.0 markup for `ecl-menu` and `ecl-search-form` from `https://ec.europa.eu/component-library`. If it later differs from what DG ENV actually runs, only the header is affected.

### 7.2 Icon sprite

ECL markup references `icons/icons.svg#corner-arrow`, but **no `icons/` folder was supplied**. Separately, the template's own breadcrumb and footer use `wt-icon--*` and `wt-icon-networks--*` classes that have **zero definitions in `ecl-ec.css`** — they are supplied by webtools `load.js`.

Resolution, in order:
1. Request the `icons/` folder from the EC contact, from the same 5.1.0 distribution.
2. **Production path:** rely on webtools supplying `wt-icon--*` once hosted on europa.eu. Verifiable only after the domain move.
3. Fallback: pull `icons.svg` from the public ECL 5.1.0 release.

Pursue 1 and 3 now; treat 2 as the expected production behaviour.

---

## 8. Verification

Build, serve `docs/` in the browser preview, and confirm:

1. No console errors from `ECL.autoInit()`.
2. Header renders correctly at desktop, 995 px (the `<picture>` breakpoint) and mobile.
3. Breadcrumb ellipsis collapses at narrow widths.
4. In-page navigation trigger works on mobile; sticky behaviour on desktop.
5. Card grids render at all breakpoints.
6. Footer common row present and complete.
7. **Article body typography, tables, figures and footnotes diffed against pre-change screenshots — must be visually identical.** This is the primary regression check for the section 5 decision.
8. Browser print output (Ctrl-P) unchanged.
9. Generated PDF **visually** identical. It will **not** be byte-identical: WeasyPrint embeds a creation timestamp, so `docs/EU-Wildlife-Trade-Reference-Guide.pdf` shows as modified on every build. Exclude it from diff checks and verify page count plus spot-checked pages instead.

### 8.2 The verification harness

`docs/` is tracked in git and `generate_summaries()` reads `output/_summaries.json` as a static file without calling any API, so **the build is deterministic and `git diff` is an exact golden-file harness.** No separate test suite is needed; this matches the project's existing build-and-inspect workflow.

The canonical check, used as a gate throughout implementation:

```bash
python build_site.py
git diff --stat -- docs/ ':(exclude)docs/EU-Wildlife-Trade-Reference-Guide.pdf'
```

For any task that touches only chrome, `git diff -- docs/chapters/` must additionally be **empty** in the article-body region. Note that `main()` calls `shutil.rmtree(docs/)` first, so every build is a full regeneration rather than an incremental update.

### 8.1 Stated limitation

Globan, cookie consent and eTranslation are webtools-injected and will very likely render as **blank space locally and on GitHub Pages**. They will be built to spec but can only be verified after the move to `ec.europa.eu`. This is expected, not a defect.

---

## 9. Out of scope

- The `@media print` block and the WeasyPrint PDF pipeline (`PDF_CSS`, `build_site.py:2603`) — protects the pagination fixes in commits `e8de198`, `74ad724`, `6a1dca0`
- ECL colour modes
- `ecl-ec-easy-to-read.css` — worth revisiting separately for a regulatory guide
- Multilingual logos and content translation
- Converting article-body headings, paragraphs and tables to `ecl-u-type-*` / `ecl-table` classes (the "full ECL adoption" option, declined for now) — but the switch to enable it **is** in scope, see section 5.1.2
- Restyling `chatbot.js`, the hero, back-to-top, and the search results page beyond token alignment

---

## 10. Residual risks

| Risk | Severity | Mitigation |
|---|---|---|
| Omitting `ecl-reset.css` breaks some ECL component | Medium | Preview verification per section 8; targeted overrides only |
| Fetched mega menu / search markup differs from DG ENV's | Low | Header-only impact; reconcile if the EC contact supplies real markup |
| Icon sprite unresolved until hosted | Medium | Three routes in section 7.2; production path is webtools |
| Payload grows from ~30 KB to ~1.5 MB | Low | Subset shipping (section 3); acceptable on a europa.eu host with CDN caching |
| Card conversion disturbs landing-page layout | Low | Isolated to index and chapter index pages; verify at all breakpoints |
| Article body regression from the ECL cascade | Medium | Screenshot diff is a hard gate (section 8.7) |
| Chrome extraction refactor introduces build errors | Low | Mechanical; output diffable against pre-change HTML |
| Card or content decision reversed after feedback | Expected | Both are flags with single-point-of-change helpers (section 5.1); reversal is a flag flip plus a rebuild, not a rewrite |
| `CONTENT_CLASSES` indirection adds noise for no immediate gain | Low | Costs one dict lookup per element and keeps output byte-identical while `ECL_CONTENT` is off; retrofitting it later would mean reopening every content transform |

---

## 11. Verification record

**Date:** 2026-09-15. Full verification pass (Task 11) run against the completed Tasks 0-10 chrome migration, using the exact PowerShell build invocation and a local `python -m http.server` preview (per this task's constraints, not `preview_start`).

**1. Clean rebuild.** `python build_site.py` from a clean `docs/` removal, then `git status --porcelain docs/`: completely empty, including the PDF (byte-identical this run, not just visually — better than the "PDF-only diff" baseline expectation). No order-dependent or non-deterministic output found.

**2. Stale bespoke chrome classes.** All eight probes (`official-strip`, `site-header`, `top-nav`, `breadcrumbs`, `page-grid`, `sidebar`, `mobile-contents`, `site-footer`) returned `0` matches across `docs/**/*.html`. No page builder was missed.

**3. Article body integrity.** Diffed every one of the 47 `docs/chapters/*.html` files against baseline commit `c212af58317c727c3cd0817db0b615d2cfaa61e0` (confirmed via `git log --oneline | tail -5` as the commit immediately preceding Task 1). The brief's literal grep command surfaced only head/chrome lines (og:meta tags, the old `.official-strip` SVG flag markup) in its first 30 lines, as expected. Went further: extracted each file's `<article class="article-body">…</article>` block from both revisions and diffed visible text content only (HTML tags stripped) — **zero text-content differences across all 47 files.** The only line-level HTML differences inside article bodies are Task 2's `data-ecl-table-header=""` attribute additions on `<td>` elements (confirmed by direct inspection, e.g. `<td>Mammals</td>` → `<td data-ecl-table-header="">Mammals</td>` in `docs/chapters/02_...html`). No article prose, footnotes, figures or table contents were altered.

**4. PDF integrity.** `pypdf` reports 237 pages against `brand/baseline/pdf-pages.txt`'s recorded 237 — match. Spot-checked three pages rendered at 150 DPI via PyMuPDF against the pre-migration PDF extracted from the same baseline commit: page 8 (chapter 2 opening heading), page 9 (Table 1, a wide data table), and page 50 (Figure 7, a full-page annotated form). All three are pixel-identical between the pre-migration and current PDF.

**5. Interactive checklist (spec section 8).** Served `docs/` locally and verified:
   - No `ECL.autoInit()` errors in the console. The only console errors are the documented, expected gaps: the missing `icons/icons.svg` sprite (section 7.2, deferred to the EC contact) and webtools' CORS/cookie-notice warnings against `localhost` (section 8.1, expected only to resolve after the `ec.europa.eu` move).
   - Header renders correctly at desktop (1280px, full nav bar + search box), at the 995px `<picture>` breakpoint, and at mobile (375px). Between roughly 995px and 1150px the header's mega-menu hamburger and search-toggle buttons are present and fully clickable but visually blank — this is the icon-sprite gap (section 7.2) manifesting in an additional, previously undocumented width range, not a new functional defect. Confirmed by directly clicking the invisible hamburger button: the mega menu opens correctly and lists Home / About / Reference Guide / Annexes.
   - Breadcrumb ellipsis collapse confirmed present (`ecl-breadcrumb__ellipsis`, a "…" button, not icon-dependent) and functional on a 3-segment sub-page.
   - In-page navigation: sticky "visible" behaviour confirmed by design (`ecl-inpage-navigation--visible` is added by ECL's own sticky-sentinel `IntersectionObserver`, matching the "sticky behaviour on desktop" line in section 8 item 4 — not a defect that the nav is `display:none` before that observer fires). Scroll-spy active-item highlighting and the mobile trigger's expand/collapse both confirmed working (`aria-expanded` toggles, `ecl-inpage-navigation__list--visible` is added on click). The trigger's "current section" text label is still blank, exactly as already documented as a known, non-blocking cosmetic gap in Task 8's report (section 3 of that report's self-review) — re-confirmed here, not a new finding.
   - Card grids confirmed correct at all three breakpoints on `index.html`'s Reference Guide grid: 3 columns at 1280px (`ecl-col-l-4`), 2 columns at 768px (`ecl-col-s-6`), 1 column at 375px.
   - Footer common row present and complete: all 5 legal-navigation links (Report an IT vulnerability, Languages on our websites, Cookies, Privacy policy, Legal notice) plus the social link list.
   - Browser print preview (Ctrl-P): **found and fixed a genuine regression** (see below).
   - Per section 8.1, globan, cookie consent and eTranslation render as blank space or webtools test/error banners locally — expected, not a defect.

**Defect found and fixed: browser print output regressed.** The pre-migration `@media print` block in `build_site.py` (~line 1354, mirrored into `docs/assets/style.css`) hid the entire site chrome by its old bespoke class names: `.official-strip, .site-header, .top-nav, .breadcrumbs, .sidebar, .mobile-contents, .site-footer`. Tasks 1-9 renamed all of those elements to `ecl-*` classes but never updated this selector list (each task's own scope reasonably excluded it — see section 9's "out of scope" note, which protects the *pagination* rules in this same block, not this specific selector list). The result: the old selectors matched nothing, and browser print (Ctrl-P) on the live HTML silently started printing the full header, mega-menu, breadcrumb, in-page navigation sidebar and footer on every page — a direct regression against this section's own item 8 ("Browser print output (Ctrl-P) unchanged"). Fixed minimally by adding `.ecl-site-header, .ecl-mega-menu, .ecl-breadcrumb, .ecl-inpage-navigation, .ecl-site-footer` to the existing `display: none !important` rule, alongside a comment explaining the history. No pagination, widows/orphans, table page-break or WeasyPrint `PDF_CSS` rule was touched. Verified by temporarily promoting the page's own `@media print` rules to `@media all` in the live DOM and confirming computed `display: none` on all five selectors, with the H1 and `.article-body` remaining visible; confirmed unaffected: PDF page count still 237, byte-identical PDF output, no other file in `docs/` changed besides `docs/assets/style.css`. The webtools-injected globan/cookie-consent banner (the closest equivalent to the old `.official-strip`) is not part of this build's static markup and stays out of scope, consistent with section 8.1.

**Overrides required because `ecl-reset.css` was omitted:** none newly discovered during this pass. The overrides already recorded in sections 3-7 (subset stylesheet loading, the digit-leading-heading-id shim for `InpageNavigation`, `CONTENT_CLASSES` indirection) remain the complete set; no additional targeted CSS override was needed to make any ECL component render or behave correctly at this stage.

**Deferred to the europa.eu move** (unchanged from sections 7.2, 8.1 and the Task 11 brief's own deferred-work list): the icon sprite (`icons/icons.svg`, section 7.2 — also responsible for the blank-but-functional hamburger/search-toggle buttons noted above), the webtools-injected globan/cookie-consent/eTranslation banners (section 8.1), and the `wt-icon--*` icon set.
