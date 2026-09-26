import re
from camoufox.sync_api import Camoufox
from bs4 import BeautifulSoup

print("Launching Camoufox stealth browser for Jobstreet debug...")
with Camoufox(headless=True) as browser:
    page = browser.new_page()
    
    print("Navigating to Jobstreet search...")
    page.goto("https://www.jobstreet.co.id/id/job-search/python-jobs-in-Indonesia", wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)
    
    print(f"Final URL: {page.url}")
    page.screenshot(path="jobstreet_debug.png")
    print("Saved screenshot to jobstreet_debug.png")
    
    soup = BeautifulSoup(page.content(), 'html.parser')
    matching = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/job/' in href:
            if not href.startswith('http'):
                href = 'https://www.jobstreet.co.id' + href
            clean = href.split('?')[0]
            if clean not in matching:
                matching.append(clean)
                
    print(f"Total job links found: {len(matching)}")
    if matching:
        sample_url = matching[0]
        print(f"\nTesting extraction on sample job: {sample_url}")
        page.goto(sample_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        
        job_soup = BeautifulSoup(page.content(), 'html.parser')
        
        # Title
        title_elem = (
            job_soup.find(attrs={"data-automation": "job-detail-title"}) 
            or job_soup.find('h1') 
            or job_soup.find(class_=re.compile(r'JobTitle', re.I))
        )
        title = title_elem.text.strip() if title_elem else "Unknown Title"
        
        # Company
        company_elem = job_soup.find(attrs={"data-automation": "advertiser-name"})
        company = company_elem.text.strip() if company_elem else "Unknown Company"
        
        # Salary
        sal_elem = job_soup.find(attrs={"data-automation": "job-detail-salary"})
        salary = sal_elem.text.strip() if sal_elem else "Not specified"
        
        # Description
        desc_elem = (
            job_soup.find(attrs={"data-automation": "jobAdDetails"}) 
            or job_soup.find(attrs={"data-automation": re.compile(r"^job-?ad-?details$", re.I)})
        )
        desc = desc_elem.get_text(separator='\n').strip() if desc_elem else "Not found"
        
        print(f"-> Title: {title}")
        print(f"-> Company: {company}")
        print(f"-> Salary: {salary}")
        print(f"-> Description Length: {len(desc)}")
        print(f"-> Description Preview:\n{desc[:300]}...\n")
