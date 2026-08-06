# Job Scraper Pipeline

Automated job scraping pipeline for Indonesian job portals (Glints, Kalibrr, Jobstreet, LinkedIn), with salary extraction, incremental salary refetch, and Supabase sync.

## Fitur Utama

- **Multi-platform scraping**: Glints, Kalibrr, Jobstreet, LinkedIn
- **Salary extraction**: 
  - Langsung dari elemen halaman portal (mis. `data-automation="job-detail-salary"` untuk Jobstreet)
  - Fallback heuristik regex dari deskripsi untuk lowongan lama
  - Mendukung `Rp`, `Rp.`, `IDR`, `SGD`, `USD`, `EUR`, `AUD`, `$` + rentang gaji + satuan `juta/jt`
- **Incremental salary refetch** (`salary_refetch.py`):
  - Scrap ulang salary lowongan existing dari halaman yang masih aktif
  - Rate-limit per platform agar tidak kena block IP
  - Checkpoint progress (`salary_refetch_progress.json`) — lanjut dari URL yang belum diproses
  - Fallback ke deskripsi jika link kadaluarsa
  - Upsert batch langsung ke Supabase
- **ETL**: PySpark transform + upload Supabase (batch 100, upsert on `job_url`)
- **Airflow DAGs**: scraping terjadwal, link checker, dan salary refetch harian

## Struktur Repository

```
job-scrapper/
├── scraper.py                       # Scraper utama (4 platform)
├── upload_to_supabase.py            # ETL: upload jobs_data.json ke Supabase
├── salary_refetch.py                # Incremental salary refetch (batch, rate-limit)
├── link_checker.py                  # Cek status link lowongan
├── spark_transform.py               # Cleanse data dengan PySpark
├── run_pipeline.py                  # Orchestrator pipeline lokal
├── run.sh                           # Script run cepat
├── repair_jobstreet_companies.py    # Perbaikan nama perusahaan Jobstreet
├── requirements.txt                 # Dependencies Python
├── implementation_plan_scrapper.md  # Rencana implementasi & pengayaan salary
├── dags/                            # Airflow DAGs
│   ├── job_scraper_pipeline.py          # Scrape + ETL (setiap 4 jam)
│   ├── job_link_checker_pipeline.py     # Cek link (harian)
│   └── job_salary_refetch_pipeline.py   # Refetch salary (harian 02:00)
└── scratch/                         # Utilitas & debugging
    ├── extract_salaries_from_desc.py   # Backfill salary dari deskripsi
    ├── test_salary_regex.py            # Unit test regex salary
    ├── add_salary_column.py            # Migrasi kolom salary
    └── ...
```

## Instalasi

```bash
# 1. Buat virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Setup environment
cp .env.example .env
# Isi SUPABASE_URL & SUPABASE_KEY (Supabase Dashboard -> Project Settings -> API)
```

> ⚠️ **JANGAN** commit `.env` — sudah diabaikan oleh `.gitignore`.

## Cara Pakai

### 1. Scraping Lowongan Baru

```bash
# Semua platform, 5 jobs per platform
python scraper.py --platform all --max-jobs 5

# Platform spesifik dengan keyword
python scraper.py --platform glints --max-jobs 10 --keyword "Data Analyst"
```

Hasil disimpan ke `jobs_data.json` (otomatis merge, tanpa duplikat).

### 2. Upload ke Supabase

```bash
python upload_to_supabase.py
```

Upsert ke tabel `jobs` dengan conflict key `job_url`.

### 3. Backfill Salary dari Deskripsi (sekali jalan)

Untuk lowongan lama yang belum punya kolom salary:

```bash
python scratch/extract_salaries_from_desc.py
```

### 4. Incremental Salary Refetch (disarankan via Airflow)

Scrap ulang salary dari halaman portal yang masih aktif, batasi per run agar tidak kena block IP:

```bash
# Proses 250 URL per run
python salary_refetch.py --batch-size 250

# Mulai ulang progress dari nol
python salary_refetch.py --batch-size 250 --reset
```

Pipeline ini:
- Ambil salary **langsung dari halaman portal** untuk link aktif
- Fallback ke **deskripsi** saat link kadaluarsa
- Rate-limit per platform (Glints 3s, Kalibrr 2s, Jobstreet 4s, LinkedIn 8s)
- Simpan checkpoint → run berikutnya lanjut, tidak mengulang URL yang sudah dicoba
- Upsert ke Supabase setiap batch selesai

### 5. Airflow (Produksi)

Letakkan folder `dags/` ke instalasi Airflow Anda. DAG yang tersedia:

| DAG | Jadwal | Fungsi |
|-----|--------|--------|
| `job_scraper_and_etl` | Setiap 4 jam | Scrape baru + transform + upload |
| `job_link_checker_pipeline` | Harian | Cek status link lowongan |
| `job_salary_refetch_pipeline` | Harian 02:00 | Refetch salary batch 250 URL |

## Kolom Database (`jobs` table)

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `job_title` | text | Judul lowongan |
| `company` | text | Nama perusahaan |
| `platform` | text | glints / kalibrr / jobstreet / linkedin |
| `job_url` | text (unique) | URL lowongan |
| `description` | text | Deskripsi lengkap |
| `job_desk` | jsonb | Tugas & tanggung jawab |
| `requirements` | jsonb | Persyaratan / skill |
| `salary` | text | Info gaji (nullable) |
| `category` | text | Kategori lowongan |
| `is_active` | boolean | Status aktif |
| `status_reason` | text | Alasan status |
| `last_checked` | timestamptz | Waktu terakhir dicek |

## Lisensi

Lihat file LICENSE (jika ada) — proyek internal / privat.