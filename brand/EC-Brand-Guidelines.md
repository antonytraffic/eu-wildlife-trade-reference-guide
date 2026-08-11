# European Commission Brand Reference

Source: [Europa Component Library](https://ec.europa.eu/component-library/ec/resources/) (ECL) v5.1.0 guideline pages + resolved design tokens from the [ECL source repo](https://github.com/ec-europa/europa-component-library) (`src/themes/ec/`). The guideline pages render swatches visually rather than as text, so exact values below were pulled from the SCSS design-token source, which is the same data the guideline pages compile from.

This file only covers what this site needs. It's a GOV.UK-styled reference guide (see [build_site.py](../build_site.py)) with: site header + search, top nav, breadcrumbs, phase banner, sidebar/contents-box nav, chapter card grids, tables, prev/next pagination, expandable footnotes, and a footer. Mappings below are written against those existing pieces.

---

## 1. Colour

### Resolved brand palette (hex)

| Token | Hex | Notes |
|---|---|---|
| Primary (brand blue) | `#0046FF` | primary-600, the EC "electric blue" |
| Secondary (accent orange) | `#FC8713` | secondary-600 |
| Brand text / surface (near-black navy) | `#00002E` | grey-950 — this is what `--cm-surface-brand` / `--cm-on-surface-brand` resolve to. Used for header background and default text-on-brand, similar role to this site's current `--black` |
| Info / EU-flag blue | `#003399` | info-800 — the classic EU flag blue, used for info status and reads closest to "official EU blue" |
| Success | `#049E62` | success-700 |
| Warning | `#FF8A20` | warning-500 |
| Error | `#CB2029` | error-600 |
| Grey (borders/greys) | `#696984` | grey-600 |
| Light surface grey | `#EDEDF0` | grey-75, used for subtle backgrounds (e.g. header banner strip) |

Each of the above has a full 13-step tonal ramp (25→950). Full ramps for primary/secondary/neutral/grey/info/success/warning/error are in [`tokens/colors.scss`](tokens/colors.scss) (saved below).

### Suggested mapping onto this site's current CSS variables

The site's [style.css](../site/assets/style.css) (embedded in `build_site.py`) currently uses GOV.UK green. Suggested EC-branded remap:

```css
:root {
  --green:        #0046FF;   /* was #00703c — now EC primary blue */
  --dark-green:   #0035BF;   /* primary-700, for hover states */
  --black:        #00002E;   /* was #0b0c0c — now EC brand navy */
  --text:         #00002E;
  --secondary:    #696984;   /* grey-600 */
  --border:       #D4D4DC;   /* grey-200 */
  --light-grey:   #F6F6F8;   /* grey-50 */
  --mid-grey:     #EDEDF0;   /* grey-75 */
  --white:        #ffffff;
  --focus:        #FFCE00;   /* yellow-gold-500, an accessible focus-state yellow that isn't in the primary ramp */
  --visited:      #66439A;   /* purple-700, kept distinct from primary blue */
  --info:         #003399;   /* EU-flag blue, for info banners/tags if needed */
}
```

Class names (`--green`, `--dark-green`) don't need renaming — the values just get swapped. Keeps the diff small.

### Usage rules (from guideline text)
- Primary-600 is the main brand colour; Secondary-400 is the secondary brand colour.
- Status colours (info/success/warning/error) are semantic — only use them for their matching message type.
- 16 alternate "colour mode" palettes exist (Blue-navy, Green-pine, Red-crayola, etc.) for section theming, not needed here since this is a single-domain reference guide.

---

## 2. Typography

**Font:** Inter, fallback Arial, sans-serif — "the standard typeface for websites under the European Commission domain."

**Base body size:** 1.125rem (18px) — this site currently uses 1rem (16px) as base ([style.css](../site/assets/style.css) `html { font-size: 16px; }`). Worth deciding whether to bump base size or keep 16px for density (this is a dense legal/regulatory reference guide, not a marketing site).

**Heading scale (desktop):**

| Element | Size | Line-height | Weight |
|---|---|---|---|
| H1 | 3.75rem | 3.75rem | 600 (semi-bold) |
| H2 | 2.5rem | 3rem | 600 |
| H3 | 1.75rem | 2.25rem | 600 |
| H4 | 1.5rem | 2.25rem | 500 (medium) |
| H5 | 1.375rem | 1.75rem | 500 |
| H6 | 1.25rem | 1.75rem | 650 (near-bold) |

These are marketing-page sizes — too large for a dense reference guide (current site uses h1: 2rem, h2: 1.5rem, h3: 1.1875rem). Recommend keeping the site's current, denser scale but switching the font family and weights to match Inter's semi-bold/medium hierarchy rather than importing the full desktop scale verbatim.

**Body text scale:**

| Size | rem | Line-height |
|---|---|---|
| L | 1.25rem | 1.75rem |
| M (default) | 1.125rem | 1.75rem |
| S | 1rem | 1.5rem |
| XS | 0.875rem | 1.25rem |

**Readability rule:** limit line length to ≤80 characters (desktop), 40–60 characters (mobile).

**Full weight scale:** thin 100, extra-light 200, light 300, semi-regular 350, regular 400, medium 500, semi-bold 600, near-bold 650, bold 700, extra-bold 800, black 900.

---

## 3. Logos

Downloaded to [`brand/logos/`](logos/):

| File | Use |
|---|---|
| `logo-ec-en-positive.svg` | Full-colour EC logo, for light backgrounds (e.g. footer if light, or a light header) |
| `logo-ec-en-negative.svg` | White/negative version, for dark backgrounds (matches this site's dark `.site-header`) |
| `logo-ec-mute.svg` | Muted/monochrome variant |
| `logo-ec-mute-negative.svg` | Muted monochrome, negative |

37 additional language variants exist in the source repo if the site ever needs to be multilingual (not pulled — English only, matching current site content).

No explicit minimum-size or clear-space specification was published on the guideline page — that level of detail (if it exists) would be in a PDF brand manual outside the component library, not in ECL.

---

## 4. Spacing

Fixed (non-responsive) scale, base unit is a vertical-rhythm baseline:

| Token | rem | px |
|---|---|---|
| 5xs | 0.0625 | 1 |
| 4xs | 0.125 | 2 |
| 3xs | 0.25 | 4 |
| 2xs | 0.375 | 6 |
| xs | 0.5 | 8 |
| s | 0.75 | 12 |
| m | 1 | 16 |
| l | 1.25 | 20 |
| xl | 1.5 | 24 |
| 2xl | 1.75 | 28 |
| 3xl | 2 | 32 |
| ... | ... | up to 13xl = 4.5rem / 72px |

**Rule:** always use a scale value for margin/padding; never combine two scale values to make a third; not responsive by design.

---

## 5. Grid / layout

```css
/* Breakpoints */
xs: 0, s: 480px, m: 768px, l: 996px, xl: 1140px

/* Container max-widths */
s: 768px, m: 996px, l: 1140px, xl: 1368px

/* Grid gutters */
gutter-s: 1rem, gutter-m: 1.5rem, gutter-l: 2rem
columns: 12
```

This site's current `--max-width: 1060px` sits between ECL's `l` (996px) and `xl` (1140px) containers — close enough, no change needed unless full grid alignment matters.

---

## 6. Shape (border-radius & shadow)

```css
--radius-2xs: 1px;  --radius-xs: 2px;  --radius-s: 4px;
--radius-m: 8px;    --radius-l: 12px;

--shadow-1: 0 0 0.5px 0.5px rgba(24,39,75,.08), 0 6px 12px 0 rgba(24,39,75,.08);
--shadow-2: 0 0 0.5px 0.5px rgba(24,39,75,.08), 0 10px 22px 0 rgba(24,39,75,.1);
```
(shadow-3/4/5 scale further for deeper elevation — see [`tokens/`](tokens/) if needed)

This site currently uses sharp corners (no border-radius) throughout, GOV.UK-style. Switching to ECL's `s` (4px) or `m` (8px) radius on cards/buttons would visually signal the EC rebrand most clearly — worth deciding deliberately rather than defaulting.

---

## 7. Icons

Sizes: 2xs 1rem, xs 1.125rem, s 1.25rem, m 1.5rem, l 2rem, xl 2.5rem, 2xl 3rem.

ECL ships an icon set (directional, status, media, functional) plus social-media and EU member-state flag icons, coloured via text-colour utility classes (icons are neutral grey by default, then tinted with CSS). No icons are currently used on this site (text-only reference guide) — flagging in case the rebuild wants a "download," "external link," or status icon set.

---

## 8. Use of images

- Only include images that add value; place near the relevant text, most important image near the top.
- Photographs for dynamic/news content, illustrations for static/topic content, infographics for complex data — not much applicable here since this site's images are all `Figure-N.png` diagrams from the source regulation document.
- **Alt text is required** for every image (this site's `_replace_figures` already sets `alt` from the figure caption — good, no change needed).
- Avoid embedding text inside images (not accessible).
- No visual effects (gradients, drop shadows, rounded corners, filters) on photos.
- Format guidance: JPEG 60–75% quality for photos, PNG for high-contrast/transparency, SVG for anything vector.

---

## 9. Component token mapping (existing site classes → EC component)

| This site's class | ECL equivalent | Key resolved tokens |
|---|---|---|
| `.site-header` | site-header | background `--cm-surface-inverted` region + dark banner `#00002E`; logo height 3rem mobile → 5rem desktop |
| `.top-nav` | menu (desktop) | background `#0046FF` (brand), current item background = neutral-medium, link colour white |
| `.breadcrumbs` | breadcrumb | link colour = brand navy `#00002E`, underlined |
| `.phase-banner` | notification (info variant) | background `#EBEFF7` (info-75), text/border `#003399` (info-800) |
| `.sidebar` / `.contents-box` | inpage-navigation / navigation-list | shadow-1, radius `xs` (2px), background white |
| `.chapter-grid .chapter-card` | card | radius `s` (4px), shadow-1, 4px bottom border in `--cm-border-medium`, body padding `l`/`m` |
| `.article table` | table | header background = grey-low-2, cell border = grey-low, zebra striping = surface-low-1 |
| `.chapter-nav` (prev/next) | pagination | current-item background = primary `#0046FF`, hover background = primary-20%-tint |
| `.footnotes-show-more` (expand button) | expandable | active toggle background = primary-lowest tint, radius `xs` |
| `.search-form` | search-form | button/separator colour = brand navy, border on focus |
| `.site-footer` | site-footer | background `#0046FF`-adjacent brand fill or navy `#00002E`? (ECL default uses `--cm-surface-brand` = navy), text white, link hover = grey |

Full raw token dumps (SCSS, with all state variants — hover/active/focus/disabled) for button, link, tag, and the components above are preserved in [`tokens/`](tokens/) for reference during implementation.

---

## Not pulled (not applicable to this site)

- Full icon SVG set / flag icon set — no icons in current design; can fetch specific icons on demand if the redesign wants them.
- Full 16-variant colour-mode palette (Blue-navy, Purple-violet, etc.) — single-theme site, doesn't need the accent-switcher system.
- eUI / Webtools resource sections — these are for interactive JS widgets (Webtools showcase), not relevant to a static Markdown-to-HTML site.
- Component markup/HTML/Twig templates — this site has its own hand-rolled HTML generator ([build_site.py](../build_site.py)); only tokens (colour/spacing/type) are portable, not ECL's component markup.
