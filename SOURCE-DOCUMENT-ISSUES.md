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
