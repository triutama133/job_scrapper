import os
import json
import time
import re
import argparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

class JobScraper:
    def __init__(self, headless=True, filepath="jobs_data.json"):
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

    def run_playwright(self, task_func):
        try:
            from camoufox.sync_api import Camoufox
            print("Launching Camoufox stealth browser (C++ Fingerprint Spoofing)...")
            with Camoufox(headless=self.headless) as browser:
                page = browser.new_page()
                task_func(page)
        except Exception as e:
            print(f"Camoufox failed or not fully initialized: {e}. Falling back to standard Playwright Chromium...")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                    locale="id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                )
                page = context.new_page()
                try:
                    from playwright_stealth import Stealth
                    Stealth().apply_stealth_sync(page)
                except ImportError:
                    pass
                try:
                    task_func(page)
                except Exception as ex:
                    print(f"Error during standard Playwright execution: {ex}")
                finally:
                    browser.close()

    def clean_text(self, text):
        if not text:
            return ""
        # Remove extra whitespaces
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def clean_description(self, text):
        if not text:
            return ""
        lines = text.split('\n')
        cleaned = []
        junk_keywords = ['show more', 'show less', 'lihat selengkapnya', 'lihat lebih sedikit', 'read more', 'read less']
        for line in lines:
            stripped = line.strip()
            # Skip empty lines or lines containing junk keywords
            if not stripped:
                cleaned.append("")
                continue
            if any(jk == stripped.lower() or (len(stripped) < 40 and jk in stripped.lower()) for jk in junk_keywords):
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

    def extract_salary(self, soup, platform):
        platform = platform.lower()
        if platform == "jobstreet":
            elem = soup.find(attrs={"data-automation": "job-detail-salary"})
            if elem:
                return elem.text.strip()
        
        # General heuristics
        # Currency prefix with optional dot after symbol (e.g. Rp., IDR., SGD.)
        # Supports international currencies: USD, SGD, EUR, AUD
        currency = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'
        
        candidates = []
        for tag in ['span', 'div', 'p', 'h3', 'h4']:
            for elem in soup.find_all(tag):
                text = elem.text.strip()
                if not text or len(text) > 100:
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

    def add_job(self, title, company, platform, url, description, job_desk=None, skills=None, salary=None):
        if url in self.seen_urls:
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
            "salary": self.clean_text(salary) if salary else None
        }
        
        self.jobs.append(job_data)
        self.seen_urls.add(url)
        print(f"[{platform}] Scraped: {job_data['job_title']} at {job_data['company']}")
        self.save_to_json()

    def save_to_json(self, filepath=None):
        if filepath:
            self.filepath = filepath
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
            
            scraped_links = set()
            page_num = 1
            max_pages = 4 # Deep pagination search (up to 4 pages)
            
            while len(scraped_links) < max_jobs and page_num <= max_pages:
                url = f"https://glints.com/id/opportunities/jobs/explore?keyword={q_keyword}&locationName={q_location}"
                if page_num > 1:
                    url += f"&page={page_num}"
                    
                print(f"Loading Glints search page {page_num}...")
                try:
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
                        if "/opportunities/jobs/" in href:
                            if not href.startswith("http"):
                                href = "https://glints.com" + href
                            clean_href = href.split('?')[0]
                            if clean_href not in self.seen_urls:
                                current_links.append(clean_href)
                                
                    new_links = set(current_links) - scraped_links
                    if not new_links:
                        print("No new jobs found on this page. Stopping pagination.")
                        break
                        
                    scraped_links.update(new_links)
                    print(f"Glints: Gathered {len(scraped_links)} / {max_jobs} links so far...")
                    page_num += 1
                except Exception as pe:
                    print(f"Glints page {page_num} loading error: {pe}")
                    break
            
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
    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to Kalibrr (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            
            raw_gathered_links = set()
            page_num = 1
            max_pages = min(8, max(2, max_jobs // 5))
            
            while page_num <= max_pages:
                url = f"https://www.kalibrr.com/id-ID/job-board?q={q_keyword}&location={q_location}&page={page_num}"
                print(f"Loading Kalibrr search page {page_num}...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                    
                    for _ in range(3):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(1000)
                        
                    content = page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    page_links = set()
                    for a in soup.find_all('a', href=True):
                        href = a['href']
                        if "/c/" in href and "/jobs/" in href:
                            if not href.startswith("http"):
                                href = "https://www.kalibrr.com" + href
                            page_links.add(href)
                            
                    if not page_links:
                        print("No job links found on this Kalibrr page. Stopping pagination.")
                        break
                        
                    raw_gathered_links.update(page_links)
                    print(f"Kalibrr: Gathered {len(raw_gathered_links)} total raw links so far...")
                    page_num += 1
                except Exception as pe:
                    print(f"Kalibrr page {page_num} error: {pe}")
                    break
            
            unseen_links = [link for link in list(raw_gathered_links) if link not in self.seen_urls]
            links = unseen_links[:max_jobs]
            print(f"Found {len(links)} NEW Kalibrr job links to scrape (out of {len(raw_gathered_links)} raw links).")
            
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            for link in links:
                try:
                    company = "Unknown Company"
                    match = re.search(r'/c/([^/]+)/jobs/', link)
                    if match:
                        company = match.group(1).replace('-', ' ').title()
                        
                    res = requests.get(link, headers=headers, timeout=15)
                    if res.status_code == 200:
                        job_soup = BeautifulSoup(res.text, 'html.parser')
                        title_elem = job_soup.find('h1')
                        title = title_elem.text.strip() if title_elem else "Unknown Title"
                        
                        h2_elems = job_soup.find_all('h2')
                        for h2 in h2_elems:
                            text = h2.text.strip()
                            text_lower = text.lower()
                            if not any(kw in text_lower for kw in ['deskripsi', 'description', 'kualifikasi', 'qualification', 'persyaratan', 'requirement', 'ringkasan', 'summary', 'tentang', 'about', 'kandidat', 'rekomendasi', 'lokasi', 'location', 'jobs']):
                                company = text
                                break
                        
                        desc_parts = job_soup.find_all(class_=re.compile(r'css-hrhjkp|description|job-description', re.I))
                        if desc_parts:
                            description = "\n".join([part.get_text(separator='\n').strip() for part in desc_parts])
                        else:
                            desc_elem = job_soup.find('article') or job_soup.find('main')
                            description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                        
                        salary = self.extract_salary(job_soup, "Kalibrr")
                        if title != "Unknown Title" and description:
                            self.add_job(title, company, "Kalibrr", link, description, salary=salary)
                            print(f"[Kalibrr] Scraped: {title} at {company}")
                            continue
                            
                    page.goto(link, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)
                    job_soup = BeautifulSoup(page.content(), 'html.parser')
                    title_elem = job_soup.find('h1')
                    title = title_elem.text.strip() if title_elem else "Unknown Title"
                    desc_elem = job_soup.find('article') or job_soup.find('main')
                    description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                    salary = self.extract_salary(job_soup, "Kalibrr")
                    if title != "Unknown Title" and description:
                        self.add_job(title, company, "Kalibrr", link, description, salary=salary)
                        print(f"[Kalibrr Browser Fallback] Scraped: {title} at {company}")
                except Exception as e:
                    print(f"Error scraping Kalibrr job {link}: {e}")
                    
        self.run_playwright(task)


class JobstreetScraper(JobScraper):
    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to Jobstreet (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            
            scraped_links = set()
            page_num = 1
            max_pages = 4 # Deep pagination search (up to 4 pages)
            
            while len(scraped_links) < max_jobs and page_num <= max_pages:
                url = f"https://www.jobstreet.co.id/id/job-search/{q_keyword}-jobs-in-{q_location}"
                if page_num > 1:
                    url += f"?page={page_num}"
                    
                print(f"Loading Jobstreet search page {page_num}...")
                try:
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
                                href = "https://www.jobstreet.co.id" + href
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
            
            links = list(scraped_links)[:max_jobs]
            print(f"Found {len(links)} Jobstreet job links to scrape.")
            
            for link in links:
                try:
                    normalized_link = link.split('?')[0]
                    page.goto(normalized_link, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    
                    job_soup = BeautifulSoup(page.content(), 'html.parser')
                    
                    title_elem = job_soup.find('h1') or job_soup.find(class_=re.compile(r'JobTitle', re.I))
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
                    
                    desc_elem = job_soup.find(class_=re.compile(r'JobDescription|description|job-description', re.I)) or job_soup.find(attrs={"data-automation": "jobDescription"})
                    description = desc_elem.get_text(separator='\n').strip() if desc_elem else ""
                    
                    salary = self.extract_salary(job_soup, "Jobstreet")
                    if title != "Unknown Title" and description:
                        self.add_job(title, company, "Jobstreet", normalized_link, description, salary=salary)
                except Exception as e:
                    print(f"Error scraping Jobstreet job {link}: {e}")
                    
        self.run_playwright(task)


class LinkedInScraper(JobScraper):
    def scrape(self, max_jobs=10, location="Indonesia", keyword=""):
        def task(page):
            print(f"Navigating to LinkedIn Jobs (Keyword: '{keyword}', Location: '{location}')...")
            import urllib.parse
            q_keyword = urllib.parse.quote(keyword)
            q_location = urllib.parse.quote(location)
            
            raw_gathered_links = set()
            start_offset = 0
            max_pages = min(8, max(2, max_jobs // 5))
            page_count = 0
            
            while page_count < max_pages:
                url = f"https://www.linkedin.com/jobs/search?keywords={q_keyword}&location={q_location}&start={start_offset}"
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
                                self.add_job(title, company, "LinkedIn", link, description, salary=salary)
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
                        self.add_job(title, company, "LinkedIn", link, description, salary=salary)
                        print(f"[LinkedIn Browser Fallback] Scraped: {title} at {company}")
                except Exception as e:
                    print(f"Error scraping LinkedIn job {link}: {e}")
                    
        self.run_playwright(task)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scraper for Glints, Kalibrr, Jobstreet, and LinkedIn")
    parser.add_argument("--max-jobs", type=int, default=5, help="Maximum number of jobs to scrape per platform")
    parser.add_argument("--platform", type=str, default="all", choices=["all", "glints", "kalibrr", "jobstreet", "linkedin"], help="Specific platform to scrape")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")
    parser.add_argument("--location", type=str, default="Indonesia", help="Location to search jobs for (e.g. Indonesia, Singapore, United States)")
    parser.add_argument("--keyword", type=str, default="", help="Keyword to search jobs for (e.g. 'Software Engineer')")
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

    for scraper in scrapers:
        platform_name = scraper.__class__.__name__.replace("Scraper", "")
        print(f"\n--- Scraping {platform_name} ---")
        
        # Rotasi keyword jika parameter keyword kosong untuk mendapatkan data yang bervariasi
        if not keyword:
            keywords_to_run = [
                "Software Engineer", "Data Entry", "Digital Marketing", "Graphic Designer", 
                "Project Manager", "Sales", "Human Resources", "Content Creator", 
                "Business Development", "Accountant", "Customer Service", "Social Media Specialist"
            ]
            jobs_per_keyword = max(5, max_jobs // len(keywords_to_run))
            print(f"No specific keyword provided. Rotating through {len(keywords_to_run)} high-value categories ({jobs_per_keyword} jobs per keyword)...")
            
            for kw in keywords_to_run:
                print(f"\n[Keyword: '{kw}']")
                try:
                    scraper.scrape(max_jobs=jobs_per_keyword, location=location, keyword=kw)
                except Exception as se:
                    print(f"Error scraping {platform_name} with keyword '{kw}': {se}")
        else:
            scraper.scrape(max_jobs=max_jobs, location=location, keyword=keyword)
            
        scraper.save_to_json("jobs_data.json")

    print("\nScraping complete.")
