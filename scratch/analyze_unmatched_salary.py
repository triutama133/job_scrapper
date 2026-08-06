import json
import re
import sys
sys.path.insert(0, 'scratch')
from extract_salaries_from_desc import extract_salary_from_text

with open('jobs_data.json', 'r', encoding='utf-8') as f:
    jobs = json.load(f)

# Only jobs WITHOUT salary
no_salary = [j for j in jobs if not j.get('salary')]
print(f"Jobs tanpa salary: {len(no_salary)} / {len(jobs)}")

# Scan descriptions for obvious salary signals but extraction returned None
SIGNALS = re.compile(r'(gaji|salary|take\s*home\s*pay|thp|upah|\brp\.?|\bidr|\busd|\beur|\baud|\bsgd|\$\s*\d|juta|jt\b|/bulan|per\s*bulan|per\s*month|/month)', re.I)

matched_signal = 0
failed_candidates = []
for job in no_salary:
    desc = job.get('description', '') or ''
    if not SIGNALS.search(desc):
        continue
    matched_signal += 1
    result = extract_salary_from_text(desc)
    if result is None:
        # Has salary signal but extraction failed - potential missed opportunity
        failed_candidates.append(job)

print(f"Jobs tanpa salary tapi deskripsi mengandung sinyal gaji: {matched_signal}")
print(f"  -> Dari jumlah itu, yang TIDAK berhasil di-extract: {len(failed_candidates)}")

# Show a few examples to understand why they failed
print("\nContoh 10 yang terlewat (deskripsi 150 char):")
for job in failed_candidates[:10]:
    desc = (job.get('description', '') or '')[:150].replace('\n', ' ')
    print(f"  [{job.get('company')} | {job.get('platform')}] {desc}")