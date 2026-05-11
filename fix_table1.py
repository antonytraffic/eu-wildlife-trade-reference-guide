import re, pathlib

path = pathlib.Path('output/02_what_species_are_covered_by_the_regulations_and_in_what_wa.md')
text = path.read_bytes()

old = b'Table 1: Numbers of species and subspecies listed in the CITES Appendices, updated 10 July 2025'
new = b'*Table 1: Numbers of species and subspecies listed in the CITES Appendices, updated 10 July 2025*'

assert old in text, 'Pattern not found'
text = text.replace(old, new, 1)
path.write_bytes(text)
print('Done')
