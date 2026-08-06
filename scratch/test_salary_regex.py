import sys
sys.path.insert(0, 'scratch')
from extract_salaries_from_desc import extract_salary_from_text

test_cases = [
    # (expected_hint, text, should_match)
    ("Rp. 10.000.000 - 15.000.000", "Salary bulanan Rp. 10.000.000 - 15.000.000", True),
    ("Gaji: 5 juta - 7 juta", "Gaji: 5 juta - 7 juta per bulan", True),
    ("Gaji mulai dari Rp 3.000.000,-", "Benefit :\nGaji mulai dari Rp 3.000.000,-\nKomisi Penjualan", True),
    ("Rp800.000 per bulan", "Gaji Rp800.000 per bulan", True),
    ("IDR 4,500,000 per month", "Salary: IDR 4,500,000 per month", True),
    ("$34 per hour", "Rate $34 per hour", True),
    ("Gaji 5 - 10 juta", "Gaji 5 - 10 juta tergantung pengalaman", True),
    ("Rp 4.000.000 - Rp 6.000.000", "Tawaran: Rp 4.000.000 - Rp 6.000.000", True),
    ("IDR 6 - 8", "Range IDR 6 - 8 juta", True),
    ("$520/mo", "during probation you will be paid $520/mo", True),
    ("USD 2,000 annual", "wellness perks. $2,000 USD annual Co-working", True),
    # --- False positives: should NOT match ---
    ("NO", "Rp60 triliun aset perusahaan", False),
    ("NO", "IDR 50 trillion worth of transactions annually", False),
    ("NO", "Experience managing ad budgets above IDR 10 million/month", False),
    ("NO", "We raised US\$4.5B in Series C funding", False),
    ("NO", "Minimal 1-2 tahun pengalaman sebagai Supervisor", False),
    ("NO", "Maksimal 25 karyawan dalam tim", False),
    ("NO", "Minimal 3 tahun pengalaman di bidang akuntansi", False),
    ("NO", "Dari 5 divisi perusahaan", False),
    ("NO", "Usia maksimal 35 tahun", False),
]

all_ok = True
for hint, text, should_match in test_cases:
    result = extract_salary_from_text(text)
    if should_match:
        ok = bool(result) and len(result) < 70
    else:
        ok = not result
    if not ok:
        all_ok = False
    print(f"[{'OK' if ok else 'FAIL'}] hint={hint!r} -> result={result!r}")

print()
print("ALL PASSED" if all_ok else "SOME FAILED")