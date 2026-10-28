-- Jalankan di Supabase Dashboard -> SQL Editor.
-- Tujuan: job_url harus unik. Tanpa ini, upsert(on_conflict="job_url") gagal (42P10) dan dua proses upload
-- yang berjalan bersamaan bisa menyisipkan lowongan ganda.

-- 1) Cek duplikat dulu. HARUS mengembalikan 0 baris (saat dicek terakhir: 0 duplikat dari ~27 ribu baris).
select job_url, count(*) as n
from public.jobs
group by job_url
having count(*) > 1;

-- 2) Hapus index lama bernama sama bila ada. Index PARTIAL (dengan WHERE) TIDAK dipakai ON CONFLICT (job_url),
--    dan "create ... if not exists" akan melewatinya diam-diam. Constraint penuh di bawah ini yang dibutuhkan.
drop index if exists public.jobs_job_url_key;

-- 3) Constraint UNIQUE penuh (tabel ~27 ribu baris: selesai dalam hitungan detik).
alter table public.jobs add constraint jobs_job_url_key unique (job_url);

-- 4) Verifikasi: harus 1 baris, is_partial = false
select i.indexrelid::regclass as idx, i.indisunique, i.indpred is not null as is_partial
from pg_index i
where i.indrelid = 'public.jobs'::regclass and i.indexrelid::regclass::text = 'jobs_job_url_key';

-- Rollback bila perlu:
-- alter table public.jobs drop constraint if exists jobs_job_url_key;
