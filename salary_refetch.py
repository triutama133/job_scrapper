"""
Salary Refetch Pipeline (Bertahap / Incremental)

Srap ulang kolom salary untuk lowongan EXISTING yang belum punya info gaji.
- Berjalan bertahap: default 150 URL per run (dibatasi agar tidak kena block IP).
- Rate limit per platform (delay antar request, lebih agresif untuk LinkedIn).
- Checkpoint: URL yang sudah dicoba disimpan di salary_refetch_progress.json
  sehingga run berikutnya melanjutkan dari URL yang belum diproses.
- Prioritas: ambil salary LANGSUNG dari halaman portal (link aktif).
  Jika link mati/kadaluarsa -> fallback ekstrak dari deskripsi yang tersimpan.
- Setiap batch langsung di-upsert ke Supabase (praktis & cepat).

Cara pakai:
  python -u salary_refetch.py --batch-size 150
  # Bila ingin mulai ulang progres dari nol:
  python -u salary_refetch.py --batch-size 150 --reset
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


def clean_salary_text(text):
    """Normalisasi & bersihkan teks salary hasil ekstraksi.

    - Hapus non-breaking space (\\xa0) dan ratakan spasi berlebih.
    - Hapus separator rentang menggantung di akhir (range tidak lengkap):
      'Rp 6.500.000 -' -> 'Rp 6.500.000'
    - Hapus desimal (koma/titik) yang diikuti spasi/akhir string saja:
      '6.500.000,00' -> '6.500.000' ; '10.000.000,00 - 15.000.000,00' -> '10.000.000 - 15.000.000'
      PENTING: JANGAN menghapus pemisah ribuan IDR ('.000') seperti di '7.000.000'.
    """
    if not text:
        return None
    t = text.replace('\xa0', ' ').strip()
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'\s*[-–—]\s*$', '', t)          # trailing range sep
    # Hapus desimal .00 / ,00 (atau .0 / ,0) di akhir angka saja secara aman
    t = re.sub(r'[,.]00?(?!\d)', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t if t else None



# ---------------------------------------------------------------------------
# Salary extraction dari halaman portal (mirip scraper.py extract_salary)
# ---------------------------------------------------------------------------

def format_salary_from_schema(currency, min_val, max_val, unit_val):
    if not currency:
        currency = 'IDR'
    is_idr = currency.upper() in ['IDR', 'RP']
    def format_num(val_str):
        try:
            val_int = int(float(val_str))
            if is_idr:
                return f"{val_int:,}".replace(",", ".")
            else:
                return f"{val_int:,}"
        except:
            return val_str
            
    min_f = format_num(min_val) if min_val else None
    max_f = format_num(max_val) if max_val else None
    
    freq = ""
    if unit_val:
        u = unit_val.lower()
        if u == "month":
            freq = " per month"
        elif u == "year":
            freq = " per year"
        elif u == "hour":
            freq = " per hour"
        else:
            freq = f" per {u}"
            
    if min_f and max_f and min_f != max_f:
        return f"{currency} {min_f} - {max_f}{freq}"
    elif min_f:
        return f"{currency} {min_f}{freq}"
    elif max_f:
        return f"{currency} {max_f}{freq}"
    return None


def extract_salary_from_schema(soup):
    # 1. Try Microdata (itemprop="baseSalary")
    base_salary_elem = soup.find(attrs={"itemprop": "baseSalary"})
    if base_salary_elem:
        curr_elem = base_salary_elem.find(attrs={"itemprop": "currency"})
        currency = curr_elem.text.strip() if curr_elem else base_salary_elem.get("content")
        if not currency and curr_elem:
            currency = curr_elem.get("content")
            
        min_elem = base_salary_elem.find(attrs={"itemprop": "minValue"})
        max_elem = base_salary_elem.find(attrs={"itemprop": "maxValue"})
        val_elem = base_salary_elem.find(attrs={"itemprop": "value"})
        unit_elem = base_salary_elem.find(attrs={"itemprop": "unitText"})
        
        min_val = min_elem.text.strip() if min_elem else None
        max_val = max_elem.text.strip() if max_elem else None
        val = val_elem.text.strip() if val_elem else None
        unit_val = unit_elem.text.strip() if unit_elem else None
        
        if min_elem and not min_val: min_val = min_elem.get("content")
        if max_elem and not max_val: max_val = max_elem.get("content")
        if val_elem and not val: val = val_elem.get("content")
        if unit_elem and not unit_val: unit_val = unit_elem.get("content")
        
        if val_elem and not (min_val or max_val):
            min_el = val_elem.find(attrs={"itemprop": "minValue"})
            max_el = val_elem.find(attrs={"itemprop": "maxValue"})
            unit_el = val_elem.find(attrs={"itemprop": "unitText"})
            if min_el: min_val = min_el.text.strip() or min_el.get("content")
            if max_el: max_val = max_el.text.strip() or max_el.get("content")
            if unit_el: unit_val = unit_el.text.strip() or unit_el.get("content")
            
        if not min_val and val:
            min_val = val
            
        if currency or min_val or max_val:
            formatted = format_salary_from_schema(currency, min_val, max_val, unit_val)
            if formatted:
                return formatted

    # 2. Try JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if "@graph" in data:
                    items = data["@graph"]
                else:
                    items = [data]
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                base_sal = item.get("baseSalary")
                if not base_sal and item.get("@type") == "JobPosting":
                    base_sal = item.get("baseSalary")
                if base_sal and isinstance(base_sal, dict):
                    currency = base_sal.get("currency")
                    value_obj = base_sal.get("value")
                    min_val = None
                    max_val = None
                    unit_val = None
                    val = None
                    if isinstance(value_obj, dict):
                        min_val = value_obj.get("minValue")
                        max_val = value_obj.get("maxValue")
                        val = value_obj.get("value")
                        unit_val = value_obj.get("unitText")
                    elif isinstance(value_obj, (int, float, str)):
                        val = value_obj
                    
                    if not min_val and val:
                        min_val = val
                        
                    if currency or min_val or max_val:
                        formatted = format_salary_from_schema(currency, min_val, max_val, unit_val)
                        if formatted:
                            return formatted
        except Exception:
            pass
    return None


def extract_salary_from_soup(soup, platform):
    # 1. Try high-fidelity schema-based parsing first
    schema_sal = extract_salary_from_schema(soup)
    if schema_sal:
        return schema_sal

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
            # Skip hidden/schema elements to avoid fused text issues
            classes = elem.get('class') or []
            if any(cls in classes for cls in ['hidden', 'k-hidden', 'd-none']):
                continue
            
            parent_hidden = False
            for p_parent in elem.parents:
                if p_parent.name == '[document]':
                    break
                p_class = p_parent.get('class') or []
                if any(c in p_class for c in ['hidden', 'k-hidden', 'd-none']) or p_parent.get('itemprop') == 'baseSalary':
                    parent_hidden = True
                    break
            if parent_hidden:
                continue

            text = elem.text.strip()
            if not text or len(text) > 100:
                continue
            # Reject long digit sequences
            if re.search(r'\d{9,}', text):
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

class BrowserManager:
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_camoufox = False

    def get_page(self):
        if self.page:
            return self.page
        try:
            from camoufox.sync_api import Camoufox
            print("Launching Camoufox stealth browser for refetching...")
            self.browser = Camoufox(headless=self.headless).__enter__()
            self.page = self.browser.new_page()
            self.is_camoufox = True
            return self.page
        except Exception as e:
            print(f"Camoufox init failed: {e}. Falling back to standard Playwright Chromium...")
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().__enter__()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox"]
            )
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            )
            try:
                from playwright_stealth import Stealth
                self.page = self.context.new_page()
                Stealth().apply_stealth_sync(self.page)
            except ImportError:
                self.page = self.context.new_page()
            return self.page

    def close(self):
        if self.browser:
            if self.is_camoufox:
                try:
                    self.browser.__exit__(None, None, None)
                except Exception:
                    pass
            else:
                try:
                    if self.context:
                        self.context.close()
                    self.browser.close()
                    if self.playwright:
                        self.playwright.__exit__(None, None, None)
                except Exception:
                    pass
            self.page = None
            self.browser = None
            self.context = None
            self.playwright = None


def process_job(job, browser_mgr=None):
    url = job.get("job_url")
    if not url:
        return None, "no-url"

    platform = (job.get("platform") or "unknown").lower()

    # 1) Coba ambil salary langsung dari halaman portal (link aktif)
    page_status = None
    salary = None

    use_browser = platform in ["glints", "linkedin"]

    if not use_browser:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            page_status = resp.status_code
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                salary = extract_salary_from_soup(soup, platform)
            elif resp.status_code in [403, 999, 429]:
                use_browser = True
        except Exception:
            page_status = "error"
            use_browser = True

    if use_browser and browser_mgr:
        try:
            page = browser_mgr.get_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2000)
            content = page.content()
            soup = BeautifulSoup(content, "html.parser")
            salary = extract_salary_from_soup(soup, platform)
            page_status = "browser_success"
        except Exception as e:
            page_status = f"browser_error({type(e).__name__})"

    if salary:
        return clean_salary_text(salary), f"page(status={page_status})"

    # 2) Link mati / halaman tak ada elemen salary -> fallback deskripsi
    desc_salary = extract_salary_from_text(job.get("description", "") or "")
    if desc_salary:
        return clean_salary_text(desc_salary), f"desc(status={page_status})"

    return None, f"none(status={page_status})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=150,
                        help="Jumlah URL yang diproses per run (default: 150)")
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

    browser_mgr = BrowserManager(headless=True)
    try:
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

            salary, source = process_job(job, browser_mgr)

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
    finally:
        browser_mgr.close()

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