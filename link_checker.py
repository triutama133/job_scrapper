import os
import json
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Headers to mimic a real browser request
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive"
}

# Heuristics for closed/inactive job listings in the page text
# Heuristics for closed/inactive job listings in the page text
CLOSED_KEYWORDS = [
    "no longer accepting applications",
    "tidak lagi menerima lamaran",
    "this job is closed",
    "job has expired",
    "this job has expired",
    "lowongan ini sudah ditutup",
    "halaman tidak ditemukan",
    "page not found",
    "this role is closed",
    "no longer accepting responses",
    "tidak lagi diiklankan",
    "lowongan kerja ini tidak lagi diiklankan"
]

def check_link(job):
    url = job.get("job_url")
    platform = job.get("platform", "").lower()
    
    if not url:
        return job, "error", "Missing URL"
        
    try:
        # We allow redirects but monitor where it goes
        response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        final_url = response.url
        status_code = response.status_code
        
        # 1. Check HTTP Status
        if status_code in [404, 410]:
            return job, False, f"HTTP {status_code}"
            
        # If blocked by anti-bot, we cannot determine for sure, so we keep it active (or unchanged)
        if status_code in [403, 401]:
            # Check if we can still read some HTML (sometimes it returns 403 but has Turnstile)
            if "verify you are human" in response.text.lower() or "cloudflare" in response.text.lower():
                return job, True, "HTTP 403 (Cloudflare Block - Assuming Active)"
            return job, True, f"HTTP {status_code} (Blocked - Assuming Active)"

        # 2. Check Redirects & Platform specific markers
        html_content = response.text.lower()
        
        if platform == "kalibrr":
            # Kalibrr redirects closed jobs to the home page or job board
            if final_url.strip('/') in ["https://www.kalibrr.com", "https://www.kalibrr.com/job-board", "https://www.kalibrr.id", "https://www.kalibrr.com/id-ID/home"]:
                return job, False, "Redirected to Homepage/Job Board (Closed)"
            
            # Kalibrr embeds state in JSON script block. e.g. "isexpired":true
            # Strip spaces to ensure robust matching in JSON properties
            html_no_space = html_content.replace(" ", "").replace("\n", "").replace("\r", "")
            if '"isexpired":true' in html_no_space or '"is_expired":true' in html_no_space:
                return job, False, "Kalibrr JSON state: isexpired = true"
                
        elif platform == "linkedin":
            # LinkedIn redirects closed jobs to general search if they are expired
            if "/jobs/search" in final_url or "/jobs/collections" in final_url:
                return job, False, "Redirected to Search Page (Closed)"
                
        elif platform == "jobstreet":
            # Jobstreet indicates closed/expired ads via expiredJobPage container or specific text
            if "expiredjobpage" in html_content or "tidak lagi diiklankan" in html_content:
                return job, False, "Jobstreet: Lowongan kerja ini tidak lagi diiklankan"
                
        # 3. Check HTML Content Keywords
        # Look for indicators of closed applications
        for kw in CLOSED_KEYWORDS:
            if kw in html_content:
                return job, False, f"Found closed keyword: {kw}"
                
        return job, True, "Active"
        
    except requests.exceptions.RequestException as e:
        return job, True, f"Connection Error: {e} (Assuming Active)"

def run_link_checker(filepath="jobs_data.json", max_workers=10):
    if not os.path.exists(filepath):
        print(f"File {filepath} tidak ditemukan.")
        return
        
    with open(filepath, 'r', encoding='utf-8') as f:
        jobs = json.load(f)
        
    # Filter only jobs that are not already marked inactive
    jobs_to_check = []
    skipped_jobs = []
    
    for job in jobs:
        if job.get("is_active") is False:
            skipped_jobs.append(job)
        else:
            jobs_to_check.append(job)
            
    print(f"Memulai pemeriksaan link lowongan kerja: {len(jobs_to_check)} aktif diperiksa, {len(skipped_jobs)} tidak aktif dilewati.")
    
    updated_jobs = []
    active_count = 0
    inactive_count = len(skipped_jobs)
    
    if jobs_to_check:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit only active jobs to thread pool
            futures = {executor.submit(check_link, job): job for job in jobs_to_check}
            
            for future in as_completed(futures):
                job, is_active, reason = future.result()
                
                # Add or update the marker columns
                job["is_active"] = is_active
                job["last_checked"] = datetime.now().isoformat()
                job["status_reason"] = reason
                
                updated_jobs.append(job)
                
                status_str = "ACTIVE" if is_active else "INACTIVE"
                print(f"[{status_str}] {job.get('job_title')} ({job.get('company')}) - Reason: {reason}")
                
                if is_active:
                    active_count += 1
                else:
                    inactive_count += 1
    else:
        print("Tidak ada lowongan aktif yang perlu diperiksa.")

    # Merge check results with skipped jobs
    all_updated_jobs = updated_jobs + skipped_jobs

    # Save the updated records back to the JSON file
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(all_updated_jobs, f, indent=4, ensure_ascii=False)
        
    print("\nPemeriksaan selesai.")
    print(f"Total Lowongan: {len(jobs)}")
    print(f"Aktif: {active_count}")
    print(f"Tidak Aktif/Tutup: {inactive_count}")

if __name__ == "__main__":
    run_link_checker("jobs_data.json")
