import os
import re
import json
import time
import argparse
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from scraper import JobScraper
from remote_sources import SOURCES, PLATFORM_NAMES, DEFAULT_BOARDS, Http
from salary_utils import strict_salary_range

# Judul yang dianggap relevan untuk fokus Data Engineering / Data Platform
DATA_TITLE_RE = re.compile(
    r"\b(data|etl|elt|analytics?|airflow|spark|dbt|warehouse|warehousing|lakehouse|"
    r"big\s*data|database|dba|bi|business\s+intelligence|mlops)\b",
    re.I,
)
DEFAULT_KEYWORDS = {"data engineer", "data engineering", "data"}

# Kategori mengikuti taksonomi yang sudah ada di database. Urutan = prioritas.
CATEGORY_RULES = [
    ("Data & Analytics", DATA_TITLE_RE),
    ("Technology & Software", re.compile(
        r"engineer|developer|devops|sre|software|programmer|architect|security|qa\b|"
        r"frontend|front-end|backend|back-end|full[\s-]?stack|mobile|ios|android|cloud|"
        r"sysadmin|machine learning|\bml\b|\bai\b|technical|it support|platform", re.I)),
    ("Design & Creative", re.compile(r"design|ux|ui\b|creative|illustrat|video|animator|copywrit|content (writer|creator)|editor", re.I)),
    ("Marketing & Sales", re.compile(r"market|sales|growth|seo|account executive|business development|\bsdr\b|\bbdr\b|partnership|social media|community|brand|customer success", re.I)),
    ("Finance & Accounting", re.compile(r"financ|account(ant|ing)|payroll|tax|audit|bookkeep|controller|treasury", re.I)),
    ("Human Resources", re.compile(r"recruit|talent|\bhr\b|human resources|people (ops|partner)|people operations", re.I)),
    ("Education & Teaching", re.compile(r"teacher|tutor|instructor|educat|curriculum|lecturer", re.I)),
    ("Healthcare & Medical", re.compile(r"nurse|medical|clinical|health ?care|physician|pharma|therapist", re.I)),
    ("Operations & Admin", re.compile(r"operations|admin|assistant|support|coordinator|manager|project|program|product|customer|legal|compliance", re.I)),
]


def classify(title):
    for name, rx in CATEGORY_RULES:
        if rx.search(title or ""):
            return name
    return "Others / General"


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class SourceContext:
    """Dependensi yang diberikan ke fetcher; satu instance per sumber (Http tidak dibagi antar thread)."""

    def __init__(self, name, scraper, limit, boards, upwork_rss, upwork_file):
        self.name = name
        self.http = Http()
        self.q = urllib.parse.quote(scraper.search_term)
        self.general = scraper.general
        self.limit = limit
        self.boards = boards
        self.upwork_rss = upwork_rss
        self.upwork_file = upwork_file
        self._scraper = scraper
        self.warnings = 0

    def warn(self, msg):
        self.warnings += 1
        print(f"  [{self.name}] {msg}")

    def is_seen(self, url):
        return self._scraper.normalize_url(url) in self._scraper.seen_urls

    def relevant(self, title, tags=None):
        return self._scraper.is_relevant(title, tags)

    def extract_salary(self, soup, platform):
        return self._scraper.extract_salary(soup, platform)
    def extract_salary_schema(self, soup):
        return self._scraper.extract_salary_from_schema(soup)



class RemoteJobScraper(JobScraper):
    """
    Multi-source remote job scraper (fokus Data Engineering & tech roles).

    Sumber (14): Remotive, WeWorkRemotely, RemoteOK, Himalayas, Jobicy, Arbeitnow,
    WorkingNomads, TheMuse, HackerNews (Who is hiring), Greenhouse/Lever/Ashby company boards,
    LinkedIn Remote, Upwork. Lihat remote_sources.py.

    Alur: semua sumber di-fetch paralel (I/O bound) -> filter relevansi -> dedup (URL +
    company/title lintas sumber) -> diurutkan terbaru -> dibatasi `limit` per sumber ->
    satu kali tulis ke jobs_data.json (bukan per-lowongan).
    """

    def __init__(self, filepath="jobs_data.json", headless=True, keyword="", boards_file=None):
        self.dedup_keys = set()
        super().__init__(headless=headless, filepath=filepath)
        self.boards = self._load_boards(boards_file)
        self._set_keyword(keyword)

    def _set_keyword(self, keyword):
        self.keyword = keyword
        kw = _norm(keyword)
        self._kw_phrase = kw
        self._kw_words = [w for w in kw.split() if len(w) > 1]
        self._use_data_re = kw in DEFAULT_KEYWORDS
        self.general = not kw  # tanpa keyword = ambil SEMUA lowongan; data tetap diprioritaskan
        self.search_term = keyword if kw else "data engineer"

    # --- state --------------------------------------------------------------
    @staticmethod
    def normalize_url(url):
        url = (url or "").strip()
        # Query string = tracking param, kecuali HN yang identitasnya ada di ?id=
        return url if "news.ycombinator.com/item?id=" in url else url.split("?")[0]

    def load_existing_urls(self):
        """Baca database sekali: kumpulkan URL + kunci company/title untuk dedup lintas sumber."""
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for job in json.load(f):
                    url = job.get("job_url")
                    if url:
                        self.seen_urls.add(url)
                    self.dedup_keys.add((_norm(job.get("company")), _norm(job.get("job_title"))))
        except Exception as e:
            print(f"Error loading existing URLs: {e}")

    def _load_boards(self, boards_file):
        boards = {k: list(v) for k, v in DEFAULT_BOARDS.items()}
        path = boards_file or os.environ.get("COMPANY_BOARDS_FILE")
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    custom = json.load(f)
                for kind, slugs in custom.items():
                    boards[kind] = sorted(set(boards.get(kind, [])) | set(slugs))
                print(f"[boards] Menambahkan company boards dari {path}")
            except Exception as e:
                print(f"[boards] Gagal membaca {path}: {e}")
        return boards

    # --- relevansi ----------------------------------------------------------
    def is_data(self, title, tags=None):
        """Lowongan data engineering & sejenisnya (prioritas khusus)."""
        if DATA_TITLE_RE.search(_norm(title)):
            return True
        return bool(
            tags and any(re.fullmatch(r"etl|elt|airflow|spark|dbt|data engineering|big data", _norm(t)) for t in tags)
            and re.search(r"engineer|developer|architect", _norm(title))
        )

    def is_relevant(self, title, tags=None):
        if self.general:
            return True
        text = _norm(f"{title} {' '.join(map(str, tags or []))}")
        if self._use_data_re:
            return self.is_data(title, tags)
        return self._kw_phrase in text or (self._kw_words and all(w in text for w in self._kw_words))

    # --- ingest -------------------------------------------------------------
    def _accept(self, raw, platform):
        """Validasi + normalisasi satu lowongan. Return True jika ditambahkan ke buffer."""
        url = self.normalize_url(raw.get("url"))
        title = self.clean_text(raw.get("title"))
        company = self.clean_text(raw.get("company")) or "Unknown Company"
        if not url or not title or url in self.seen_urls:
            return False
        key = (_norm(company), _norm(title))
        if key in self.dedup_keys:
            return False

        desc = self.clean_description(raw.get("description") or "")
        if len(desc.strip()) < 15:
            return False

        h_resp, h_skills = self.extract_skills_and_responsibilities(desc)
        job_desk = h_resp or [l.strip() for l in desc.split("\n") if len(l.strip()) > 15][:15]

        self.jobs.append({
            "job_title": title,
            "company": company,
            "platform": platform,
            "job_url": url,
            "description": desc,
            "job_desk": job_desk,
            "requirements": raw.get("skills") or h_skills,
            "is_active": True,
            "status_reason": "Active",
            "category": classify(title),
            # Banyak ATS (Greenhouse/Ashby/TheMuse/WWR) menaruh rentang gaji hanya di teks deskripsi ("pay transparency"):
            # bila API tidak memberi salary, ambil rentang penuh yang eksplisit dari deskripsi.
            "salary": (self.clean_salary_text(raw.get("salary")) if raw.get("salary") else None) or strict_salary_range(desc),
            "source": "remote_scraper",
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self.seen_urls.add(url)
        self.dedup_keys.add(key)
        return True

    @staticmethod
    def _drop_placeholder_salaries(rows, name, min_companies=4):
        """Salary identik pada >=4 perusahaan berbeda dalam satu fetch = placeholder sumber, bukan gaji nyata."""
        by_salary = {}
        for r in rows:
            if r.get("salary"):
                by_salary.setdefault(r["salary"], set()).add(_norm(r.get("company")))
        bad = {s for s, cos in by_salary.items() if len(cos) >= min_companies}
        if bad:
            for r in rows:
                if r.get("salary") in bad:
                    r["salary"] = None
            print(f"  [{name}] salary placeholder diabaikan: {', '.join(sorted(bad))[:120]}")

    # --- runner -------------------------------------------------------------
    def scrape_all(self, keyword=None, limit_per_source=25, data_limit=200, sources=None,
                   upwork_rss=None, upwork_file=None, workers=6):
        if keyword:
            self._set_keyword(keyword)
        selected = [s.strip().lower() for s in sources.split(",")] if sources else list(SOURCES)
        unknown = [s for s in selected if s not in SOURCES]
        if unknown:
            raise SystemExit(f"Sumber tidak dikenal: {', '.join(unknown)}. Pilihan: {', '.join(SOURCES)}")

        print("=" * 70)
        print(f"REMOTE JOB SCRAPER | keyword='{self.keyword or "(semua)"}' | limit umum/sumber={limit_per_source} | limit data/sumber={data_limit}")
        print(f"Sumber ({len(selected)}): {', '.join(selected)}")
        print("=" * 70)

        started = time.time()
        results, new_urls = {}, []

        def run(name):
            ctx = SourceContext(name, self, limit_per_source, self.boards, upwork_rss, upwork_file)
            t0 = time.time()
            rows = SOURCES[name](ctx)
            return rows, ctx.warnings, time.time() - t0

        try:
            with ThreadPoolExecutor(max_workers=min(workers, len(selected))) as pool:
                futures = {pool.submit(run, n): n for n in selected}
                for fut in as_completed(futures):
                    name = futures[fut]
                    try:
                        rows, warns, secs = fut.result()
                    except Exception as e:
                        results[name] = {"fetched": 0, "relevant": 0, "new": 0, "data": 0, "error": str(e)[:80], "secs": 0}
                        print(f"[{name}] GAGAL: {e}")
                        continue
                    # Hanya thread utama yang menyentuh buffer/seen_urls -> tanpa lock
                    self._drop_placeholder_salaries(rows, name)
                    relevant = [r for r in rows if r.get("title") and self.is_relevant(r["title"], r.get("skills"))]
                    relevant.sort(key=lambda r: str(r.get("posted") or ""), reverse=True)
                    # Data engineering & sejenisnya dulu dengan kuota sendiri (lebih besar), sisanya kuota umum
                    relevant.sort(key=lambda r: not self.is_data(r["title"], r.get("skills")))
                    added = n_data = n_other = 0
                    for r in relevant:
                        is_d = self.is_data(r["title"], r.get("skills"))
                        if (is_d and n_data >= data_limit) or (not is_d and n_other >= limit_per_source):
                            continue
                        if self._accept(r, PLATFORM_NAMES[name]):
                            added += 1
                            n_data += is_d
                            n_other += not is_d
                            new_urls.append(self.jobs[-1]["job_url"])
                    results[name] = {"fetched": len(rows), "relevant": len(relevant), "new": added, "data": n_data,
                                     "error": None, "warns": warns, "secs": secs}
                    print(f"[{name}] fetched={len(rows)} relevan={len(relevant)} baru={added} (data={n_data}) ({secs:.1f}s)")
        finally:
            # Satu kali tulis (bukan per-lowongan); tetap jalan saat Ctrl+C / error
            if self.jobs:
                self.save_to_json()

        self._print_summary(results, time.time() - started)
        self.last_results, self.new_urls = results, new_urls
        return len(new_urls)

    def _print_summary(self, results, secs):
        print("\n" + "=" * 70)
        print(f"{'Sumber':<16}{'fetched':>8}{'relevan':>9}{'baru':>6}{'data':>6}  status")
        for name, r in results.items():
            status = f"ERROR: {r['error']}" if r["error"] else ("ok" if not r.get("warns") else f"ok ({r['warns']} warning)")
            print(f"{name:<16}{r['fetched']:>8}{r['relevant']:>9}{r['new']:>6}{r.get('data', 0):>6}  {status}")
        total = sum(r["new"] for r in results.values())
        failed = [n for n, r in results.items() if r["error"]]
        print(f"\nTotal baru: {total} | sumber gagal: {len(failed)}/{len(results)} | durasi: {secs:.1f}s")
        print(f"Total lowongan di {self.filepath}: {len(self.seen_urls)}")
        print("=" * 70)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Multi-Source Remote Job Scraper untuk Data Engineering.")
    parser.add_argument("--keyword", type=str, default="", help="Opsional: batasi hanya judul yang cocok. Kosong (default) = semua lowongan, data engineering diprioritaskan")
    parser.add_argument("--limit", type=int, default=25, help="Maksimum lowongan NON-data baru per sumber (default: 25)")
    parser.add_argument("--data-limit", type=int, default=200, help="Maksimum lowongan data engineering & sejenisnya per sumber (default: 200)")
    parser.add_argument("--sources", type=str, default=None, help=f"Sumber (koma dipisah). Pilihan: {','.join(SOURCES)}")
    parser.add_argument("--file", type=str, default="jobs_data.json", help="Path database JSON (default: jobs_data.json)")
    parser.add_argument("--workers", type=int, default=6, help="Jumlah sumber yang diambil paralel (default: 6)")
    parser.add_argument("--boards-file", type=str, default=None, help='JSON tambahan company boards: {"greenhouse": [...], "lever": [...], "ashby": [...]}')
    parser.add_argument("--upwork-rss", type=str, default=None, help="URL RSS Feed pribadi dari Upwork")
    parser.add_argument("--upwork-file", type=str, default=None, help="Path file XML/JSON Upwork lokal")
    parser.add_argument("--upload-supabase", action="store_true", default=False, help="Sinkronisasi lowongan baru ke Supabase setelah selesai")
    args = parser.parse_args()

    scraper = RemoteJobScraper(filepath=args.file, keyword=args.keyword, boards_file=args.boards_file)
    total_added = scraper.scrape_all(
        limit_per_source=args.limit, data_limit=args.data_limit, sources=args.sources, workers=args.workers,
        upwork_rss=args.upwork_rss, upwork_file=args.upwork_file,
    )

    if args.upload_supabase and total_added > 0:
        print("\n[Supabase] Mengunggah lowongan remote baru ke Supabase...")
        try:
            from upload_to_supabase import upload_jobs_to_supabase
            upload_jobs_to_supabase(filepath=args.file, urls=scraper.new_urls)
        except Exception as e:
            print(f"[Supabase] Gagal mengunggah ke Supabase: {e}")

    # Exit code non-zero (agar Airflow menandai gagal/retry) hanya jika SEMUA sumber error
    if scraper.last_results and all(r["error"] for r in scraper.last_results.values()):
        raise SystemExit(1)
