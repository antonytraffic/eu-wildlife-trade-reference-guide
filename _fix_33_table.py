import pathlib

p = pathlib.Path(r"output\03_3_what_are_the_rules_for_the_issuance_of_import_perm.md")
data = p.read_bytes()

# Locate the section to replace
START_MARKER = b'<div class=\xe2\x80\x9dtable-wrap'
END_MARKER   = b'A* and B*: Does not apply to re-imports'

start = data.find(START_MARKER)
end   = data.find(END_MARKER)
line_end = data.find(b'\r\n', end) + 2  # include the trailing \r\n

if start == -1 or end == -1:
    print("ERROR: markers not found", start, end)
    raise SystemExit(1)

# Extract the tbody block which we preserve verbatim
tbody_start = data.find(b'<tbody>', start)
tbody_end   = data.find(b'</tbody>', start) + len(b'</tbody>')
tbody_bytes = data[tbody_start:tbody_end]

# The curly-quote "taking into account..." text inside tbody is kept as-is.

NEW_CONTENT = (
    b'<table>\n'
    b'<thead>\n'
    b'<tr>\n'
    b'  <th colspan="2" style="width:16%">Annex</th>\n'
    b'  <th>Conditions<sup>1</sup></th>\n'
    b'</tr>\n'
    b'</thead>\n'
    + tbody_bytes.replace(b'\r\n', b'\n') +
    b'\n</table>\n'
    b'\n'
    b'Note: A* and B*: Does not apply to re-imports and worked specimens acquired more than 50 years before the EU Wildlife Trade Regulations came into effect, i.e. before 3 March 1947 (see **Section 3.6.3**) (Article 4(5) *Regulation (EC) No 338/97*)\n'
)

# Build the new file: everything before the table + new content + everything after
before = data[:start]
after  = data[line_end:]

result = before.replace(b'\r\n', b'\n') + NEW_CONTENT + after.replace(b'\r\n', b'\n')

p.write_bytes(result)
print(f"OK - replaced bytes {start}..{line_end}, file is now {len(result)} bytes")
