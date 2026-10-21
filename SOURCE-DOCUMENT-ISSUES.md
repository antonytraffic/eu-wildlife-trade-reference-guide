# Source document issues

Defects found in the original source document (`data/CITES Reference Guide_Nov 2025 FIN_clean.pdf` /
`.docx`) itself while rebuilding the website's Annexes -- not website/extraction bugs, so not
something fixable on the site side. Logged here for reference back to the document owner.

## Annex XVII (Measuring reptiles) -- "Fig. 4 and 5" referenced but never included

Body text for snakes and crocodiles says measurements can be done "from above with a measure tape
(Fig. 4 and 5)", but the source PDF only contains three images for this annex (Figures 1-3, on
pages 206-207 -- confirmed via a full embedded-image scan of pages 122-216). No Figure 4 or 5
exists anywhere in the document. Left as plain, unlinked text on the site rather than fabricating
or removing the reference.

## Annex VII (Standard references for nomenclature) -- mislabeled family for 5 species

Source PDF p.142: five species -- *Cheirogaleus andysabini*, *Cheirogaleus lavasoensis*,
*Cheirogaleus chethi*, *Microcebus gerpi*, *Microcebus marohita* (all mouse/dwarf lemurs,
correct family **Cheirogaleidae**) -- are labelled **Cercopithecidae** in the Family column,
sandwiched between rows for *Microcebus ganzhorni*/*manitatra* and *Microcebus tanosi* that
correctly say "Cheirogaleidae". Confirmed against the source PDF text directly (not an
extraction artifact -- the PDF itself has the wrong label). Per your instruction, the website
now shows the corrected family ("Cheirogaleidae") for all 5 rows rather than reproducing the
error; the source PDF/DOCX's Annex VII table should be corrected to match.

## Annex VIII / Annex IX -- duplicated "purpose of transaction" codes table

Annex IX (p.163, "Codes for the indication in permits and certificates of the source of specimens
... Article 5(6)") opens with a full repeat of the "1. Codes for the indication ... purpose of a
transaction, referred to in Article 5(5)" table -- which is also, in full, Annex VIII's own and
only content (p.162). Confirmed directly against the source PDF text (not an extraction artifact).
Reproduced faithfully on the site as-is; flagging in case the document owner wants to de-duplicate
by trimming Annex IX down to just its "2. Codes ... source of specimens" section, with a
cross-reference to Annex VIII for the purpose codes instead.

## Annex tables -- defects found while rebuilding them from the DOCX (fix_annex_tables.py)

All confirmed in the source DOCX/PDF itself; corrected on the site as noted.

- **Annex VII, broken URLs**: the Reptile Database checklist link appears as
  `..._CoP18lr_CITES. pdf` / `..._CoP18lr_CITE S.pdf` (stray space), and the ZooBank
  reference as `http://zoobank.orgurn:lsid:...` (missing `/`). Site uses the evident
  correct Reptile Database URL; the corrected ZooBank URL is dead too, so the site links
  the article's DOI (10.1093/zoolinnean/zlz046) instead.
- **Annex VII, last class heading**: "ANTHOZOA & HYDROZOA" sits in the Order column under an
  empty full-width bar instead of being the bar's heading. Site shows it as a class
  sub-header like CEPHALOPODA etc.
- **Annex IX, "Pre-Convention¹"**: superscript 1 with no matching note anywhere on the page.
  Site omits the marker (as before).
- **Annex XII, Attachment J table**: "unsustainable trade12." -- a stray footnote number
  typed into the cell text. Site drops the "12".
- **Annex XII, typos in the permits table**: "Article5.2 (d)" and "the competent Sa".
  Site corrects both.
- **Annex XII, footnotes**: the Res. Conf. 12.10 footnote displays one URL but links to
  speciesplus.net. Site links the displayed URL.

## Dead links replaced on the site (October 2026)

Found with `python check_links.py --external` and checked in a browser. The source
document still has the old addresses.

- **Section 9.1.2**: Res. Conf. 10.3 link `http://www.cites.org/eng/res/10/10-03C15.php` ->
  official CITES page `https://cites.org/eng/node/134745`.
- **Annex VII, Chamaeleonidae**: Glaw (2015) Senckenberg PDF (404) -> DOI
  `https://doi.org/10.3897/vz.65.e31518`.
- **Annex VII, Brachypelma**: ZooBank LSID (404) -> DOI `https://doi.org/10.1093/zoolinnean/zlz046`.
- **Annex VII, Theraphosidae**: `spider_checklist.pdf` (also had a stray `]` in the link;
  removed from cites.org) -> Internet Archive copy of the original file (2011 capture).
- **Annex XII, purpose code N**: IUCN re-introduction guidelines `iucnsscrsg.org/images/English.pdf`
  (1998 guidelines; site dead) -> IUCN library page for the 2013 *Guidelines for
  Reintroductions and Other Conservation Translocations*, which replaced them.

Checked and fine (the automated check misreports them): the three CIRCABC links and the
footer's Bluesky link, which only answer properly in a browser.
