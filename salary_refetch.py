"""
Salary Refetch Pipeline (Bertahap / Incremental)

Srap ulang kolom salary untuk lowongan EXISTING yang belum punya info gaji.
- Berjalan bertahap: default 250 URL per run (dibatasi agar tidak kena block IP).
- Rate limit per platform (delay antar request, lebih agresif untuk LinkedIn).
- Checkpoint: URL yang sudah dicoba disimpan di salary_refetch_progress.json
  sehingga run berikutnya melanjutkan dari URL yang belum diproses.
- Prioritas: ambil salary LANGSUNG dari halaman portal (link aktif).
  Jika link mati/kadaluarsa -> fallback ekstrak dari deskripsi yang tersimpan.
- Setiap batch langsung di-upsert ke Supabase (praktis & cepat).

Cara pakai:
  python -u salary_refetch.py --batch-size 250
  # Bila ingin mulai ulang progres dari nol:
  python -u salary_refetch.py --batch-size 250 --reset
"""
import os
import json
import re
import time
import random
import argparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(PROJECT_PATH, "jobs_data.json")
PROGRESS_FILE = os.path.join(PROJECT_PATH, "salary_refetch_progress.json")

# Rate-limit delay per platform (detik antar request)
PLATFORM_DELAY = {
    "glints": 3.0,
    "kalibrr": 2.0,
    "jobstreet": 4.0,
    "linkedin": 8.0,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Salary extraction dari halaman portal (mirip scraper.py extract_salary)
# ---------------------------------------------------------------------------

def extract_salary_from_soup(soup, platform):
    platform = (platform or "").lower()

    # Selector khusus Jobstreet
    if platform == "jobstreet":
        elem = soup.find(attrs={"data-automation": "job-detail-salary"})
        if elem and elem.text.strip():
            return elem.text.strip()

    # Heuristik umum: elemen pendek yang mengandung mata uang + angka
    currency = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'
    candidates = []
    for tag in ["span", "div", "p", "h3", "h4"]:
        for elem in soup.find_all(tag):
            text = elem.text.strip()
            if not text or len(text) > 100:
                continue
            if re.search(currency + r'\d+[\d\.,\s\-–—jt]*', text, re.I):
                candidates.append(text)

    for cand in candidates:
        if re.search(r'(bulan|month|year|tahun|/month|/bulan|/yr|/thn|per\s*month|per\s*bulan)', cand, re.I):
            match = re.search(
                currency + r'\d+[\d\.,\s\-–—jt]*\s*([/]\s*(bulan|month|year|tahun|yr|thn)|per\s*(month|bulan))?',
                cand, re.I,
            )
            if match:
                return match.group(0).strip()
            return cand

    for cand in candidates:
        if re.search(currency + r'\d+[\d\.,\s\-–—jt]+', cand, re.I):
            if len(cand) < 60:
                match = re.search(currency + r'\d+[\d\.,\s\-–—jt]*', cand, re.I)
                if match:
                    return match.group(0).strip()
                return cand
    return None


# ---------------------------------------------------------------------------
# Ekstraksi salary dari deskripsi (fallback saat link mati)
# ---------------------------------------------------------------------------

def extract_salary_from_text(text):
    """Fallback parser — versi konservatif yang hanya percaya pola salary kuat."""
    if not text:
        return None

    CURRENCY = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'
    OPT_CURRENCY = r'(?:{})?'.format(r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*')
    NUM = r'\d[\d\.,]*(?:\s*(?:juta|jt|rb|ribu))?'
    RANGE_SEP = r'\s*(?:[-–—]|to|s/d|sd)\s*'
    FREQ = (r'(?:\s*(?:/\s*)?(?:bulan|month|mo|hari|day|jam|hour|hr|yr|thn|tahun|'
            r'minggu|week|wk|bln)|per\s*(?:bulan|month|hari|day|jam|hour|tahun|minggu|week)|'
            r'bulanan|monthly|tahunan|annual|per\s*annum|per\s*year)')
    LABELS = (r'(?:gaji|salary|take\s*home\s*pay|thp|remunerasi|kompensasi|rate|fee|upah|'
              r'income|earning|revenue|budget|range|kisaran|maksimal|maksimum|maximum|'
              r'up\s*to|minimal|minimum|mulai\s*dari|dari|start\s*from|between)')
    SALARY_LABELS = r'(?:gaji|salary|take\s*home\s*pay|thp|remunerasi|kompensasi|upah)'
    CURRENCY_CODE = r'(?:Rp|IDR|SGD|USD|EUR|AUD)'

    patterns = [
        re.compile(
            rf'{LABELS}\s*[:\-–—]?\s*{CURRENCY}{NUM}(?:{RANGE_SEP}{OPT_CURRENCY}{NUM})?\s*{FREQ}?',
            re.I,
        ),
        re.compile(
            rf'{CURRENCY}{NUM}{RANGE_SEP}{OPT_CURRENCY}{NUM}\s*{FREQ}?',
            re.I,
        ),
        re.compile(
            rf'{CURRENCY}{NUM}\s*(?:{CURRENCY_CODE})?\s*{FREQ}',
            re.I,
        ),
        re.compile(
            rf'{SALARY_LABELS}\s*[:\-–—]?\s*{NUM}(?:{RANGE_SEP}{NUM})?\s*(?:juta|jt|rb|ribu)?\s*(?:{FREQ})?',
            re.I,
        ),
        re.compile(
            rf'{SALARY_LABELS}\s*[:\-–—]?\s*{NUM}\s*(?:juta|jt|rb|ribu)\s*{FREQ}',
            re.I,
        ),
    ]

    for pat in patterns:
        match = re.search(pat, text)
        if match:
            val = match.group(0).strip()
            if len(val) < 70:
                return val
    return None


# ---------------------------------------------------------------------------
# Supabase helper
# ---------------------------------------------------------------------------

def get_supabase():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY tidak ditemukan di .env")
    return create_client(url, key)


def upload_salary_batch(supabase, records):
    """Upsert kolom salary saja untuk daftar lowongan (job_url -> salary)."""
    if not records:
        return
    batches = [records[i:i + 100] for i in range(0, len(records), 100)]
    for b in batches:
        supabase.table("jobs").upsert(b, on_conflict="job_url").execute()


# ---------------------------------------------------------------------------
# Progress / checkpoint
# ---------------------------------------------------------------------------

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Proses satu job
# ---------------------------------------------------------------------------

def process_job(job):
    url = job.get("job_url")
    if not url:
        return None, "no-url"

    platform = (job.get("platform") or "unknown").lower()

    # 1) Coba ambil salary langsung dari halaman portal (link aktif)
    page_status = None
    salary = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        page_status = resp.status_code
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            salary = extract_salary_from_soup(soup, platform)
    except Exception:
        page_status = "error"

    if salary:
        return salary, f"page(status={page_status})"

    # 2) Link mati / halaman tak ada elemen salary -> fallback deskripsi
    desc_salary = extract_salary_from_text(job.get("description", "") or "")
    if desc_salary:
        return desc_salary, f"desc(status={page_status})"

    return None, f"none(status={page_status})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=250,
                        help="Jumlah URL yang diproses per run (default: 250)")
    parser.add_argument("--reset", action="store_true",
                        help="Reset progress checkpoint untuk mulai dari awal")
    args = parser.parse_args()

    # Muat jobs
    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    progress = load_progress()
    if args.reset:
        progress = {}
        save_progress(progress)
        print(">>> Progress checkpoint di-reset.")

    processed_urls = set(progress.keys())

    # Kandidat: job tanpa salary & URL belum pernah dicoba
    pending = [
        j for j in jobs
        if not j.get("salary")
        and j.get("job_url")
        and j["job_url"] not in processed_urls
    ]
    batch = pending[:args.batch_size]

    print(f"Total jobs di database : {len(jobs)}")
    print(f"Sudah punya salary      : {sum(1 for j in jobs if j.get('salary'))}")
    print(f"Belum diproses          : {len(pending)}")
    print(f"Batch run ini           : {len(batch)}")
    print()

    if not batch:
        print("Tidak ada URL tersisa. Semua sudah diproses atau semua sudah punya salary.")
        return

    supabase = get_supabase()
    stats = {"page": 0, "desc": 0, "none": 0, "error": 0}
    to_upload = []

    last_platform = None
    for idx, job in enumerate(batch, 1):
        url = job["job_url"]
        platform = (job.get("platform") or "unknown").lower()

        # Rate limit: delay antar request (per platform)
        delay = PLATFORM_DELAY.get(platform, 2.0)
        if last_platform and platform != last_platform:
            delay += 1.5  # jeda ekstra saat ganti platform
        time.sleep(delay + random.uniform(0.5, 1.5))
        last_platform = platform

        salary, source = process_job(job)

        if salary:
            job["salary"] = salary
            to_upload.append({
                "job_url": url,
                "salary": salary,
            })
            if source.startswith("page"):
                stats["page"] += 1
            else:
                stats["desc"] += 1
        else:
            if "error" in source:
                stats["error"] += 1
            else:
                stats["none"] += 1

        # Tandai URL sudah diproses (termasuk yang gagal, agar tidak diulang)
        progress[url] = {
            "salary": salary,
            "source": source,
            "ts": time.time(),
        }

        if idx % 50 == 0:
            print(f"  Progress: {idx}/{len(batch)} "
                  f"| page={stats['page']} desc={stats['desc']} "
                  f"none={stats['none']} error={stats['error']}", flush=True)

    # Simpan jobs_data.json (perbarui salary)
    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=4, ensure_ascii=False)

    # Simpan checkpoint progress
    save_progress(progress)

    # Upload batch ke Supabase
    if to_upload:
        print(f"\nMengunggah {len(to_upload)} salary ke Supabase...")
        try:
            upload_salary_batch(supabase, to_upload)
            print("Upload ke Supabase berhasil.")
        except Exception as e:
            print(f"Upload ke Supabase gagal: {e} — data tetap tersimpan di jobs_data.json.")
    else:
        print("\nTidak ada salary baru untuk diunggah.")

    total_with = sum(1 for j in jobs if j.get("salary"))
    print(f"\n=== Ringkasan run ===")
    print(f"  Salary dari halaman portal: {stats['page']}")
    print(f"  Salary fallback deskripsi : {stats['desc']}")
    print(f"  Tidak ditemukan (link mati): {stats['none']}")
    print(f"  Error request/gagal       : {stats['error']}")
    print(f"  Total dengan salary       : {total_with}/{len(jobs)} "
          f"({total_with / len(jobs) * 100:.2f}%)")


if __name__ == "__main__":
    main()