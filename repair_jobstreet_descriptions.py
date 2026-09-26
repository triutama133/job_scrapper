import os
import json
import re
import time
import random
import argparse
import shutil
from bs4 import BeautifulSoup
from camoufox.sync_api import Camoufox
from scraper import JobScraper

JUNK_DESC_KEYWORD = "bergabunglah dengan jutaan pencari kerja di seluruh dunia"

def pause_airflow_dag(dag_id):
    """Menonaktifkan (PAUSE) DAG Airflow secara otomatis."""
    print(f"\n[Airflow] Mematikan DAG '{dag_id}' secara otomatis...")
    # Cara 1: Menggunakan Airflow ORM Model
    try:
        from airflow.models import DagModel
        from airflow.settings import Session
        session = Session()
        dag_model = session.query(DagModel).filter(DagModel.dag_id == dag_id).first()
        if dag_model:
            dag_model.set_is_paused(is_paused=True)
            session.commit()
            session.close()
            print(f"[Airflow] Berhasil me-nonaktifkan DAG '{dag_id}' via ORM Session.")
            return True
        session.close()
    except Exception as e:
        print(f"[Airflow] Peringatan: Gagal mem-pause DAG via ORM ({e}), mencoba CLI...")

    # Cara 2: Menggunakan Airflow CLI sebagai fallback
    try:
        import subprocess
        result = subprocess.run(["airflow", "dags", "pause", dag_id], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[Airflow] Berhasil me-nonaktifkan DAG '{dag_id}' via Airflow CLI.")
            return True
        else:
            print(f"[Airflow] CLI returned non-zero: {result.stderr.strip()}")
    except Exception as ce:
        print(f"[Airflow] Peringatan: Gagal mem-pause DAG via CLI: {ce}")

    return False

def count_corrupted_jobs(data, auto_clean_inactive=True):
    """Menghitung jumlah data lowongan Jobstreet yang deskripsinya masih boilerplate."""
    corrupted_indices = []
    cleaned_inactive = 0
    for idx, job in enumerate(data):
        if job.get('platform') == 'Jobstreet':
            desc = job.get('description', '')
            has_junk = JUNK_DESC_KEYWORD in desc.lower() or not desc.strip()
            
            # Jika lowongan sudah ditandai inactive/expired, bersihkan teks boilerplatenya langsung tanpa perlu request ulang
            if job.get('is_active') is False:
                if has_junk and auto_clean_inactive:
                    job["description"] = "Lowongan kerja ini sudah ditutup/tidak lagi diiklankan di Jobstreet."
                    job["job_desk"] = []
                    job["requirements"] = []
                    cleaned_inactive += 1
                continue
                
            if has_junk:
                corrupted_indices.append(idx)
                
    if cleaned_inactive > 0:
        print(f"[CLEANUP] Otomatis merapikan {cleaned_inactive} lowongan Jobstreet yang sudah inactive.")
        
    return corrupted_indices

def repair_descriptions(filepath="jobs_data.json", limit=50, save_interval=10, 
                        delay_min=2.0, delay_max=4.0, headless=True, auto_pause_dag=None):
    if not os.path.exists(filepath):
        print(f"Error: {filepath} tidak ditemukan.")
        return

    # 1. Backup file sebelum modifikasi jika belum ada backup
    backup_path = f"{filepath}.bak_before_desc_repair"
    if not os.path.exists(backup_path):
        print(f"Membuat backup database ke {backup_path}...")
        shutil.copy2(filepath, backup_path)

    print(f"Membaca data dari {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 2. Periksa sisa data yang perlu diperbaiki (sekaligus bersihkan data yang sudah inactive)
    to_repair = count_corrupted_jobs(data, auto_clean_inactive=True)
    total_remaining = len(to_repair)
    print(f"Ditemukan {total_remaining} lowongan Jobstreet aktif dengan deskripsi boilerplate/rusak.")

    if total_remaining == 0:
        print("\n" + "=" * 60)
        print("[SELESAI] Tidak ada lagi data Jobstreet yang perlu diperbaiki!")
        print("=" * 60)
        # Simpan perubahan jika ada pembersihan inactive
        temp_file = f"{filepath}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, filepath)
        if auto_pause_dag:
            pause_airflow_dag(auto_pause_dag)
        return

    # Batasi batch untuk menjaga keamanan dari blocking IP
    batch_indices = to_repair[:limit] if limit and limit > 0 else to_repair
    print(f"Batch kali ini akan memproses {len(batch_indices)} lowongan...")

    scraper_helper = JobScraper(headless=headless, filepath=filepath)
    repaired_count = 0
    expired_count = 0
    error_count = 0

    print("Memulai Camoufox stealth browser (C++ Fingerprint Spoofing)...")
    with Camoufox(headless=headless) as browser:
        page = browser.new_page()

        for step, idx in enumerate(batch_indices, 1):
            job = data[idx]
            url = job.get('job_url', '').split('?')[0]
            title = job.get('job_title', 'Unknown')
            company = job.get('company', 'Unknown')

            print(f"\n[{step}/{len(batch_indices)}] Memeriksa: {title} @ {company}")
            print(f"URL: {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                soup = BeautifulSoup(page.content(), 'html.parser')

                # Cek apakah lowongan sudah kedaluwarsa / tutup
                is_expired = (
                    soup.find(attrs={"data-automation": "expiredJobPage"}) is not None
                    or "tidak lagi diiklankan" in soup.text.lower()[:800]
                )

                if is_expired:
                    print("-> [EXPIRED] Lowongan sudah ditutup/tidak lagi diiklankan.")
                    job["is_active"] = False
                    job["status_reason"] = "Job Expired (tidak lagi diiklankan)"
                    job["description"] = "Lowongan kerja ini sudah ditutup/tidak lagi diiklankan di Jobstreet."
                    job["job_desk"] = []
                    job["requirements"] = []
                    expired_count += 1
                else:
                    # Ambil deskripsi asli dari jobAdDetails
                    desc_elem = (
                        soup.find(attrs={"data-automation": "jobAdDetails"})
                        or soup.find(attrs={"data-automation": re.compile(r"^job-?ad-?details$", re.I)})
                    )

                    # Fallback jika kontainer utama tidak ditemukan
                    if not desc_elem:
                        for cand in soup.find_all(class_=re.compile(r'\b(?:job[-_]?description|jobDetails)\b', re.I)):
                            cand_classes = " ".join(cand.get("class", []))
                            if "lmis" not in cand_classes.lower():
                                desc_elem = cand
                                break

                    raw_desc = desc_elem.get_text(separator='\n').strip() if desc_elem else ""

                    if raw_desc and JUNK_DESC_KEYWORD not in raw_desc.lower():
                        cleaned_desc = scraper_helper.clean_description(raw_desc)
                        h_resp, h_skills = scraper_helper.extract_skills_and_responsibilities(cleaned_desc)

                        final_job_desk = h_resp if h_resp else [
                            l.strip() for l in cleaned_desc.split('\n') if l.strip() and len(l.strip()) > 15
                        ]

                        job["description"] = cleaned_desc
                        job["job_desk"] = final_job_desk
                        job["requirements"] = h_skills
                        job["is_active"] = True
                        job["status_reason"] = "Active"

                        # Perbarui title & company jika sebelumnya Unknown
                        if job.get("job_title") in ["Unknown", "Unknown Title"]:
                            t_elem = soup.find(attrs={"data-automation": "job-detail-title"}) or soup.find('h1')
                            if t_elem:
                                job["job_title"] = scraper_helper.clean_text(t_elem.text)

                        if job.get("company") in ["Unknown", "Unknown Company"]:
                            c_elem = soup.find(attrs={"data-automation": "advertiser-name"})
                            if c_elem:
                                job["company"] = scraper_helper.clean_text(c_elem.text)

                        # Ekstraksi salary jika belum ada
                        if not job.get("salary"):
                            sal = scraper_helper.extract_salary(soup, "Jobstreet")
                            if sal:
                                job["salary"] = scraper_helper.clean_salary_text(sal)

                        repaired_count += 1
                        print(f"-> [SUCCESS] Berhasil! Karakter: {len(cleaned_desc)}, Job Desk: {len(final_job_desk)}, Syarat: {len(h_skills)}")
                    else:
                        print("-> [WARNING] Gagal mengekstrak deskripsi baru atau teks masih boilerplate.")
                        error_count += 1

            except Exception as e:
                print(f"-> [ERROR] Gagal memproses URL: {e}")
                error_count += 1

            # Simpan data secara berkala dengan atomic file write
            if step % save_interval == 0 or step == len(batch_indices):
                temp_file = f"{filepath}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                os.replace(temp_file, filepath)
                print(f"[PROGRESS] Disimpan ke {filepath} ({step}/{len(batch_indices)}).")

            # Randomized delay antar request untuk mencegah rate limiting / IP block
            if step < len(batch_indices):
                jitter = random.uniform(delay_min, delay_max)
                time.sleep(jitter)

    # 3. Hitung ulang sisa pekerjaan setelah batch ini
    final_remaining = len(count_corrupted_jobs(data))

    print("\n" + "=" * 60)
    print("HASIL PERBAIKAN BATCH INI")
    print("=" * 60)
    print(f"Batch diproses    : {len(batch_indices)}")
    print(f"Berhasil diisi    : {repaired_count}")
    print(f"Expired/Closed    : {expired_count}")
    print(f"Gagal/Skip        : {error_count}")
    print(f"Sisa data corrupt : {final_remaining}")
    print("=" * 60)

    # Jika semua data sampah sudah habis, otomatis matikan DAG scheduler
    if final_remaining == 0:
        print("\n🎉 [ALL DONE] Semua data lowongan Jobstreet telah 100% diperbaiki!")
        if auto_pause_dag:
            pause_airflow_dag(auto_pause_dag)
    else:
        print(f"\n[INFO] Masih ada {final_remaining} lowongan tersisa. Akan dilanjutkan pada jadwal berikutnya.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Perbaiki deskripsi lowongan Jobstreet secara bertahap & aman.")
    parser.add_argument("--batch-size", "--limit", type=int, default=50, help="Jumlah lowongan per batch (default: 50)")
    parser.add_argument("--file", type=str, default="jobs_data.json", help="Path ke jobs_data.json")
    parser.add_argument("--interval", type=int, default=10, help="Interval penyimpanan progress")
    parser.add_argument("--delay-min", type=float, default=2.0, help="Minimum jeda detik antar URL (default: 2.0s)")
    parser.add_argument("--delay-max", type=float, default=4.0, help="Maksimum jeda detik antar URL (default: 4.0s)")
    parser.add_argument("--auto-pause-dag", type=str, default=None, help="Nama DAG Airflow yang otomatis di-PAUSE jika semua data selesai")
    args = parser.parse_args()

    repair_descriptions(
        filepath=args.file,
        limit=args.batch_size,
        save_interval=args.interval,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        auto_pause_dag=args.auto_pause_dag
    )
