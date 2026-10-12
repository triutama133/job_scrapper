import os
import json
import time
import re
import argparse
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from scraper import JobScraper

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

class RemoteJobScraper(JobScraper):
    """
    Multi-source Remote Job Scraper specializing in Data Engineering & Tech roles.
    Aggregates high-trust platforms:
    - Remotive (REST API)
    - We Work Remotely (RSS Feeds)
    - RemoteOK (JSON API)
    - Himalayas (REST API)
    - Jobicy (REST API)
    - Arbeitnow (REST API)
    - LinkedIn Remote (Guest Search API)
    - Upwork (Personal RSS Feed / Export)
    """

    def __init__(self, filepath="jobs_data.json", headless=True):
        super().__init__(headless=headless, filepath=filepath)
        self.headers = {"User-Agent": DEFAULT_USER_AGENT}

    def _http_get(self, url, timeout=15):
        """Helper to perform HTTP GET requests with standard user agent."""
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            print(f"  [HTTP Error] Gagal memuat {url[:70]}... ({e})")
            return None

    def add_remote_job(self, title, company, platform, url, description,
                       job_desk=None, skills=None, salary=None,
                       category="Data & Analytics"):
        """Menambahkan entri lowongan kerja remote ke database lokal."""
        if not url:
            return
            
        clean_url = url.split("?")[0].strip()
        if clean_url in self.seen_urls:
            return

        cleaned_desc = self.clean_description(description) if description else ""
        if not cleaned_desc or len(cleaned_desc.strip()) < 15:
            # Lewati jika deskripsi kosong atau terlalu pendek
            return

        # Ekstrak tugas & kualifikasi jika belum ada
        h_resp, h_skills = self.extract_skills_and_responsibilities(cleaned_desc)
        final_job_desk = job_desk or h_resp
        if not final_job_desk:
            final_job_desk = [
                line.strip() for line in cleaned_desc.split("\n")
                if len(line.strip()) > 15
            ][:15]

        final_skills = skills or h_skills

        job_data = {
            "job_title": self.clean_text(title),
            "company": self.clean_text(company),
            "platform": platform,
            "job_url": clean_url,
            "description": cleaned_desc,
            "job_desk": final_job_desk,
            "requirements": final_skills,
            "is_active": True,
            "status_reason": "Active",
            "category": category,
            "salary": self.clean_salary_text(salary) if salary else None,
            "source": "remote_scraper"
        }

        self.jobs.append(job_data)
        self.seen_urls.add(clean_url)
        print(f"[{platform}] Scraped: {job_data['job_title']} @ {job_data['company']} (Salary: {job_data['salary'] or 'N/A'})")
        self.save_to_json()

    # -------------------------------------------------------------------------
    # 1. REMOTIVE (REST API)
    # -------------------------------------------------------------------------
    def fetch_remotive(self, keyword="data engineer", limit=30):
        print(f"\n--- [1/8] Remotive API (Keyword: '{keyword}', Limit: {limit}) ---")
        q = urllib.parse.quote(keyword)
        url = f"https://remotive.com/api/remote-jobs?search={q}"
        raw = self._http_get(url)
        if not raw:
            return 0

        count = 0
        try:
            data = json.loads(raw.decode("utf-8"))
            jobs = data.get("jobs", [])
            for j in jobs:
                if count >= limit:
                    break
                title = j.get("title", "")
                company = j.get("company_name", "Unknown Company")
                job_url = j.get("url")
                raw_desc = j.get("description", "")
                desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")
                salary = j.get("salary")
                tags = j.get("tags", [])

                if job_url and title:
                    before_count = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="Remotive",
                        url=job_url,
                        description=desc_text,
                        skills=tags if tags else None,
                        salary=salary
                    )
                    if len(self.seen_urls) > before_count:
                        count += 1
        except Exception as e:
            print(f"  [Remotive] Error parsing data: {e}")

        print(f"-> Remotive: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 2. WE WORK REMOTELY (RSS FEEDS)
    # -------------------------------------------------------------------------
    def fetch_weworkremotely(self, keywords=None, limit=30):
        print(f"\n--- [2/8] We Work Remotely RSS Feeds (Limit: {limit}) ---")
        feeds = [
            "https://weworkremotely.com/categories/remote-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
        ]
        if keywords is None:
            keywords = ["data", "etl", "analytics", "backend", "engineer", "pipeline", "sql", "python"]

        count = 0
        for feed_url in feeds:
            if count >= limit:
                break
            raw = self._http_get(feed_url)
            if not raw:
                continue
            try:
                root = ET.fromstring(raw)
                for item in root.findall(".//item"):
                    if count >= limit:
                        break
                    raw_title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    raw_desc = item.find("description").text if item.find("description") is not None else ""

                    # Filter relevansi jika keyword diberikan
                    if keywords:
                        text_check = f"{raw_title} {raw_desc}".lower()
                        if not any(k.lower() in text_check for k in keywords):
                            continue

                    # WWR title format biasanya: "Company: Job Title"
                    if ":" in raw_title:
                        comp, tit = raw_title.split(":", 1)
                        company = comp.strip()
                        title = tit.strip()
                    else:
                        company = "WWR Partner"
                        title = raw_title.strip()

                    desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")
                    if link and title:
                        before = len(self.seen_urls)
                        self.add_remote_job(
                            title=title,
                            company=company,
                            platform="WeWorkRemotely",
                            url=link,
                            description=desc_text
                        )
                        if len(self.seen_urls) > before:
                            count += 1
            except Exception as e:
                print(f"  [WWR] Error parsing XML feed: {e}")

        print(f"-> We Work Remotely: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 3. REMOTE OK (JSON API)
    # -------------------------------------------------------------------------
    def fetch_remoteok(self, tag="data", limit=30):
        print(f"\n--- [3/8] RemoteOK API (Tag: '{tag}', Limit: {limit}) ---")
        url = f"https://remoteok.com/api?tag={urllib.parse.quote(tag)}"
        raw = self._http_get(url)
        if not raw:
            return 0

        count = 0
        try:
            data = json.loads(raw.decode("utf-8"))
            jobs = [j for j in data if isinstance(j, dict) and "position" in j]
            for j in jobs:
                if count >= limit:
                    break
                title = j.get("position", "")
                company = j.get("company", "RemoteOK Company")
                job_url = j.get("url") or (f"https://remoteok.com/remote-jobs/{j.get('id')}" if j.get("id") else None)
                raw_desc = j.get("description", "")
                desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")
                
                # Format gaji
                sal_min = j.get("salary_min")
                sal_max = j.get("salary_max")
                salary = None
                if sal_min and sal_max:
                    salary = f"${int(sal_min):,} - ${int(sal_max):,} USD/year"
                elif sal_min:
                    salary = f"${int(sal_min):,} USD/year"

                tags = j.get("tags", [])

                if job_url and title:
                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="RemoteOK",
                        url=job_url,
                        description=desc_text,
                        skills=tags if tags else None,
                        salary=salary
                    )
                    if len(self.seen_urls) > before:
                        count += 1
        except Exception as e:
            print(f"  [RemoteOK] Error parsing JSON: {e}")

        print(f"-> RemoteOK: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 4. HIMALAYAS (REST API)
    # -------------------------------------------------------------------------
    def fetch_himalayas(self, keyword="data engineer", limit=30):
        print(f"\n--- [4/8] Himalayas API (Keyword: '{keyword}', Limit: {limit}) ---")
        q = urllib.parse.quote(keyword)
        url = f"https://himalayas.app/jobs/api?search={q}"
        raw = self._http_get(url)
        if not raw:
            return 0

        count = 0
        try:
            data = json.loads(raw.decode("utf-8"))
            jobs = data.get("jobs", [])
            for j in jobs:
                if count >= limit:
                    break
                title = j.get("title", "")
                company = j.get("companyName", "Himalayas Partner")
                job_url = j.get("applicationLink") or j.get("guid") or (
                    f"https://himalayas.app/companies/{j.get('companySlug')}/jobs/{j.get('slug')}"
                    if j.get("slug") and j.get("companySlug") else None
                )
                raw_desc = j.get("description") or j.get("excerpt", "")
                desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")

                min_sal = j.get("minSalary")
                max_sal = j.get("maxSalary")
                curr = j.get("currency", "USD")
                salary = None
                if min_sal and max_sal:
                    salary = f"${int(min_sal):,} - ${int(max_sal):,} {curr}"
                elif min_sal:
                    salary = f"${int(min_sal):,} {curr}"

                if job_url and title:
                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="Himalayas",
                        url=job_url,
                        description=desc_text,
                        salary=salary
                    )
                    if len(self.seen_urls) > before:
                        count += 1
        except Exception as e:
            print(f"  [Himalayas] Error parsing data: {e}")

        print(f"-> Himalayas: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 5. JOBICY (REST API)
    # -------------------------------------------------------------------------
    def fetch_jobicy(self, tag="data", limit=30):
        print(f"\n--- [5/8] Jobicy API (Tag: '{tag}', Limit: {limit}) ---")
        url = f"https://jobicy.com/api/v2/remote-jobs?count={limit}&tag={urllib.parse.quote(tag)}"
        raw = self._http_get(url)
        if not raw:
            return 0

        count = 0
        try:
            data = json.loads(raw.decode("utf-8"))
            jobs = data.get("jobs", [])
            for j in jobs:
                if count >= limit:
                    break
                title = j.get("jobTitle", "")
                company = j.get("companyName", "Jobicy Partner")
                job_url = j.get("url")
                raw_desc = j.get("jobDescription", "")
                desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")

                min_sal = j.get("annualSalaryMin")
                max_sal = j.get("annualSalaryMax")
                curr = j.get("salaryCurrency", "USD")
                salary = None
                if min_sal and max_sal:
                    salary = f"${int(min_sal):,} - ${int(max_sal):,} {curr}/year"
                elif min_sal:
                    salary = f"${int(min_sal):,} {curr}/year"

                if job_url and title:
                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="Jobicy",
                        url=job_url,
                        description=desc_text,
                        salary=salary
                    )
                    if len(self.seen_urls) > before:
                        count += 1
        except Exception as e:
            print(f"  [Jobicy] Error parsing data: {e}")

        print(f"-> Jobicy: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 6. ARBEITNOW (REST API)
    # -------------------------------------------------------------------------
    def fetch_arbeitnow(self, keyword="data", limit=30):
        print(f"\n--- [6/8] Arbeitnow API (Keyword: '{keyword}', Limit: {limit}) ---")
        url = "https://www.arbeitnow.com/api/job-board-api"
        raw = self._http_get(url)
        if not raw:
            return 0

        count = 0
        try:
            data = json.loads(raw.decode("utf-8"))
            jobs = data.get("data", [])
            filtered = [
                j for j in jobs
                if j.get("remote") and any(keyword.lower() in str(f).lower() for f in [j.get("title"), j.get("tags")])
            ]
            for j in filtered:
                if count >= limit:
                    break
                title = j.get("title", "")
                company = j.get("company_name", "Arbeitnow Partner")
                job_url = j.get("url")
                raw_desc = j.get("description", "")
                desc_text = BeautifulSoup(raw_desc, "html.parser").get_text(separator="\n")
                tags = j.get("tags", [])

                if job_url and title:
                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="Arbeitnow",
                        url=job_url,
                        description=desc_text,
                        skills=tags if tags else None
                    )
                    if len(self.seen_urls) > before:
                        count += 1
        except Exception as e:
            print(f"  [Arbeitnow] Error parsing data: {e}")

        print(f"-> Arbeitnow: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 7. LINKEDIN REMOTE (GUEST SEARCH API)
    # -------------------------------------------------------------------------
    def fetch_linkedin_remote(self, keyword="Data Engineer", limit=30):
        print(f"\n--- [7/8] LinkedIn Remote (Keyword: '{keyword}', Limit: {limit}) ---")
        count = 0
        start = 0
        max_start = max(100, limit * 2)

        while count < limit and start < max_start:
            q = urllib.parse.quote(keyword)
            # f_WT=2 menandakan Remote workplace filter resmi di LinkedIn
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}&f_WT=2&start={start}"
            raw = self._http_get(url)
            if not raw:
                break

            try:
                soup = BeautifulSoup(raw.decode("utf-8"), "html.parser")
                cards = soup.find_all("div", class_="base-card")
                if not cards:
                    break

                for card in cards:
                    if count >= limit:
                        break
                    t = card.find("h3", class_="base-search-card__title")
                    c = card.find("h4", class_="base-search-card__subtitle")
                    link = card.find("a", class_="base-card__full-link")
                    if not (t and c and link):
                        continue

                    job_url = link["href"].split("?")[0].strip()
                    if job_url in self.seen_urls:
                        continue

                    title = t.text.strip()
                    company = c.text.strip()

                    # Ambil deskripsi lengkap dari detail page
                    detail_raw = self._http_get(job_url, timeout=10)
                    desc_text = ""
                    salary = None
                    if detail_raw:
                        d_soup = BeautifulSoup(detail_raw.decode("utf-8", errors="ignore"), "html.parser")
                        desc_elem = d_soup.find("div", class_="show-more-less-html__markup")
                        if desc_elem:
                            desc_text = desc_elem.get_text(separator="\n")
                        salary = self.extract_salary(d_soup, "linkedin")

                    if not desc_text:
                        desc_text = f"Remote job posting for {title} at {company}. Apply via LinkedIn."

                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=title,
                        company=company,
                        platform="LinkedIn Remote",
                        url=job_url,
                        description=desc_text,
                        salary=salary
                    )
                    if len(self.seen_urls) > before:
                        count += 1
                        time.sleep(0.5)  # Jeda sopan antar detail page

                start += 25
            except Exception as e:
                print(f"  [LinkedIn Remote] Error pada offset {start}: {e}")
                break

        print(f"-> LinkedIn Remote: Berhasil mengumpulkan {count} lowongan baru.")
        return count

    # -------------------------------------------------------------------------
    # 8. UPWORK (PERSONAL RSS FEED / EXPORT XML)
    # -------------------------------------------------------------------------
    def fetch_upwork(self, rss_url=None, file_path=None, limit=30):
        print(f"\n--- [8/8] Upwork Freelance Gigs (Limit: {limit}) ---")
        feed_url = rss_url or os.environ.get("UPWORK_RSS_URL")

        xml_content = None
        if file_path and os.path.exists(file_path):
            print(f"  Membaca file data Upwork: {file_path}")
            if file_path.endswith(".json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    count = 0
                    for item in items[:limit]:
                        title = item.get("title")
                        url = item.get("url")
                        desc = item.get("description", "")
                        salary = item.get("budget") or item.get("salary")
                        skills = item.get("skills", [])
                        if title and url:
                            before = len(self.seen_urls)
                            self.add_remote_job(
                                title=title,
                                company="Upwork Client",
                                platform="Upwork",
                                url=url,
                                description=desc,
                                skills=skills if skills else None,
                                salary=salary
                            )
                            if len(self.seen_urls) > before:
                                count += 1
                    print(f"-> Upwork: Berhasil memproses {count} proyek dari {file_path}.")
                    return count
                except Exception as je:
                    print(f"  [Upwork] Error membaca file JSON: {je}")
                    return 0
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    xml_content = f.read().encode("utf-8")
        elif feed_url:
            print(f"  Mengunduh Upwork feed dari RSS URL terotentikasi...")
            xml_content = self._http_get(feed_url)
        else:
            print("  [INFO] UPWORK_RSS_URL belum disetel di file .env atau argumen.")
            print("  Cara mendapatkan Link Feed Upwork pribadi:")
            print("  1. Buka Upwork -> Cari lowongan 'Data Engineer'")
            print("  2. Klik ikon 'RSS' di pojok kanan hasil pencarian")
            print("  3. Salin URL feed dan gunakan opsi --upwork-rss <URL> atau simpan ke .env")
            return 0

        if not xml_content:
            return 0

        count = 0
        try:
            root = ET.fromstring(xml_content)
            for item in root.findall(".//item"):
                if count >= limit:
                    break
                raw_title = item.find("title").text if item.find("title") is not None else "Upwork Project"
                link = item.find("link").text if item.find("link") is not None else ""
                raw_desc = item.find("description").text if item.find("description") is not None else ""

                desc_soup = BeautifulSoup(raw_desc, "html.parser")
                desc_text = desc_soup.get_text(separator="\n")

                # Ekstraksi budget / hourly rate
                salary = None
                sal_match = re.search(
                    r'(Hourly Range|Budget):\s*(\$[0-9.,]+(?:\s*-\s*\$[0-9.,]+)?)',
                    desc_text,
                    re.I
                )
                if sal_match:
                    salary = sal_match.group(0).strip()

                # Ekstraksi skills tag dari teks Upwork: "Skills: Python, Airflow..."
                skills = []
                skills_match = re.search(r'Skills:\s*([^\n]+)', desc_text, re.I)
                if skills_match:
                    skills = [s.strip() for s in skills_match.group(1).split(",") if s.strip()]

                if link and raw_title:
                    before = len(self.seen_urls)
                    self.add_remote_job(
                        title=raw_title,
                        company="Upwork Client",
                        platform="Upwork",
                        url=link,
                        description=desc_text,
                        skills=skills if skills else None,
                        salary=salary
                    )
                    if len(self.seen_urls) > before:
                        count += 1
        except Exception as e:
            print(f"  [Upwork] Error parsing RSS XML: {e}")

        print(f"-> Upwork: Berhasil mengumpulkan {count} proyek freelance baru.")
        return count

    # -------------------------------------------------------------------------
    # AGGREGATOR RUNNER
    # -------------------------------------------------------------------------
    def scrape_all(self, keyword="data engineer", limit_per_source=25,
                    sources=None, upwork_rss=None, upwork_file=None):
        """Menjalankan seluruh atau sebagian sumber remote job scraper."""
        all_sources = ["remotive", "weworkremotely", "remoteok", "himalayas",
                       "jobicy", "arbeitnow", "linkedin", "upwork"]
        selected = [s.strip().lower() for s in sources.split(",")] if sources else all_sources

        print("=" * 70)
        print("MEMULAI MULTI-SOURCE REMOTE JOB SCRAPER (FOKUS: DATA ENGINEERING)")
        print(f"Target Sumber     : {', '.join(selected)}")
        print(f"Limit per Sumber  : {limit_per_source} lowongan")
        print("=" * 70)

        total_scraped = 0

        if "remotive" in selected:
            total_scraped += self.fetch_remotive(keyword=keyword, limit=limit_per_source)
        if "weworkremotely" in selected:
            total_scraped += self.fetch_weworkremotely(limit=limit_per_source)
        if "remoteok" in selected:
            total_scraped += self.fetch_remoteok(tag="data", limit=limit_per_source)
        if "himalayas" in selected:
            total_scraped += self.fetch_himalayas(keyword=keyword, limit=limit_per_source)
        if "jobicy" in selected:
            total_scraped += self.fetch_jobicy(tag="data", limit=limit_per_source)
        if "arbeitnow" in selected:
            total_scraped += self.fetch_arbeitnow(keyword=keyword, limit=limit_per_source)
        if "linkedin" in selected:
            total_scraped += self.fetch_linkedin_remote(keyword="Data Engineer", limit=limit_per_source)
        if "upwork" in selected:
            total_scraped += self.fetch_upwork(rss_url=upwork_rss, file_path=upwork_file, limit=limit_per_source)

        print("\n" + "=" * 70)
        print(f"SELESAI! Total lowongan/proyek remote baru yang didapat: {total_scraped}")
        print(f"Total seluruh lowongan di {self.filepath}: {len(self.seen_urls)}")
        print("=" * 70)
        return total_scraped


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Multi-Source Remote Job Scraper untuk Data Engineering.")
    parser.add_argument("--keyword", type=str, default="data engineer", help="Kata kunci pencarian (default: 'data engineer')")
    parser.add_argument("--limit", type=int, default=25, help="Jumlah lowongan maksimum per platform (default: 25)")
    parser.add_argument("--sources", type=str, default=None, help="Daftar sumber (koma dipisah): remotive,weworkremotely,remoteok,himalayas,jobicy,arbeitnow,linkedin,upwork")
    parser.add_argument("--file", type=str, default="jobs_data.json", help="Path ke database JSON (default: jobs_data.json)")
    parser.add_argument("--upwork-rss", type=str, default=None, help="URL RSS Feed pribadi dari Upwork")
    parser.add_argument("--upwork-file", type=str, default=None, help="Path ke file XML RSS Upwork lokal (opsional)")
    parser.add_argument("--upload-supabase", action="store_true", default=False, help="Langsung sinkronisasi data baru ke Supabase setelah scraping selesai")
    args = parser.parse_args()

    scraper = RemoteJobScraper(filepath=args.file)
    total_added = scraper.scrape_all(
        keyword=args.keyword,
        limit_per_source=args.limit,
        sources=args.sources,
        upwork_rss=args.upwork_rss,
        upwork_file=args.upwork_file
    )

    if args.upload_supabase and total_added > 0:
        print("\n[Supabase] Mengunggah data remote baru ke Supabase...")
        try:
            from upload_to_supabase import upload_jobs_to_supabase
            upload_jobs_to_supabase(filepath=args.file)
        except Exception as e:
            print(f"[Supabase] Gagal mengunggah ke Supabase: {e}")
