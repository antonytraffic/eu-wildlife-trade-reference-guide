"""Fix missing figure placeholders and captions in markdown source files."""
import pathlib

# ── File 1: 03_3 (Figures 1, 2, 3) ────────────────────────────────────────

p1 = pathlib.Path('output/03_3_what_are_the_rules_for_the_issuance_of_import_perm.md')
text = p1.read_bytes()

# Detect line ending
lf = b'\r\n' if b'\r\n' in text else b'\n'

# --- Figure 1: insert after the bullet list, before "The procedures described"
old1 = (
    b'- the applicant must be informed of **significant delays**[^3].' + lf +
    lf +
    b'The procedures described'
)
new1 = (
    b'- the applicant must be informed of **significant delays**[^3].' + lf +
    lf +
    b'*Figure 1: A simplified procedure for obtaining an import permit for Annex A and B specimens*' + lf +
    lf +
    b'[Insert Figure 1]' + lf +
    lf +
    b'The procedures described'
)
assert old1 in text, 'Figure 1 anchor pattern not found'
text = text.replace(old1, new1, 1)

# --- Figure 2: insert after the Note about Figure 2, before the numbered list
old2 = (
    b'- see also **Figure 2**.)' + lf +
    lf +
    b'1. **Exporter/re-exporter:**'
)
new2 = (
    b'- see also **Figure 2**.)' + lf +
    lf +
    b'*Figure 2: Annotated import permit form*' + lf +
    lf +
    b'[Insert Figure 2]' + lf +
    lf +
    b'1. **Exporter/re-exporter:**'
)
assert old2 in text, 'Figure 2 anchor pattern not found'
text = text.replace(old2, new2, 1)

# --- Figure 3 caption: convert bold caption to italic single line
old3_cap = (
    b'**Figure 3:** **Overview of procedures to establish Positive and Negative Opinions and import**   **restrictions for species listed in Annex A or B of the EU Wildlife Trade Regulations***'
)
new3_cap = (
    b'*Figure 3: Overview of procedures to establish Positive and Negative Opinions and import restrictions for species listed in Annex A or B of the EU Wildlife Trade Regulations*'
)
assert old3_cap in text, 'Figure 3 caption pattern not found'
text = text.replace(old3_cap, new3_cap, 1)

# --- Figure 3 placeholder: unescape \[Insert flowchart\]
old3_ph = b'\\[Insert flowchart\\]'
new3_ph = b'[Insert Figure 3]'
assert old3_ph in text, 'Figure 3 placeholder pattern not found'
text = text.replace(old3_ph, new3_ph, 1)

p1.write_bytes(text)
print('03_3: Figures 1, 2, 3 fixed')

# ── File 2: 11 (Figure 16) ─────────────────────────────────────────────────

p2 = pathlib.Path('output/11_how_are_cites_duties_organised_at_national_and_eu_levels_bet.md')
text2 = p2.read_bytes()
lf2 = b'\r\n' if b'\r\n' in text2 else b'\n'

# Insert Figure 16 between "## 11.2 Which bodies operate at EU level?" and next heading
old16 = (
    b'## 11.2 Which bodies operate at EU level?' + lf2 +
    lf2 +
    b'### 11.2.1'
)
new16 = (
    b'## 11.2 Which bodies operate at EU level?' + lf2 +
    lf2 +
    b'*Figure 16: EU-level bodies for CITES implementation*' + lf2 +
    lf2 +
    b'[Insert Figure 16]' + lf2 +
    lf2 +
    b'### 11.2.1'
)
assert old16 in text2, 'Figure 16 anchor pattern not found'
text2 = text2.replace(old16, new16, 1)

p2.write_bytes(text2)
print('11: Figure 16 fixed')
print('All done.')
