"""
Data quality gate (dijalankan di akhir DAG). Exit code 1 bila ada pemeriksaan FAIL sehingga Airflow
menandai task gagal, bukan 'sukses' palsu. Hanya membaca data (tidak menulis apa pun).

Pemeriksaan:
  1. Integritas file      : JSON valid, tidak ada job_url ganda, field wajib terisi
  2. Salary mencurigakan  : nilai identik berulang di banyak perusahaan berbeda (placeholder halaman),
                            nilai 0, dan deret digit menyatu (fused) -> pola bug sebelumnya
  3. Sinkron Supabase     : lowongan lokal yang belum ada di Supabase, salary lokal yang belum tersalin
"""
import os
import re
import sys
import json
import argparse
from collections import defaultdict
from dotenv import load_dotenv

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(PROJECT_PATH, "jobs_data.json")

# Batas
REPEAT_MIN_COUNT = 8        # nilai salary identik sebanyak ini ...
REPEAT_MIN_COMPANIES = 5    # ... pada sebanyak ini perusahaan berbeda = mencurigakan
SALARY_GAP_FAIL = 100       # salary lokal belum ada di Supabase
MISSING_JOBS_FAIL = 1500    # lowongan lokal belum ada di Supabase (wajar sementara antar upload)

results = []


def record(level, name, detail):
    results.append((level, name, detail))
    print(f"[{level:4}] {name}: {detail}")


def check_file(jobs):
    urls = [j.get("job_url") for j in jobs]
    dupes = len(urls) - len(set(urls))
    record("FAIL" if dupes else "OK", "duplikat job_url (lokal)", str(dupes))
    bad = [j for j in jobs if not (j.get("job_url") and j.get("job_title") and j.get("company"))]
    record("FAIL" if len(bad) > 20 else ("WARN" if bad else "OK"), "field wajib kosong", str(len(bad)))


def check_salaries(jobs):
    groups = defaultdict(list)
    zero = fused = 0
    for j in jobs:
        s = j.get("salary")
        if not s:
            continue
        if re.search(r"\d{10,}", s):   # digit menyatu tanpa pemisah ribuan (bug lama: IDR230000033000002300000)
            fused += 1
        if re.fullmatch(r"\D*0+\D*(per \w+)?", s.strip()) or "USD 0 " in s or s.strip() in ("$0", "$0.00"):
            zero += 1
        # Portal Indonesia punya nilai umum yang wajar berulang (mis. Rp 5.000.000); fokus pada sumber remote
        if j.get("source") == "remote_scraper":
            groups[(j.get("platform"), s)].append(j)
    suspicious = []
    for (platform, s), js in groups.items():
        companies = {(j.get("company") or "").lower() for j in js}
        if len(js) >= REPEAT_MIN_COUNT and len(companies) >= REPEAT_MIN_COMPANIES:
            suspicious.append(f"{platform}: '{s}' x{len(js)} ({len(companies)} perusahaan)")
    record("FAIL" if suspicious else "OK", "salary identik berulang (sumber remote)", "; ".join(suspicious) or "tidak ada")
    record("FAIL" if zero else "OK", "salary bernilai 0", str(zero))
    record("WARN" if fused else "OK", "salary digit menyatu", str(fused))


def check_supabase(jobs):
    load_dotenv(os.path.join(PROJECT_PATH, ".env"))
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        record("WARN", "sinkron Supabase", "kredensial tidak ada, dilewati")
        return
    try:
        from supabase import create_client
        sb = create_client(url, key)
        # Paginasi keyset (id > last ORDER BY id), BUKAN .range() tanpa ORDER BY: tanpa urutan, halaman bisa saling
        # tumpang tindih saat baris sedang ditulis ulang (upload meng-upsert seluruh tabel) -> "duplikat" & "hilang" palsu.
        rows, last = [], -1
        while True:
            chunk = sb.table("jobs").select("id,job_url,salary").gt("id", last).order("id").limit(1000).execute().data
            if not chunk:
                break
            rows += chunk
            last = chunk[-1]["id"]
    except Exception as e:
        record("FAIL", "sinkron Supabase", f"tidak bisa membaca Supabase: {e}")
        return
    sup = {r["job_url"]: r for r in rows}
    missing = sum(1 for j in jobs if j.get("job_url") not in sup)
    gap = sum(1 for j in jobs if j.get("salary") and j.get("job_url") in sup and not sup[j["job_url"]]["salary"])
    record("FAIL" if missing > MISSING_JOBS_FAIL else ("WARN" if missing else "OK"),
           "lowongan lokal belum ada di Supabase", f"{missing} (Supabase {len(rows)} baris, lokal {len(jobs)})")
    record("FAIL" if gap > SALARY_GAP_FAIL else ("WARN" if gap else "OK"),
           "salary lokal belum tersalin ke Supabase", str(gap))
    record("FAIL" if len(rows) - len(sup) else "OK", "duplikat job_url (Supabase)", str(len(rows) - len(sup)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-supabase", action="store_true")
    args = parser.parse_args()
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            jobs = json.load(f)
    except Exception as e:
        record("FAIL", "baca jobs_data.json", str(e))
        return 1
    check_file(jobs)
    check_salaries(jobs)
    if not args.skip_supabase:
        check_supabase(jobs)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\nHasil: {len(results) - len(fails)} lolos/peringatan, {len(fails)} GAGAL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
