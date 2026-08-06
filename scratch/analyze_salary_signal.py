import json
import re
import sys
sys.path.insert(0, 'scratch')
from extract_salaries_from_desc import extract_salary_from_text, CURRENCY, NUM

with open('jobs_data.json', 'r', encoding='utf-8') as f:
    jobs = json.load(f)

no_salary = [j for j in jobs if not j.get('salary')]
print("Jobs tanpa salary:", len(no_salary), "/", len(jobs))

currency_pat = re.compile(rf'{CURRENCY}\d[\d\.,]*', re.I)

salary_ctx = re.compile(
    r'(gaji|salary|take\s*home\s*pay|thp|upah|remunerasi|kompensasi|'
    r'per\s*bulan|/bulan|per\s*month|/month|per\s*hour|/hour|per\s*day|/day|'
    r'bulanan|monthly|per\s*tahun|tahunan|annual|/tahun|/yr|/thn)',
    re.I
)

currency_only = 0
currency_near_salary_ctx = 0
salaries_try_extract = 0

for j in no_salary:
    desc = j.get('description', '') or ''
    m = currency_pat.search(desc)
    if not m:
        continue
    currency_only += 1

    window = desc[max(0, m.start()-150):m.end()+150]
    if salary_ctx.search(window):
        currency_near_salary_ctx += 1
        if extract_salary_from_text(desc):
            salaries_try_extract += 1

print("Jobs tanpa salary dengan pola currency+digit:", currency_only)
print("  -> dekat kata kunci gaji/bulan/jam dll:", currency_near_salary_ctx)
print("  -> berhasil di-extract:", salaries_try_extract)

print("\nContoh currency+digit DEKAT konteks gaji yang GAGAL di-extract (10):")
shown = 0
for j in no_salary:
    desc = j.get('description', '') or ''
    m = currency_pat.search(desc)
    if not m:
        continue
    window = desc[max(0, m.start()-150):m.end()+150]
    if not salary_ctx.search(window):
        continue
    if extract_salary_from_text(desc):
        continue
    snippet = window[:200].replace('\n', ' ')
    print("")
    print("  [" + str(j.get('company')) + " | " + str(j.get('platform')) + "]")
    print("  ..." + snippet + "...")
    shown += 1
    if shown >= 10:
        break

print("\nContoh currency+digit yang BENAR-BENAR bukan salary (aset/perusahaan):")
shown = 0
for j in no_salary:
    desc = j.get('description', '') or ''
    m = currency_pat.search(desc)
    if not m:
        continue
    window = desc[max(0, m.start()-150):m.end()+150]
    if salary_ctx.search(window):
        continue
    snippet = window[:200].replace('\n', ' ')
    print("")
    print("  [" + str(j.get('company')) + " | " + str(j.get('platform')) + "]")
    print("  ..." + snippet + "...")
    shown += 1
    if shown >= 5:
        break