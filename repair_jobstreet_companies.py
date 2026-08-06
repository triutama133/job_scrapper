# repair_jobstreet_companies.py
import json
import re
import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

def repair_companies(filepath="jobs_data.json"):
    print(f"Loading {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Identifikasi baris dengan nama perusahaan yang tidak valid
    to_repair = []
    for idx, job in enumerate(data):
        if job.get('platform') == 'Jobstreet':
            company = job.get('company', '')
            # Cek jika company berupa angka (rating), mengandung kalimat boilerplate, atau Unknown Company
            if (re.match(r'^\d+(\.\d+)?$', company) or 
                'Selengkapnya tentang perusahaan' in company or 
                company == 'Unknown Company'):
                to_repair.append((idx, job['job_title'], job['job_url'], company))

    print(f"Ditemukan {len(to_repair)} lowongan Jobstreet dengan nama perusahaan tidak valid untuk diperbaiki.")
    if not to_repair:
        print("Tidak ada data yang perlu diperbaiki.")
        return

    # Batasi ke 30 pertama terlebih dahulu atau jalankan semuanya jika memungkinkan secara aman
    # Kita jalankan semuanya secara bertahap
    repaired_count = 0
    
    print("Memulai Playwright browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7"
        )
        page = context.new_page()
        
        # Menerapkan stealth jika tersedia
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except ImportError:
            pass

        for count, (idx, title, url, old_company) in enumerate(to_repair):
            print(f"\n[{count+1}/{len(to_repair)}] Memperbaiki: '{title}' (URL: {url})")
            print(f"Perusahaan lama: '{old_company}'")
            
            try:
                # Bersihkan URL parameter
                clean_url = url.split('?')[0]
                page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                
                soup = BeautifulSoup(page.content(), 'html.parser')
                
                company = None
                company_elem = soup.find(attrs={"data-automation": "advertiser-name"})
                if company_elem:
                    company = company_elem.text.strip()
                else:
                    # Fallback
                    company_links = soup.find_all('a', href=re.compile(r'/companies/|/company/'))
                    for a in company_links:
                        a_text = a.text.strip()
                        if a_text and not any(junk in a_text.lower() for junk in ["selengkapnya tentang", "ulasan", "reviews", "lihat semua", "perusahaan"]):
                            if not re.match(r'^\d+(\.\d+)?$', a_text):
                                company = a_text
                                break
                    
                    if not company:
                        company_elem = soup.find(class_=re.compile(r'CompanyName', re.I))
                        company = company_elem.text.strip() if company_elem else None
                
                if company and company != old_company:
                    print(f"-> BERHASIL DIPERBAIKI: '{old_company}' -> '{company}'")
                    data[idx]['company'] = company
                    repaired_count += 1
                    
                    # Simpan data berkala setiap 5 perbaikan sukses
                    if repaired_count % 5 == 0:
                        with open(filepath, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=4, ensure_ascii=False)
                        print("...Progress disimpan ke file...")
                else:
                    print(f"-> Gagal mengekstrak nama baru atau sama. Hasil: '{company}'")
                    
            except Exception as ex:
                print(f"-> Error saat memproses URL {url}: {ex}")
                # Jeda sejenak sebelum mencoba berikutnya
                time.sleep(2)
                
        browser.close()

    # Simpan hasil akhir
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        
    print(f"\nProses perbaikan selesai. Berhasil memperbaiki {repaired_count} perusahaan.")

if __name__ == "__main__":
    repair_companies()
