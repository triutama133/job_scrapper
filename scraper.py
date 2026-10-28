import os
import json
import time
import random
import re
import argparse
from bs4 import BeautifulSoup
from camoufox.sync_api import Camoufox

HOME_REGION = "Indonesia"

# Indonesia = semua lowongan. Region lain = HANYA lowongan remote.
REGIONS = {
    "indonesia":   {"jobstreet": "https://www.jobstreet.co.id", "glints": "id", "kalibrr": "Indonesia"},
    "singapore":   {"jobstreet": "https://www.jobstreet.com.sg", "glints": "sg", "kalibrr": None},
    "malaysia":    {"jobstreet": "https://www.jobstreet.com.my", "glints": "my", "kalibrr": None},
    "philippines": {"jobstreet": "https://www.jobstreet.com.ph", "glints": None, "kalibrr": "Philippines"},
}

REMOTE_RE = re.compile(r"\b(remote(ly)?|work\s*from\s*home|wfh|work\s*from\s*anywhere|fully\s*distributed)\b", re.I)


class JobScraper:
    save_batch = 10  # tulis ke disk tiap N lowongan (bukan tiap lowongan: file 90MB+)

    def __init__(self, headless=True, filepath="jobs_data.json"):
        self.remote_only = False
        self.headless = headless
        self.jobs = []
        self.seen_urls = set()
        self.filepath = filepath
        self.load_existing_urls()

    def load_existing_urls(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    existing_jobs = json.load(f)
                    for job in existing_jobs:
                        url = job.get("job_url")
                        if url:
                            self.seen_urls.add(url)
            except Exception as e:
                print(f"Error loading existing URLs: {e}")

    _session_page = None
    uses_browser = True
    keyword_delay = None  # (min, max) detik jeda antar keyword; dipakai situs yang cepat rate-limit

    def session(self):
        """Satu browser dipakai ulang untuk semua keyword/region (launch Camoufox ~10-50 detik di Raspberry Pi)."""
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            if not self.uses_browser:
                yield self
                return
            print("Launching Camoufox stealth browser (sekali untuk seluruh sesi)...")
            with Camoufox(headless=self.headless) as browser:
                self._session_page = browser.new_page()
                try:
                    yield self
                finally:
                    try:
                        self._session_page.close()
                    except Exception:
                        pass
                    self._session_page = None
        return _cm()

    def run_browser(self, task_func):
        if self._session_page is not None:
            task_func(self._session_page)
            return
        print("Launching Camoufox stealth browser (C++ Fingerprint Spoofing)...")
        with Camoufox(headless=self.headless) as browser:
            page = browser.new_page()
            try:
                task_func(page)
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    def run_playwright(self, task_func):
        """Backward compatibility alias: delegates to Camoufox browser."""
        self.run_browser(task_func)

    def clean_text(self, text):
        if not text:
            return ""
        # Remove extra whitespaces
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def clean_salary_text(self, text):
        """Normalisasi & bersihkan teks salary hasil ekstraksi.

        - Hapus non-breaking space (\xa0) dan ratakan spasi berlebih.
        - Hapus separator rentang menggantung di akhir (range tidak lengkap):
          'Rp 6.500.000 -' -> 'Rp 6.500.000'
        - Hapus desimal koma/titik nol di akhir angka saja (tidak merusak format rupiah):
          '6.500.000,00' -> '6.500.000' ; '10.000.000,00 - 15.000.000,00' -> '10.000.000 - 15.000.000'
        """
        if not text:
            return None
        t = text.replace('\xa0', ' ').strip()
        t = re.sub(r'\s+', ' ', t)
        t = re.sub(r'\s*[-–—]\s*$', '', t)          # trailing range sep
        t = re.sub(r'[,.]00?(?!\d)', '', t)          # safe decimal zeros removal at end of digits
        t = re.sub(r'\s+', ' ', t).strip()
        return t if t else None

    def clean_description(self, text):
        if not text:
            return ""
        lines = text.split('\n')
        cleaned = []
        junk_keywords = [
            'show more', 'show less', 'lihat selengkapnya', 'lihat lebih sedikit', 
            'read more', 'read less',
            'bergabunglah dengan jutaan pencari kerja di seluruh dunia menggunakan alat dan wawasan pembentukan karier kami'
        ]
        for line in lines:
            stripped = line.strip()
            # Skip empty lines or lines containing junk keywords
            if not stripped:
                cleaned.append("")
                continue
            if any(jk == stripped.lower() or (len(stripped) < 120 and jk in stripped.lower()) for jk in junk_keywords):
                continue
            cleaned.append(stripped)
            
        # Join lines and clean up excessive consecutive newlines (max 2 consecutive newlines)
        result = "\n".join(cleaned)
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip()

    def extract_skills_and_responsibilities(self, text):
        """
        Extract job desk (responsibilities) and skills from description text using basic heuristic matching.
        """
        responsibilities = []
        skills = []
        
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        in_resp = False
        in_skills = False
        
        resp_keywords = [
            'tanggung jawab', 'responsibilities', 'job desk', 'tugas', 'tanggung-jawab', 
            'deskripsi pekerjaan', 'job description', 'job descriptions', 'deskripsi', 
            'tanggungjawab', 'jobdesk', 'peran anda', 'your role', 'what will you do',
            'what you will do', 'about the role', 'key responsibilities', 'role & responsibilities',
            'tugas dan tanggung jawab', 'tugas & tanggung jawab'
        ]
        skill_keywords = [
            'kualifikasi', 'persyaratan', 'requirements', 'skills', 'keahlian', 
            'kemampuan', 'kriteria', 'qualification', 'job requirement', 'job requirements',
            'spesifikasi', 'minimum', 'persyaratan minimum', 'what you need', "what you'll need",
            'background', 'who you are', 'requirement'
        ]

        for line in lines:
            line_lower = line.lower()
            
            # Check headers
            if any(kw in line_lower for kw in resp_keywords) and len(line) < 60:
                in_resp = True
                in_skills = False
                continue
            elif any(kw in line_lower for kw in skill_keywords) and len(line) < 60:
                in_skills = True
                in_resp = False
                continue
            
            # If line is bullet point or in specific section
            is_bullet = line.startswith('-') or line.startswith('*') or (line[0].isdigit() and len(line) > 1 and line[1] in ['.', ')'])
            
            cleaned_line = line.lstrip('-*0123456789.) ').strip()
            
            # Skip junk lines
            junk_keywords = ['show more', 'show less', 'lihat selengkapnya', 'lihat lebih sedikit', 'read more', 'read less']
            if not cleaned_line or any(jk in cleaned_line.lower() for jk in junk_keywords):
                continue
                
            if in_resp:
                if is_bullet or len(line) > 10:
                    responsibilities.append(cleaned_line)
            elif in_skills:
                if is_bullet or len(line) > 5:
                    skills.append(cleaned_line)

        return responsibilities, skills

    def format_salary_from_schema(self, currency, min_val, max_val, unit_val):
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

    def extract_salary_from_schema(self, soup):
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
                formatted = self.format_salary_from_schema(currency, min_val, max_val, unit_val)
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
                            formatted = self.format_salary_from_schema(currency, min_val, max_val, unit_val)
                            if formatted:
                                return formatted
            except Exception:
                pass
        return None

    def extract_salary(self, soup, platform):
        # 1. Try high-fidelity schema-based parsing first
        schema_sal = self.extract_salary_from_schema(soup)
        if schema_sal:
            return schema_sal

        platform = platform.lower()
        if platform == "jobstreet":
            elem = soup.find(attrs={"data-automation": "job-detail-salary"})
            if elem:
                return elem.text.strip()
        
        # General heuristics fallback
        currency = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'
        
        candidates = []
        for tag in ['span', 'div', 'p', 'h3', 'h4']:
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
                # Reject very long sequence of digits (usually fused numbers or timestamps/IDs)
                if re.search(r'\d{9,}', text):
                    continue
                # Match common currency symbols followed by numbers
                if re.search(currency + r'\d+[\d\.,\s\-–—jt]*', text, re.I):
                    candidates.append(text)
                    
        # Filter for candidates with frequency indicators (month/bulan etc.)
        for cand in candidates:
            if re.search(r'(bulan|month|year|tahun|/month|/bulan|/yr|/thn|per\s*month|per\s*bulan)', cand, re.I):
                match = re.search(currency + r'\d+[\d\.,\s\-–—jt]*\s*([/]\s*(bulan|month|year|tahun|yr|thn)|per\s*(month|bulan))?', cand, re.I)
                if match:
                    return match.group(0).strip()
                return cand
                
        # If no frequency indicator, look for any range or clean value
        for cand in candidates:
            if re.search(currency + r'\d+[\d\.,\s\-–—jt]+', cand, re.I):
                if len(cand) < 60:
                    match = re.search(currency + r'\d+[\d\.,\s\-–—jt]*', cand, re.I)
                    if match:
                        return match.group(0).strip()
                    return cand
        return None

    def looks_remote(self, title, description):
        return bool(REMOTE_RE.search(title or "") or REMOTE_RE.search((description or "")[:1200]))

    def add_job(self, title, company, platform, url, description, job_desk=None, skills=None, salary=None, is_remote=None):
        if url in self.seen_urls:
            return
        # Region non-Indonesia: hanya remote. is_remote dari flag resmi situs bila ada, selain itu deteksi teks.
        if self.remote_only and not (is_remote or (is_remote is None and self.looks_remote(title, description))):
            return
        
        # Clean description of any dynamic UI junk lines first
        cleaned_desc = self.clean_description(description)
        
        # Heuristic extraction if not provided or empty
        h_resp, h_skills = self.extract_skills_and_responsibilities(cleaned_desc)
        
        # If no job desk was extracted, use a cleaned line-by-line fallback
        final_job_desk = job_desk or h_resp
        if not final_job_desk:
            final_job_desk = [l.strip() for l in cleaned_desc.split('\n') if l.strip() and len(l.strip()) > 15]
            
        final_skills = skills or h_skills
            
        job_data = {
            "job_title": self.clean_text(title),
            "company": self.clean_text(company),
            "platform": platform,
            "job_url": url,
            "description": cleaned_desc,
            "job_desk": final_job_desk,
            "requirements": final_skills,
            "salary": self.clean_salary_text(salary) if salary else None,
            # Penanda lowongan BARU: salary diambil langsung oleh scraper; salary_refetch hanya untuk data lama (tanpa penanda)
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        
        self.jobs.append(job_data)
        self.seen_urls.add(url)
        print(f"[{platform}] Scraped: {job_data['job_title']} at {job_data['company']}")
        if len(self.jobs) >= self.save_batch:
            self.save_to_json()

    def save_to_json(self, filepath=None):
        if filepath:
            self.filepath = filepath
        if not self.jobs:
            return  # tidak ada yang baru: jangan baca/tulis ulang file besar
        import fcntl
        # Kunci antar-proses: scraper portal & remote scraper (DAG berbeda) menulis file yang sama
        with open(self.filepath + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._save_locked()

    def _save_locked(self):
        # Load existing jobs to prevent duplicates across runs
        existing_jobs = []
        if os.path.exists(self.filepath):
            success = False
            for attempt in range(5):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        existing_jobs = json.load(f)
                    success = True
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1}: Error reading existing database file: {e}")
                    time.sleep(0.5)
            if not success:
                print("CRITICAL: Failed to load database file safely. Aborting write to prevent data loss.")
                return
        
        # Merge new jobs using set for fast O(1) checks
        merged_jobs = list(existing_jobs)
        existing_urls = {j.get("job_url") for j in existing_jobs if j.get("job_url")}
        
        for job in self.jobs:
            if job["job_url"] not in existing_urls:
                merged_jobs.append(job)
                existing_urls.add(job["job_url"])
                
        # Atomic write to prevent file corruption
        temp_filepath = self.filepath + ".tmp"
        # Atomic write with unique PID to prevent race conditions between concurrent scrapers
        temp_filepath = f"{self.filepath}.{os.getpid()}_{int(time.time() * 1000)}.tmp"
        try:
            with open(temp_filepath, 'w', encoding='utf-8') as f:
                json.dump(merged_jobs, f, indent=4, ensure_ascii=False)
            os.replace(temp_filepath, self.filepath)
            print(f"Saved {len(merged_jobs)} total unique jobs to {self.filepath}")
            self.jobs = []
        except Exception as e:
            print(f"Error saving database file atomicly: {e}")
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except:
                    pass


class GlintsScraper(JobScraper):
    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to Glints (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            region = REGIONS.get(location.lower())
            if not region or not region.get("glints"):
                print(f"Glints tidak mendukung region '{location}'. Dilewati.")
                return
            cc = region["glints"]
            self.remote_only = location.lower() != HOME_REGION.lower()
            
            scraped_links = set()
            # Tanpa login Glints hanya memberi halaman 1 (30 hasil; halaman 2+ -> "please login"). Agar tidak terus
            # mendapat 30 lowongan teratas yang sama, ambil dua himpunan berbeda per keyword: terbaru dulu, lalu peringkat default.
            for sort_param in ("&sortBy=LATEST", ""):
                if len(scraped_links) >= max_jobs:
                    break
                base_url = f"https://glints.com/{cc}/opportunities/jobs/explore?keyword={q_keyword}&locationName={q_location}"
                if self.remote_only:
                    base_url = f"https://glints.com/{cc}/opportunities/jobs/explore?keyword={q_keyword}&country={cc.upper()}&workArrangementOptions=REMOTE"
                url = base_url + sort_param
                label = "terbaru" if sort_param else "default"
                print(f"Loading Glints search ({label})...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, window.innerHeight)")
                        page.wait_for_timeout(1000)

                    soup = BeautifulSoup(page.content(), 'html.parser')

                    page_links = set()
                    for a in soup.find_all('a', href=True):
                        clean_href = a['href'].split('?')[0]
                        if re.search(r"/opportunities/jobs/[^/]+/[0-9a-f]{8}-[0-9a-f-]{27}$", clean_href):
                            page_links.add(clean_href if clean_href.startswith("http") else "https://glints.com" + clean_href)

                    new_links = {l for l in page_links if l not in self.seen_urls} - scraped_links
                    scraped_links.update(new_links)
                    print(f"Glints ({label}): {len(page_links)} lowongan, {len(new_links)} belum pernah diambil; terkumpul {len(scraped_links)} / {max_jobs}")
                except Exception as pe:
                    print(f"Glints search ({label}) loading error: {pe}")

            links = list(scraped_links)[:max_jobs]
            print(f"Found {len(links)} Glints job links to scrape.")
            
            for link in links:
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    
                    job_soup = BeautifulSoup(page.content(), 'html.parser')
                    
                    # Try to find title and company name
                    title_elem = job_soup.find('h1')
                    title = title_elem.text.strip() if title_elem else "Unknown Title"
                    
                    company_elem = job_soup.find('a', href=re.compile(r'/organizations/|/companies/'))
                    if not company_elem:
                        company_elem = job_soup.find(class_=re.compile(r'CompanyName|company-name', re.I))
                    company = company_elem.text.strip() if company_elem else "Unknown Company"
                    
                    # Try to get description
                    desc_elem = job_soup.find(class_=re.compile(r'JobDescription|description|job-description', re.I))
                    if not desc_elem:
                        desc_elem = job_soup.find('main') or job_soup.find('article')
                    
                    description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                    
                    # Extract skills specifically from Glints tags if available
                    skills_elems = job_soup.find_all(class_=re.compile(r'SkillTag|skill-tag|Tag-', re.I))
                    skills = [elem.text.strip() for elem in skills_elems] if skills_elems else []
                    
                    salary = self.extract_salary(job_soup, "Glints")
                    if title != "Unknown Title" and description:
                        self.add_job(title, company, "Glints", link, description, skills=skills, salary=salary)
                except Exception as e:
                    print(f"Error scraping Glints job {link}: {e}")
                    
        self.run_playwright(task)


class KalibrrScraper(JobScraper):
    uses_browser = False
    keyword_delay = (1, 3)  # ~75 keyword beruntun ke API: beri jeda sopan
    """Kalibrr lewat JSON API internal (tanpa browser): lebih cepat & tidak bergantung selector HTML.
    Halaman /job-board?q= lama sudah di-redirect ke homepage sehingga scraper HTML menghasilkan 0 lowongan."""

    API = "https://www.kalibrr.com/kjs/job_board/search"

    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        import requests
        import urllib.parse
        region = REGIONS.get(location.lower(), {})
        country = region.get("kalibrr")
        if not country:
            print(f"Kalibrr tidak mendukung region '{location}' (hanya Indonesia & Philippines). Dilewati.")
            return
        self.remote_only = location.lower() != HOME_REGION.lower()
        print(f"Kalibrr API (Keyword: '{keyword}', Country: '{country}', remote_only={self.remote_only})...")

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        added, offset, limit = 0, 0, 50
        while added < max_jobs and offset < 500:
            params = {"limit": limit, "offset": offset, "country": country}
            if keyword:
                params["text"] = keyword
            if self.remote_only:
                params["is_work_from_home"] = "true"
            jobs, err = [], None
            for attempt in range(3):  # 5xx sesaat itu wajar; coba lagi dengan jeda sebelum menyerah
                try:
                    res = requests.get(self.API, params=params, headers=headers, timeout=20)
                    res.raise_for_status()
                    jobs, err = res.json().get("jobs", []), None
                    break
                except Exception as e:
                    err = e
                    time.sleep(2 * (attempt + 1))
            if err:
                print(f"Kalibrr API error (offset {offset}): {err}")
                break
            if not jobs:
                break

            for j in jobs:
                if added >= max_jobs:
                    break
                code = (j.get("company_info") or j.get("company") or {}).get("code")
                if not (code and j.get("id") and j.get("slug")):
                    continue
                url = f"https://www.kalibrr.com/id-ID/c/{code}/jobs/{j['id']}/{j['slug']}"
                if url in self.seen_urls:
                    continue
                desc = BeautifulSoup(j.get("description") or "", "html.parser").get_text(separator="\n")
                quals = BeautifulSoup(j.get("qualifications") or "", "html.parser").get_text(separator="\n")
                description = desc + ("\n\nKualifikasi:\n" + quals if quals.strip() else "")
                salary = None
                if j.get("base_salary") or j.get("maximum_salary"):
                    salary = self.format_salary_from_schema(
                        j.get("salary_currency") or "IDR", j.get("base_salary"), j.get("maximum_salary"), j.get("salary_interval"))
                company = j.get("company_name") or (j.get("company_info") or {}).get("name") or code.replace("-", " ").title()
                skills = [x.get("name") if isinstance(x, dict) else str(x) for x in (j.get("job_sds_skills") or [])]
                before = len(self.seen_urls)
                self.add_job(j.get("name"), company, "Kalibrr", url, description, skills=skills or None,
                             salary=salary, is_remote=bool(j.get("is_work_from_home")) if self.remote_only else None)
                added += len(self.seen_urls) - before
            offset += limit
            time.sleep(random.uniform(0.5, 1.2))
        print(f"Kalibrr: {added} lowongan baru.")


class JobstreetScraper(JobScraper):
    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to Jobstreet (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            region = REGIONS.get(location.lower())
            if not region:
                print(f"Region '{location}' tidak dikenal untuk Jobstreet. Dilewati.")
                return
            self.remote_only = location.lower() != HOME_REGION.lower()
            base = region["jobstreet"]
            slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-") or "remote"
            
            # Path /remote Jobstreet hanya pencarian lunak -> banyak hasil tidak remote; periksa lebih banyak kandidat
            want = max_jobs * 4 if self.remote_only else max_jobs
            scraped_links = set()
            page_num = 1
            max_pages = 4 # Deep pagination search (up to 4 pages)
            
            while len(scraped_links) < want and page_num <= max_pages:
                if self.remote_only:
                    url = f"{base}/{slug}-jobs/remote"   # filter remote bawaan Jobstreet (region non-Indonesia)
                else:
                    url = f"{base}/id/job-search/{q_keyword}-jobs-in-{q_location}"
                if page_num > 1:
                    url += f"?page={page_num}"
                    
                print(f"Loading Jobstreet search page {page_num}...")
                try:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        page.wait_for_timeout(3000)  # sesekali NS_ERROR_ABORT akibat redirect domain: coba sekali lagi
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, window.innerHeight)")
                        page.wait_for_timeout(1000)
                        
                    content = page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    current_links = []
                    for a in soup.find_all('a', href=True):
                        href = a['href']
                        if "/job/" in href:
                            if not href.startswith("http"):
                                href = (urllib.parse.urljoin(page.url, href) if self.remote_only
                                        else "https://www.jobstreet.co.id" + href)
                            clean_href = href.split('?')[0]
                            if clean_href not in self.seen_urls:
                                current_links.append(clean_href)
                                
                    new_links = set(current_links) - scraped_links
                    if not new_links:
                        print("No new jobs found on this page. Stopping pagination.")
                        break
                        
                    scraped_links.update(new_links)
                    print(f"Jobstreet: Gathered {len(scraped_links)} / {max_jobs} links so far...")
                    page_num += 1
                except Exception as pe:
                    print(f"Jobstreet page {page_num} loading error: {pe}")
                    break
            
            links = list(scraped_links)[:want]
            print(f"Found {len(links)} Jobstreet job links to scrape.")
            
            added = 0
            for link in links:
                if self.remote_only and added >= max_jobs:
                    break
                try:
                    normalized_link = link.split('?')[0]
                    try:
                        page.goto(normalized_link, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        page.wait_for_timeout(4000)  # NS_ERROR_ABORT sesekali muncul saat redirect/challenge; coba sekali lagi
                        page.goto(normalized_link, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    
                    job_soup = BeautifulSoup(page.content(), 'html.parser')
                    
                    # Check if the job has already expired
                    if job_soup.find(attrs={"data-automation": "expiredJobPage"}) or "tidak lagi diiklankan" in job_soup.text.lower()[:500]:
                        print(f"Jobstreet job {link} is expired/closed. Skipping.")
                        continue

                    title_elem = (
                        job_soup.find(attrs={"data-automation": "job-detail-title"}) 
                        or job_soup.find('h1') 
                        or job_soup.find(class_=re.compile(r'JobTitle', re.I))
                    )
                    title = title_elem.text.strip() if title_elem else "Unknown Title"
                    
                    # Prioritize seeker/jobstreet automation attribute for advertiser name
                    company_elem = job_soup.find(attrs={"data-automation": "advertiser-name"})
                    if company_elem:
                        company = company_elem.text.strip()
                    else:
                        # Fallback: scan all /companies/ or /company/ links, filter out ratings and boilerplate texts
                        company_links = job_soup.find_all('a', href=re.compile(r'/companies/|/company/'))
                        valid_company = None
                        for a in company_links:
                            a_text = a.text.strip()
                            if a_text and not any(junk in a_text.lower() for junk in ["selengkapnya tentang", "ulasan", "reviews", "lihat semua", "perusahaan"]):
                                # Skip pure rating numbers (e.g. "3.3" or "4.0")
                                if not re.match(r'^\d+(\.\d+)?$', a_text):
                                    valid_company = a_text
                                    break
                        if valid_company:
                            company = valid_company
                        else:
                            # Fallback 2: Class name matching
                            company_elem = job_soup.find(class_=re.compile(r'CompanyName', re.I))
                            company = company_elem.text.strip() if company_elem else "Unknown Company"
                    
                    # Seek / Jobstreet main job advertisement details
                    desc_elem = (
                        job_soup.find(attrs={"data-automation": "jobAdDetails"}) 
                        or job_soup.find(attrs={"data-automation": re.compile(r"^job-?ad-?details$", re.I)})
                    )
                    
                    # Fallback: candidate elements while strictly excluding lmis / promotional blocks
                    if not desc_elem:
                        for cand in job_soup.find_all(attrs={"data-automation": re.compile(r"description", re.I)}):
                            cand_auto = cand.get("data-automation", "").lower()
                            if "lmis" not in cand_auto:
                                desc_elem = cand
                                break
                                
                    if not desc_elem:
                        for cand in job_soup.find_all(class_=re.compile(r'\b(?:job[-_]?description|jobDetails)\b', re.I)):
                            cand_classes = " ".join(cand.get("class", []))
                            if "lmis" not in cand_classes.lower():
                                desc_elem = cand
                                break
                                
                    description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                    
                    # Discard if it only captured the lmis boilerplate marketing text
                    if "bergabunglah dengan jutaan pencari kerja di seluruh dunia" in description.lower() and len(description) < 300:
                        description = ""
                    
                    salary = self.extract_salary(job_soup, "Jobstreet")
                    if title != "Unknown Title" and description:
                        before = len(self.seen_urls)
                        self.add_job(title, company, "Jobstreet", normalized_link, description, salary=salary)
                        added += len(self.seen_urls) - before
                except Exception as e:
                    print(f"Error scraping Jobstreet job {link}: {e}")
                    
        self.run_playwright(task)


class LinkedInScraper(JobScraper):
    keyword_delay = (25, 45)  # ~16 keyword beruntun memicu rate-limit (hasil kosong / NS_ERROR_NET_EMPTY_RESPONSE)

    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to LinkedIn Jobs (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            self.remote_only = location.lower() != HOME_REGION.lower()
            wt = "&f_WT=2" if self.remote_only else ""   # filter resmi LinkedIn: Remote
            
            raw_gathered_links = set()
            retried = False
            start_offset = 0
            max_pages = min(8, max(2, max_jobs // 5))
            page_count = 0
            
            while page_count < max_pages:
                url = f"https://www.linkedin.com/jobs/search?keywords={q_keyword}&location={q_location}{wt}&start={start_offset}"
                print(f"Loading LinkedIn search offset {start_offset} (Page {page_count + 1})...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2500)
                    
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, window.innerHeight)")
                        page.wait_for_timeout(800)
                    
                    content = page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    page_links = set()
                    for a in soup.find_all('a', href=True):
                        href = a['href']
                        if "/jobs/view/" in href:
                            clean_href = href.split('?')[0]
                            page_links.add(clean_href)
                            
                    if not page_links and start_offset == 0 and not retried:
                        retried = True  # halaman pertama kosong biasanya rate-limit sesaat: tunggu lalu coba lagi sekali
                        print("LinkedIn: hasil kosong, menunggu lalu mencoba lagi sekali...")
                        page.wait_for_timeout(random.randint(8000, 14000))
                        continue
                    if not page_links:
                        print("No job links found on this offset. Stopping LinkedIn pagination.")
                        break
                        
                    raw_gathered_links.update(page_links)
                    print(f"LinkedIn: Gathered {len(raw_gathered_links)} total raw links so far...")
                    
                    start_offset += 25
                    page_count += 1
                except Exception as e:
                    print(f"LinkedIn offset {start_offset} error: {e}")
                    break
            
            unseen_links = [link for link in list(raw_gathered_links) if link not in self.seen_urls]
            links = unseen_links[:max_jobs]
            print(f"Found {len(links)} NEW LinkedIn job links to scrape (out of {len(raw_gathered_links)} raw links).")
            
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9,id;q=0.8'
            }
            
            for link in links:
                try:
                    job_ids = re.findall(r'\d{8,}', link)
                    if job_ids:
                        job_id = job_ids[-1]
                        time.sleep(random.uniform(1.5, 3.5))  # jeda acak antar detail request
                        api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
                        res = requests.get(api_url, headers=headers, timeout=15)
                        if res.status_code == 200:
                            job_soup = BeautifulSoup(res.text, 'html.parser')
                            title_elem = job_soup.find('h2', class_='top-card-layout__title') or job_soup.find('h1') or job_soup.find('h2')
                            title = title_elem.text.strip() if title_elem else "Unknown Title"
                            
                            company_elem = job_soup.find('a', class_='topcard__org-name-link') or job_soup.find(class_='topcard__flavor')
                            company = company_elem.text.strip() if company_elem else "Unknown Company"
                            
                            desc_elem = job_soup.find(class_='show-more-less-html__markup') or job_soup.find(class_=re.compile(r'description', re.I))
                            description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                            
                            if "•" in company:
                                company = company.split("•")[0].strip()
                            company = re.sub(r'\d+\s+(minggu|hari|jam|bulan|tahun)\s+yang\s+lalu|laju.*', '', company, flags=re.I).strip()
                            
                            salary = self.extract_salary(job_soup, "LinkedIn")
                            if title != "Unknown Title" and description:
                                self.add_job(title, company, "LinkedIn", link, description, salary=salary, is_remote=True if self.remote_only else None)
                                print(f"[LinkedIn API] Scraped: {title} at {company}")
                                continue
                    
                    page.goto(link, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)
                    
                    job_soup = BeautifulSoup(page.content(), 'html.parser')
                    title_elem = job_soup.find(class_=re.compile(r'topcard__title|top-card-layout__title|h1', re.I)) or job_soup.find('h1')
                    title = title_elem.text.strip() if title_elem else "Unknown Title"
                    
                    company_elem = job_soup.find('a', class_='topcard__org-name-link') or job_soup.find('a', href=re.compile(r'/company/'))
                    company = company_elem.text.strip() if company_elem else "Unknown Company"
                    
                    desc_elem = job_soup.find(class_=re.compile(r'description__text|show-more-less-html__markup', re.I))
                    description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                    
                    if "•" in company:
                        company = company.split("•")[0].strip()
                    company = re.sub(r'\d+\s+(minggu|hari|jam|bulan|tahun)\s+yang\s+lalu|laju.*', '', company, flags=re.I).strip()
                    
                    salary = self.extract_salary(job_soup, "LinkedIn")
                    if title != "Unknown Title" and description:
                        self.add_job(title, company, "LinkedIn", link, description, salary=salary, is_remote=True if self.remote_only else None)
                        print(f"[LinkedIn Browser Fallback] Scraped: {title} at {company}")
                except Exception as e:
                    print(f"Error scraping LinkedIn job {link}: {e}")
                    
        self.run_playwright(task)


# Keyword pencarian untuk region utama (Indonesia = SEMUA lowongan). Portal hanya bisa dicari per keyword, jadi
# kumpulan ini dibuat mencakup semua kategori taksonomi (Data, Tech, Design, HR, Finance, Marketing, Healthcare,
# Education, Operations) plus bidang non-kantor yang banyak di portal Indonesia. Data engineering selalu duluan.
PRIORITY_KEYWORDS = ["Data Engineer", "Data Analyst", "Data Scientist", "Analytics Engineer"]
KEYWORD_POOL = [
    # Teknologi
    "Software Engineer", "Backend Developer", "Frontend Developer", "Mobile Developer", "DevOps Engineer",
    "QA Engineer", "UI UX Designer", "IT Support", "Network Engineer", "Product Manager", "Business Analyst",
    # Admin & operasional
    "Data Entry", "Admin", "Staff Administrasi", "Sekretaris", "Receptionist", "Customer Service",
    "Project Manager", "Operations Manager", "General Affairs", "Customer Success",
    # Marketing & sales
    "Digital Marketing", "Social Media Specialist", "Content Creator", "SEO Specialist", "Brand Manager",
    "Copywriter", "Sales", "Sales Executive", "Business Development", "Account Executive", "Telemarketing",
    # Desain & kreatif
    "Graphic Designer", "Video Editor", "Illustrator", "Photographer",
    # HR
    "Human Resources", "Recruiter", "Talent Acquisition", "Payroll",
    # Keuangan
    "Accountant", "Finance", "Tax", "Auditor", "Banking Officer", "Credit Analyst",
    # Logistik & produksi
    "Logistics", "Supply Chain", "Warehouse", "Purchasing", "Procurement", "Driver", "Production Operator",
    "Quality Control", "Maintenance Technician", "HSE Officer",
    # Teknik
    "Mechanical Engineer", "Civil Engineer", "Electrical Engineer", "Architect",
    # Kesehatan
    "Perawat", "Doctor", "Apoteker", "Medical Representative",
    # Pendidikan
    "Teacher", "Guru", "Lecturer", "Tutor",
    # Ritel, F&B, perhotelan
    "Store Manager", "Kasir", "Barista", "Chef", "Waiter", "Hotel Front Office",
    # Lainnya
    "Legal", "Consultant", "Translator", "Content Writer", "Researcher",
]
REMOTE_KEYWORDS = ["Data Engineer", "Data Analyst", "Software Engineer"]
ROTATION_STATE = "scraper_state.json"


def rotating_keywords(window, state_file=ROTATION_STATE):
    """Ambil `window` keyword berikutnya dari KEYWORD_POOL secara bergilir (kursor disimpan di file).

    Platform berbasis browser lambat, jadi tiap run hanya mengerjakan satu jendela; seluruh pool tuntas dalam
    beberapa run. Kursor maju di awal run sehingga run yang terpotong timeout tidak mengulang jendela yang sama.
    """
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            cursor = int(json.load(f).get("keyword_cursor", 0))
    except Exception:
        cursor = 0
    n = len(KEYWORD_POOL)
    cursor %= n
    picked = [KEYWORD_POOL[(cursor + i) % n] for i in range(min(window, n))]
    try:
        tmp = f"{state_file}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"keyword_cursor": (cursor + window) % n}, f)
        os.replace(tmp, state_file)
    except Exception as e:
        print(f"Peringatan: gagal menyimpan kursor keyword ({e}); run berikutnya mengulang jendela yang sama.")
    return picked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scraper for Glints, Kalibrr, Jobstreet, and LinkedIn")
    parser.add_argument("--max-jobs", type=int, default=5, help="Maximum number of jobs to scrape per platform")
    parser.add_argument("--platform", type=str, default="all", choices=["all", "glints", "kalibrr", "jobstreet", "linkedin"], help="Specific platform to scrape")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")
    parser.add_argument("--location", type=str, default="Indonesia", help="Location to search jobs for (e.g. Indonesia, Singapore, United States)")
    parser.add_argument("--keyword", type=str, default="", help="Keyword to search jobs for (e.g. 'Software Engineer')")
    parser.add_argument("--remote-regions", type=str, default="Singapore,Malaysia,Philippines", help="Region lain (koma dipisah) yang hanya diambil lowongan REMOTE-nya. Negara apa pun didukung LinkedIn (mis. 'United States,United Kingdom,Germany'); Glints/Jobstreet/Kalibrr hanya Asia Tenggara & akan dilewati untuk region lain. Kosongkan untuk menonaktifkan")
    parser.add_argument("--skip-home", action="store_true", help="Lewati region utama (--location); hanya jalankan --remote-regions (untuk dipecah jadi task terpisah)")
    parser.add_argument("--window", type=int, default=12, help="Jumlah keyword umum bergilir per run untuk platform berbasis browser (Glints/Jobstreet/LinkedIn)")
    parser.add_argument("--api-per-keyword", type=int, default=30, help="Lowongan baru per keyword untuk platform API (Kalibrr), yang menjalankan SEMUA keyword tiap run")
    args = parser.parse_args()

    platform = args.platform.lower()
    max_jobs = args.max_jobs
    location = args.location
    keyword = args.keyword

    print(f"Starting Job Scraper. Target: {platform.upper()}, Max Jobs: {max_jobs}, Location: '{location}', Keyword: '{keyword}'")

    scrapers = []
    if platform in ["all", "glints"]:
        scrapers.append(GlintsScraper(headless=args.headless))
    if platform in ["all", "kalibrr"]:
        scrapers.append(KalibrrScraper(headless=args.headless))
    if platform in ["all", "jobstreet"]:
        scrapers.append(JobstreetScraper(headless=args.headless))
    if platform in ["all", "linkedin"]:
        scrapers.append(LinkedInScraper(headless=args.headless))

    remote_regions = [r.strip() for r in args.remote_regions.split(",") if r.strip() and r.strip().lower() != location.lower()]
    home_window = [] if (keyword or args.skip_home or not any(sc.uses_browser for sc in scrapers)) else rotating_keywords(args.window)

    def run_scrape(scraper, loc, kws, per_kw):
        scraper.remote_only = loc.lower() != HOME_REGION.lower()
        for i, kw in enumerate(kws):
            if i and scraper.keyword_delay:
                time.sleep(random.uniform(*scraper.keyword_delay))
            print(f"\n[{scraper.__class__.__name__.replace('Scraper','')} | {loc}{' remote-only' if scraper.remote_only else ''} | '{kw}']")
            try:
                scraper.scrape(max_jobs=per_kw, location=loc, keyword=kw)
            except Exception as se:
                print(f"Error scraping '{kw}' di {loc}: {se}")
            scraper.save_to_json("jobs_data.json")

    for scraper in scrapers:
        platform_name = scraper.__class__.__name__.replace("Scraper", "")
        print(f"\n--- Scraping {platform_name} ---")

        with scraper.session():
            if args.skip_home:
                pass
            elif keyword:
                run_scrape(scraper, location, [keyword], max_jobs)
            elif not scraper.uses_browser:
                # Platform API (cepat, tanpa browser): semua keyword tiap run, lebih dalam per keyword
                kws = PRIORITY_KEYWORDS + KEYWORD_POOL
                print(f"Tanpa keyword: {len(kws)} keyword (semua), {args.api_per_keyword} lowongan baru per keyword")
                run_scrape(scraper, location, kws, args.api_per_keyword)
            else:
                kws = PRIORITY_KEYWORDS + home_window
                print(f"Tanpa keyword: {len(PRIORITY_KEYWORDS)} keyword data + {len(home_window)} keyword bergilir: {', '.join(home_window)}")
                run_scrape(scraper, location, kws, max(5, max_jobs // len(kws)))
            for region in remote_regions:
                kws = [keyword] if keyword else REMOTE_KEYWORDS
                run_scrape(scraper, region, kws, max_jobs if keyword else max(5, max_jobs // len(kws)))
        scraper.remote_only = False
