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
import fcntl
import signal
import sys
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client
from salary_utils import strict_salary_range

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(PROJECT_PATH, "jobs_data.json")
PROGRESS_FILE = os.path.join(PROJECT_PATH, "salary_refetch_progress.json")
DONE_FLAG = os.path.join(PROJECT_PATH, "salary_refetch_complete.flag")  # ada = seluruh data lama sudah diproses
LOCK_FILE = JOBS_FILE + ".lock"  # lock yang sama dengan scraper.py agar tidak saling menimpa
FLUSH_EVERY = 50              # checkpoint ke disk tiap N URL (aman bila di-kill / timeout)

# Rate-limit delay per platform (detik antar request)
PLATFORM_DELAY = {
    "glints": 3.0,
    "kalibrr": 2.0,
    "jobstreet": 4.0,
    "linkedin": 8.0,
}

# Lowongan dari remote_scraper (API/feed) sudah membawa salary akurat saat scrape. Ekstraksi halaman mereka
# menghasilkan nilai palsu/terpotong (RemoteOK "80,000 - 155,000" untuk 26 lowongan, WWR "$100,000", Jobicy "USD 135",
# dst.), jadi halaman TIDAK dipakai; hanya rentang lengkap yang ada di deskripsi (salary_utils.strict_salary_range).
def is_remote_source(job):
    return job.get("source") == "remote_scraper" or (job.get("platform") or "").lower() in (
        "remoteok", "weworkremotely", "linkedin remote", "jobicy", "himalayas", "arbeitnow", "workingnomads",
        "themuse", "hackernews", "greenhouse", "lever", "ashby", "remotive", "upwork")

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


def sync_salaries_to_supabase(supabase, jobs, dry_run=False):
    """Samakan salary lokal -> Supabase. HANYA mengisi yang masih kosong (tidak menimpa nilai yang ada).

    Dulu memakai upsert(on_conflict="job_url"), tetapi tabel tidak punya UNIQUE di job_url sehingga selalu
    gagal (42P10) dan error ditelan -> salary tidak pernah sampai ke Supabase. Sekarang diff terhadap
    isi Supabase lalu UPDATE per id; karena membandingkan state, run berikutnya otomatis memperbaiki yang tertinggal.
    """
    rows, off = [], 0
    while True:
        chunk = supabase.table("jobs").select("id,job_url,salary").range(off, off + 999).execute().data
        rows += chunk
        off += 1000
        if len(chunk) < 1000:
            break
    sup = {r["job_url"]: r for r in rows}
    todo = [(sup[j["job_url"]]["id"], j["salary"]) for j in jobs
            if j.get("salary") and j.get("job_url") in sup and not sup[j["job_url"]]["salary"]]
    print(f"Supabase: {len(rows)} baris | salary lokal yang belum ada di Supabase: {len(todo)}")
    if dry_run or not todo:
        return len(todo)
    done = 0
    for id_, salary in todo:
        supabase.table("jobs").update({"salary": salary}).eq("id", id_).execute()
        done += 1
        if done % 100 == 0:
            print(f"  Supabase update {done}/{len(todo)}", flush=True)
    print(f"Supabase: {done} salary diperbarui.")
    return done


def apply_salaries_to_file(updates):
    """Gabungkan HANYA kolom salary ke jobs_data.json TERBARU di bawah lock, tulis atomik.

    Versi lama memuat seluruh file di awal lalu menimpa file dengan salinan basi di akhir (~20 menit
    kemudian, tanpa lock, tidak atomik) -> lowongan yang ditambah scraper lain selama run bisa hilang.
    Mengembalikan seluruh isi file terbaru (untuk sinkronisasi Supabase).
    """
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            fresh = json.load(f)
        changed = 0
        if updates:
            for j in fresh:
                sal = updates.get(j.get("job_url"))
                if sal and not j.get("salary"):
                    j["salary"] = sal
                    changed += 1
            tmp = f"{JOBS_FILE}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(fresh, f, indent=4, ensure_ascii=False)
            os.replace(tmp, JOBS_FILE)
    return fresh, changed


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

    if is_remote_source(job):
        rng = strict_salary_range(job.get("description", "") or "")
        return (rng, "desc(status=strict)") if rng else (None, "none(status=skipped-page)")

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

def mark_complete(reason):
    with open(DONE_FLAG, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": reason}))
    print(f"\n[SELESAI] Refetch data lama tuntas ({reason}). Penanda: {os.path.basename(DONE_FLAG)} - "
          f"run berikutnya langsung berhenti. Lowongan baru memakai salary dari scraper.")


def platform_yields(progress, url_platform):
    """Rasio keberhasilan historis per platform (dari checkpoint) untuk memprioritaskan yang produktif."""
    tried, found = {}, {}
    for url, v in progress.items():
        pl = url_platform.get(url)
        if not pl:
            continue
        tried[pl] = tried.get(pl, 0) + 1
        found[pl] = found.get(pl, 0) + (1 if v.get("salary") else 0)
    return {pl: found[pl] / tried[pl] for pl in tried}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=150,
                        help="Jumlah URL yang diproses per run (default: 150)")
    parser.add_argument("--reset", action="store_true",
                        help="Reset progress checkpoint untuk mulai dari awal")
    parser.add_argument("--include-new", action="store_true",
                        help="Sertakan juga lowongan baru (bertanda scraped_at). Default: hanya data lama tanpa salary")
    parser.add_argument("--sync-only", action="store_true",
                        help="Hanya sinkronkan salary lokal yang belum ada di Supabase (tanpa scraping)")
    parser.add_argument("--dry-run", action="store_true", help="Dengan --sync-only: hitung saja, jangan menulis")
    args = parser.parse_args()

    # SIGTERM (timeout Airflow) -> KeyboardInterrupt agar blok finally sempat menyimpan progres
    def _term(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _term)

    if args.sync_only:
        fresh, _ = apply_salaries_to_file({})
        sync_salaries_to_supabase(get_supabase(), fresh, dry_run=args.dry_run)
        return 0

    if args.reset and os.path.exists(DONE_FLAG):
        os.remove(DONE_FLAG)
    if os.path.exists(DONE_FLAG) and not args.include_new:
        print("Salary refetch sudah SELESAI (data lama tuntas). Tidak ada yang dikerjakan. "
              "Hapus salary_refetch_complete.flag atau gunakan --reset untuk memulai ulang.")
        return 0

    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    progress = load_progress()
    if args.reset:
        progress = {}
        save_progress(progress)
        print(">>> Progress checkpoint di-reset.")
    processed_urls = set(progress.keys())

    url_platform = {j.get("job_url"): (j.get("platform") or "unknown") for j in jobs}
    yields = platform_yields(progress, url_platform)

    # Kandidat: DATA LAMA tanpa salary, belum pernah dicoba, dan link tidak ditandai mati oleh link checker
    pending = [
        (i, j) for i, j in enumerate(jobs)
        if not j.get("salary") and j.get("job_url")
        and j["job_url"] not in processed_urls
        and j.get("is_active") is not False
        # Lowongan baru (scraped_at) mendapat salary langsung dari scraper; refetch hanya untuk data lama
        and (args.include_new or not j.get("scraped_at"))
    ]
    # Platform paling produktif dulu (yield historis), lalu lowongan terbaru (akhir file) -> lebih sedikit link mati
    pending.sort(key=lambda t: (-yields.get((t[1].get("platform") or "unknown"), 0.5), -t[0]))
    batch = [j for _, j in pending[:args.batch_size]]
    last_batch = len(pending) <= args.batch_size  # batch ini menghabiskan seluruh kandidat

    print(f"Total jobs di database : {len(jobs)}")
    print(f"Sudah punya salary      : {sum(1 for j in jobs if j.get('salary'))}")
    print(f"Belum diproses          : {len(pending)}")
    print(f"Batch run ini           : {len(batch)}")
    print()
    n_pending = len(pending)
    del jobs, pending  # lepas ~ratusan MB; hanya `batch` yang dibutuhkan

    if not batch:
        print("Tidak ada URL tersisa. Semua sudah diproses atau semua sudah punya salary.")
        # tetap sinkronkan Supabase (memperbaiki upload yang pernah gagal)
        fresh, _ = apply_salaries_to_file({})
        sync_salaries_to_supabase(get_supabase(), fresh)
        if n_pending == 0 and not args.include_new:   # hanya selesai bila benar-benar tidak ada kandidat (bukan sekadar batch kosong)
            mark_complete("tidak ada kandidat tersisa")
        return 0

    supabase = get_supabase()
    stats = {"page": 0, "desc": 0, "none": 0, "error": 0}
    found = {}          # url -> salary, belum ditulis ke file
    exit_code = 0

    def flush():
        """Tulis salary yang ditemukan ke file terbaru (lock + atomik) dan simpan checkpoint."""
        nonlocal found
        if found:
            _, changed = apply_salaries_to_file(found)
            print(f"  [checkpoint] {changed} salary ditulis ke {os.path.basename(JOBS_FILE)}", flush=True)
            found = {}
        save_progress(progress)

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
                found[url] = salary
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
            progress[url] = {"salary": salary, "source": source, "ts": time.time()}

            if idx % FLUSH_EVERY == 0:
                print(f"  Progress: {idx}/{len(batch)} "
                      f"| page={stats['page']} desc={stats['desc']} "
                      f"none={stats['none']} error={stats['error']}", flush=True)
                flush()
    except KeyboardInterrupt:
        print("\n[!] Dihentikan (timeout/SIGTERM) - menyimpan progres yang sudah ada...")
        exit_code = 1
    finally:
        browser_mgr.close()
        flush()

    # Sinkronkan ke Supabase dengan diff terhadap isinya (juga memperbaiki upload yang gagal sebelumnya)
    total_with = total = 0
    try:
        fresh, _ = apply_salaries_to_file({})
        total, total_with = len(fresh), sum(1 for j in fresh if j.get("salary"))
        sync_salaries_to_supabase(supabase, fresh)
    except Exception as e:
        print(f"Sinkron Supabase GAGAL: {e} - salary aman di jobs_data.json, akan dicoba lagi di run berikutnya.")
        exit_code = 1

    if last_batch and exit_code == 0 and not args.include_new:
        mark_complete("batch terakhir selesai diproses")

    print(f"\n=== Ringkasan run ===")
    print(f"  Salary dari halaman portal: {stats['page']}")
    print(f"  Salary fallback deskripsi : {stats['desc']}")
    print(f"  Tidak ditemukan (link mati): {stats['none']}")
    print(f"  Error request/gagal       : {stats['error']}")
    if total:
        print(f"  Total dengan salary       : {total_with}/{total} ({total_with / total * 100:.2f}%)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)