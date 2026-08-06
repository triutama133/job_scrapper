import sys
import re
sys.path.insert(0, 'scratch')
import extract_salaries_from_desc as m

LABELS = m.LABELS
CURRENCY = m.CURRENCY
NUM = m.NUM
RANGE_SEP = m.RANGE_SEP
FREQ = m.FREQ

print("CURRENCY:", CURRENCY)
print("NUM:", NUM)
print("RANGE_SEP:", RANGE_SEP)
print()

patterns = [
    ("P1 label+currency", re.compile(rf'{LABELS}\s*[:\-–—]?\s*{CURRENCY}{NUM}(?:{RANGE_SEP}{CURRENCY}?{NUM})?\s*{FREQ}?', re.I)),
    ("P2 currency+range", re.compile(rf'{CURRENCY}{NUM}{RANGE_SEP}{CURRENCY}?{NUM}\s*{FREQ}?', re.I)),
    ("P3 currency+freq", re.compile(rf'{CURRENCY}{NUM}\s*{FREQ}', re.I)),
    ("P4 label+juta", re.compile(rf'{LABELS}\s*[:\-–—]?\s*{NUM}(?:{RANGE_SEP}{NUM})?', re.I)),
]

tests = [
    "Salary bulanan Rp. 10.000.000 - 15.000.000",
    "Range IDR 6 - 8 juta",
    "Gaji mulai dari Rp 3.000.000,-",
]

for t in tests:
    print(f"=== TEXT: {t!r}")
    for name, pat in patterns:
        mm = pat.search(t)
        if mm:
            print(f"  {name}: MATCH -> {mm.group(0)!r}")
        else:
            print(f"  {name}: no match")
    print()