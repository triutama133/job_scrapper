import json
import re

filepath = "jobs_data.json"

# Currency prefix: supports Rp, Rp., IDR, IDR., SGD, SGD., USD, EUR, AUD, S$, $
CURRENCY = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'

# Optional currency wrapped in a non-capturing group so the whole pattern is optional
OPT_CURRENCY = r'(?:{})?'.format(r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*')

# Currency code that may appear AFTER the number (e.g. "$2,000 USD annual")
CURRENCY_CODE = r'(?:Rp|IDR|SGD|USD|EUR|AUD)'

# A number: 5.000.000 | 5000000 | 4,500,000 | optionally followed by unit (juta/jt/ribu/rb)
NUM = r'\d[\d\.,]*(?:\s*(?:juta|jt|rb|ribu))?'

# Range separator: dash variants, or the words "to" / "s/d" / "sd"
RANGE_SEP = r'\s*(?:[-–—]|to|s/d|sd)\s*'

# Frequency suffix (including short forms: /mo, /hr, /wk, /thn, /bln, /jt)
FREQ = r'(?:\s*(?:/\s*)?(?:bulan|month|mo|hari|day|jam|hour|hr|yr|thn|tahun|minggu|week|wk|bln)|per\s*(?:bulan|month|hari|day|jam|hour|tahun|minggu|week)|bulanan|monthly|tahunan|annual|per\s*annum|per\s*year)'

# Labels that introduce a salary value
LABELS = r'(?:gaji|salary|take\s*home\s*pay|thp|remunerasi|kompensasi|rate|fee|upah|income|earning|revenue|budget|range|kisaran|maksimal|maksimum|maximum|up\s*to|minimal|minimum|mulai\s*dari|dari|start\s*from|between)'

# Stricter labels for patterns WITHOUT currency: only unambiguous salary words,
# so "minimal 1-2 tahun pengalaman" or "maksimal 25 karyawan" don't false-match.
SALARY_LABELS = r'(?:gaji|salary|take\s*home\s*pay|thp|remunerasi|kompensasi|upah)'


def extract_salary_from_text(text):
    if not text:
        return None

    patterns = [
        # 1. Label + currency + number [range] [freq]
        #    "Gaji: Rp. 5.000.000 - 8.000.000"
        #    "Gaji mulai dari Rp 3.000.000"
        #    "Salary IDR 6.000.000 - 8.000.000 per month"
        re.compile(
            rf'{LABELS}\s*[:\-–—]?\s*{CURRENCY}{NUM}(?:{RANGE_SEP}{OPT_CURRENCY}{NUM})?\s*{FREQ}?',
            re.I
        ),
        # 2. Currency + number + range + currency? + number [freq]
        #    "Rp 4.000.000 - Rp 6.000.000 per bulan"
        #    "Rp. 10.000.000 - 15.000.000"
        re.compile(
            rf'{CURRENCY}{NUM}{RANGE_SEP}{OPT_CURRENCY}{NUM}\s*{FREQ}?',
            re.I
        ),
        # 3. Currency + single number + [currency code] + freq (no label / no range)
        #    "Rp800.000 per bulan"
        #    "$34 per hour"
        #    "$2,000 USD annual"
        re.compile(
            rf'{CURRENCY}{NUM}\s*(?:{CURRENCY_CODE})?\s*{FREQ}',
            re.I
        ),
        # 4. Salary label + number + "juta" (without explicit currency)
        #    "Gaji 5 - 10 juta" | "Gaji mulai dari 3 juta"
        #    NOTE: Requires "juta/jt" unit or a frequency suffix so that
        #    "minimal 3 tahun pengalaman" doesn't match.
        re.compile(
            rf'{SALARY_LABELS}\s*[:\-–—]?\s*{NUM}(?:{RANGE_SEP}{NUM})?\s*(?:juta|jt|rb|ribu)?\s*(?:{FREQ})?',
            re.I
        ),
        # 5. Label + number + frequency (e.g. "Gaji 5 juta/bln", "salary 8jt/bulan")
        #    Still requires a salary label to avoid "minimal 1 tahun" matches.
        re.compile(
            rf'{SALARY_LABELS}\s*[:\-–—]?\s*{NUM}\s*(?:juta|jt|rb|ribu)\s*{FREQ}',
            re.I
        ),
    ]

    for pat in patterns:
        match = re.search(pat, text)
        if match:
            val = match.group(0).strip()
            if len(val) < 70:
                return val

    return None


def process():
    print("Loading jobs_data.json...")
    with open(filepath, 'r', encoding='utf-8') as f:
        jobs = json.load(f)

    already_had = sum(1 for j in jobs if j.get("salary"))
    extracted_count = 0
    for job in jobs:
        if job.get("salary"):
            continue
        desc = job.get("description", "")
        salary = extract_salary_from_text(desc)
        if salary:
            job["salary"] = salary
            extracted_count += 1

    total_with_salary = already_had + extracted_count
    print(f"Sudah punya salary sebelumnya: {already_had}")
    print(f"Berhasil ekstrak salary baru: {extracted_count}")
    print(f"Total dengan salary: {total_with_salary} / {len(jobs)} jobs ({total_with_salary/len(jobs)*100:.2f}%)")

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=4, ensure_ascii=False)
    print("Saved updated jobs_data.json.")


if __name__ == "__main__":
    process()