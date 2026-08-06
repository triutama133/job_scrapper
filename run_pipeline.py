import subprocess
import sys
import argparse
import time

def run_step(command):
    print("\n" + "=" * 60)
    print(f" Menjalankan: python {' '.join(command)}")
    print("=" * 60)
    
    start_time = time.time()
    # Use sys.executable to run using the current active Python virtual env
    result = subprocess.run([sys.executable] + command)
    duration = time.time() - start_time
    
    if result.returncode != 0:
        print(f"\n[ERROR] Langkah gagal dengan exit code: {result.returncode} (Durasi: {duration:.2f}s)")
        return False
        
    print(f"\n[SUKSES] Langkah selesai dengan sukses (Durasi: {duration:.2f}s)")
    return True

def main():
    parser = argparse.ArgumentParser(description="End-to-End Automated Data Acquisition & ETL Pipeline")
    parser.add_argument("--platform", type=str, default="all", choices=["all", "glints", "kalibrr", "jobstreet", "linkedin"], help="Platform lowongan kerja yang di-scrape")
    parser.add_argument("--max-jobs", type=int, default=10, help="Maksimum lowongan kerja yang di-scrape per platform")
    parser.add_argument("--location", type=str, default="Indonesia", help="Lokasi pencarian lowongan kerja")
    parser.add_argument("--keyword", type=str, default="", help="Kata kunci pencarian lowongan kerja")
    
    # Flags to skip specific phases of the pipeline
    parser.add_argument("--skip-scrape", action="store_true", help="Lewati fase scraping")
    parser.add_argument("--skip-spark", action="store_true", help="Lewati fase transformasi PySpark")
    parser.add_argument("--skip-check", action="store_true", help="Lewati fase link checker")
    parser.add_argument("--skip-upload", action="store_true", help="Lewati fase upload ETL ke Supabase")
    
    args = parser.parse_args()
    
    pipeline_start = time.time()
    print("\n" + "#" * 60)
    print(" MEMULAI PIPELINE AKUISISI DATA & ETL LOWONGAN KERJA")
    print("#" * 60)
    
    # 1. Scraping Phase
    if not args.skip_scrape:
        scrape_cmd = [
            "scraper.py", 
            "--platform", args.platform, 
            "--max-jobs", str(args.max_jobs),
            "--location", args.location,
            "--keyword", args.keyword
        ]
        if not run_step(scrape_cmd):
            print("\n[CRITICAL] Pipeline dihentikan karena fase Scraping gagal.")
            sys.exit(1)
    else:
        print("\n[INFO] Menghindari fase Scraping (--skip-scrape).")

    # 2. PySpark Transformation Phase
    if not args.skip_spark:
        if not run_step(["spark_transform.py"]):
            print("\n[CRITICAL] Pipeline dihentikan karena fase PySpark Transformation gagal.")
            sys.exit(1)
    else:
        print("\n[INFO] Menghindari fase PySpark Transformation (--skip-spark).")

    # 3. Link Checking Phase
    if not args.skip_check:
        if not run_step(["link_checker.py"]):
            print("\n[CRITICAL] Pipeline dihentikan karena fase Link Checker gagal.")
            sys.exit(1)
    else:
        print("\n[INFO] Menghindari fase Link Checker (--skip-check).")

    # 4. Supabase Upload Phase (ETL)
    if not args.skip_upload:
        if not run_step(["upload_to_supabase.py"]):
            print("\n[CRITICAL] Pipeline dihentikan karena fase ETL Supabase gagal.")
            sys.exit(1)
    else:
        print("\n[INFO] Menghindari fase ETL Supabase (--skip-upload).")

    total_duration = time.time() - pipeline_start
    print("\n" + "#" * 60)
    print(f" PIPELINE SELESAI DENGAN SUKSES! (Total Durasi: {total_duration:.2f}s)")
    print("#" * 60 + "\n")

if __name__ == "__main__":
    main()
