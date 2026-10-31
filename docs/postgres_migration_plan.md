# Rencana: Postgres lokal sebagai sumber kebenaran, Supabase sebagai salinan hilir

Status: **draft untuk di-review, belum ada yang dijalankan.** Tanggal: 2026-10-31.

## Tujuan
1. Postgres 16 di Pi (sudah terpasang, disk bebas 147 GB) menjadi sumber kebenaran; `jobs_data.json` berhenti menjadi database.
2. Supabase (free, batas 500 MB) menjadi salinan hilir yang kecil untuk web app `talenthub`.
3. Sinkron ke Supabase **inkremental** (hanya baris baru/berubah), bukan upsert 32 ribu baris tiap run.
4. Riwayat lowongan tersimpan: kapan pertama terlihat, terakhir terlihat, kapan ditutup, perubahan gaji.

## Kondisi saat ini (terukur)
- 32.173 baris lokal; Supabase 32.226 baris (53 baris hanya ada di Supabase).
- Tabel `jobs` di Supabase: 196 MB (heap 57 MB, index 10 MB, sisanya ~129 MB teks panjang). DB 213 MB, batas 500 MB.
- Pertumbuhan sekitar 2.200 lowongan baru/hari (~13 MB/hari) -> penuh dalam ~3 minggu tanpa tindakan.
- Web app hanya membaca baris `is_active = true` (`talenthub/app/api/jobs/route.ts`, `alumni_RESTAPI/main.py` learning-path).
- Langkah 0 (sudah dikerjakan): baris nonaktif di Supabase memakai 300 karakter pertama `description`; teks penuh tetap di Pi. Backup: `jobs_inactive_full.json.bak_before_truncate`.

## Skema lokal (database `jobs_dw`, role `scraper` terpisah)
```
jobs            -- satu baris per job_url (UNIQUE), kolom sama seperti sekarang +
                   first_seen_at, last_seen_at, closed_at, content_hash, updated_at
job_changes     -- riwayat perubahan is_active / salary (job_id, changed_at, field, old, new)
scrape_runs     -- metrik per run: source, started_at, finished_at, rows_seen, rows_new, rows_changed, status
sync_state      -- target ('supabase'), last_updated_at (watermark sinkron)
```
- `updated_at` hanya berubah jika `content_hash` berubah, sehingga sinkron cukup `WHERE updated_at > watermark`.
- Proyeksi ke Supabase tetap seperti sekarang: lowongan nonaktif -> `description` 300 karakter; `job_desk` dan `requirements` selalu lengkap.
- Password role disimpan di `.env` (tidak di-commit, tidak dicetak). Tidak memakai `SUDO_PASS`.

## Fase (tiap fase bisa dibatalkan, pipeline berjalan normal di antaranya)
| # | Fase | Verifikasi | Rollback |
|---|------|-----------|----------|
| 1 | Buat database/role/skema; impor `jobs_data.json` **dan 53 baris yang hanya ada di Supabase**, sehingga Pi menjadi superset | jumlah baris, jumlah per platform, checksum per kolom cocok | drop database |
| 2 | **Dual-write**: scraper menulis ke JSON (seperti sekarang) dan ke Postgres di dalam lock yang sama | DQ check membandingkan file vs DB selama beberapa run, selisih harus 0 | matikan flag dual-write |
| 3 | Pindah pembacaan: dedup scraper, `data_quality_check`, `salary_refetch`, link checker membaca Postgres; kategori di `spark_transform` dipindah ke SQL (regex Postgres) atau dbt | hasil kategori identik dengan Spark pada sampel penuh | kembalikan kode, JSON masih terjaga |
| 4 | **Sinkron inkremental ke Supabase** memakai watermark `updated_at`; hentikan upsert penuh | DQ gate (0 duplikat, 0 hilang) lulus; run upload turun dari ~3 menit ke detik | jalankan `upload_to_supabase.py` penuh seperti sekarang |
| 5 | JSON hanya menjadi ekspor/backup berkala; Spark dilepas dari jalur utama | 1 minggu run tanpa selisih | hidupkan kembali ekspor |
| 6 | (Lanjutan) skema bintang + dbt + tes; tabel agregat tren untuk LLM | tes dbt lulus | - |

## Menjaga Supabase di bawah 500 MB
- Fase 4 menghentikan penulisan ulang massal (mengurangi baris mati).
- Tambahkan fungsi SQL `public.db_size_mb()` di Supabase agar DQ gate memberi peringatan di 80% (400 MB) dan gagal di 95%. Fungsi dibuat sekali oleh pemilik project.
- Jika tabel aktif sendiri mendekati batas: (a) agregat tren di tabel kecil, (b) pangkas teks duplikat (`description` mengulang 94% isi `job_desk`/`requirements`) setelah web app disesuaikan, atau (c) Pro plan.
- Arsip lowongan nonaktif penuh tetap di Pi; bila LLM kelak memerlukan "belajar dari lowongan lama", dilayani dari tabel agregat (mis. frekuensi persyaratan per bulan dan kategori), bukan baris mentah.

## Risiko dan mitigasi
- Dedup scraper saat ini membaca JSON -> pindah ke lookup DB di fase 3 (dual-write dulu agar bisa dibandingkan).
- Beban: Pi 16 GB RAM, Airflow + Spark + Postgres. Spark di fase 5 dilepas sehingga memori lebih longgar.
- Data tidak boleh hilang: backup `pg_dump` sebelum tiap fase, simpan JSON sampai fase 5 selesai.
- Pekerjaan lain terhenti jika DB lokal mati: aktifkan backup `pg_dump` harian dan cek `systemctl` di DQ.

## Keputusan yang dibutuhkan
1. Nama database/role (usulan: `jobs_dw` / `scraper`).
2. Setuju impor 53 baris yang hanya ada di Supabase ke Pi (usulan: ya, lalu periksa `is_active`).
3. Spark: dilepas di fase 5 (usulan) atau dipertahankan.
4. Apakah fungsi `db_size_mb()` boleh dibuat di Supabase (SQL dijalankan Anda di editor).
