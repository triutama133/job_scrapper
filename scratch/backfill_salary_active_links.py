import json
import re
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, 'scratch')
from extract_salaries_from_desc import extract_salary_from_text

import requests
from bs4 import BeautifulSoup

filepath = "jobs_data.json"
DEBUG_LIMIT = None

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,id;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def extract_salary_from_soup(soup, platform):
    """Same logic as scraper.py extract_salary() -- but standalone."""
    platform = (platform or '').lower()
    if platform == "jobstreet":
        elem = soup.find(attrs={"data-automation": "job-detail-salary"})
        if elem and elem.text.strip():
            return elem.text.strip()

    currency = r'(?:Rp|IDR|SGD|USD|EUR|AUD|S\$|\$)\s*\.?\s*'
    candidates = []
    for tag in ['span', 'div', 'p', 'h3', 'h4']:
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
                cand, re.I
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


def process_job(job):
    url = job.get('job_url')
    if not url or job.get('salary'):
        return job, 'skipped'

    # --- STEP 1: Try to fetch salary directly from the live page ---
    status_code = None
    from_page = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        status_code = resp.status_code
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            from_page = extract_salary_from_soup(soup, job.get('platform', ''))
    except Exception:
        status_code = 'error'

    if from_page:
        job['salary'] = from_page
        return job, f'page(status={status_code})'

    # --- STEP 2: Link expired/dead OR page has no salary element --> fallback to description ---
    desc_salary = extract_salary_from_text(job.get('description', '') or '')
    if desc_salary:
        job['salary'] = desc_salary
        return job, f'desc(status={status_code})'

    return job, f'none(status={status_code})'


def main():
    global DEBUG_LIMIT
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None, help='Only process first N jobs (debug/testing)')
    args = parser.parse_args()
    DEBUG_LIMIT = args.limit

    print("Loading jobs_data.json...")
    with open(filepath, 'r', encoding='utf-8') as f:
        jobs = json.load(f)

    already_had = sum(1 for j in jobs if j.get('salary'))
    pending = [j for j in jobs if not j.get('salary')]
    if DEBUG_LIMIT:
        pending = pending[:DEBUG_LIMIT]
        print(f">>> MODE TEST: hanya memproses {len(pending)} job pertama")
    print(f"Total jobs: {len(jobs)}")
    print(f"Sudah punya salary: {already_had}")
    print(f"Perlu diproses: {len(pending)}")
    print()

    from_page = 0
    from_desc = 0
    none = 0

    # Parallel fetch with 15 workers
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(process_job, j): j for j in pending}
        done = 0
        for fut in as_completed(futures):
            job, src = fut.result()
            if src.startswith('page'):
                from_page += 1
            elif src.startswith('desc'):
                from_desc += 1
            else:
                none += 1
            done += 1
            if done % 250 == 0:
                print(f"  Progress: {done}/{len(pending)} | page={from_page} desc={from_desc} none={none}", flush=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=4, ensure_ascii=False)

    total_with = sum(1 for j in jobs if j.get('salary'))
    print(f"\nSelesai.")
    print(f"  Salary dari halaman portal:  {from_page}")
    print(f"  Salary fallback deskripsi:   {from_desc}")
    print(f"  Tidak ditemukan:             {none}")
    print(f"  Total dengan salary: {total_with}/{len(jobs)} ({total_with/len(jobs)*100:.2f}%)")
    print("Saved updated jobs_data.json.")


if __name__ == "__main__":
    main()