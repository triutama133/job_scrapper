import os
import json
import fcntl
import hashlib
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

import argparse

JUNK_DESC_KEYWORD = "bergabunglah dengan jutaan pencari kerja di seluruh dunia"

# Lowongan nonaktif tidak ditampilkan di web app; job_desk/requirements tetap lengkap untuk bahan belajar LLM
# (fallback LLM hanya memakai 300 karakter pertama deskripsi). Teks penuh tetap di jobs_data.json (Pi)
# agar Supabase free plan (500 MB) tidak penuh.
INACTIVE_DESC_CHARS = 300

# Sinkron inkremental: hash record terakhir yang berhasil terkirim per job_url. Upsert ulang seluruh file tiap run
# menulis ulang ~33 ribu baris (bloat tabel + 3 menit); sekarang hanya baris baru/berubah yang dikirim.
STATE_FILE = "supabase_sync_state.json"
FULL_SYNC_EVERY_DAYS = 7   # kirim ulang semuanya secara berkala agar penyimpangan (edit manual di Supabase) terkoreksi


def supabase_description(job):
    desc = job.get("description") or ""
    if job.get("is_active") is False:
        return desc[:INACTIVE_DESC_CHARS]
    return desc


def record_hash(rec):
    """Hash isi record yang dikirim. last_checked dipotong ke tanggal: pengecekan ulang di hari yang sama
    tidak memicu pengiriman ulang ribuan baris."""
    r = dict(rec)
    if r.get("last_checked"):
        r["last_checked"] = str(r["last_checked"])[:10]
    return hashlib.md5(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("hashes"), dict):
        return None
    return state


def save_state(path, sent, full_done):
    """Gabungkan hash yang berhasil terkirim ke state TERBARU di bawah lock, tulis atomik.
    Dua DAG bisa mengunggah bersamaan: tanpa re-read, yang selesai belakangan menimpa catatan yang lain."""
    if not sent and not full_done:
        return
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state(path) or {"version": 1, "hashes": {}, "last_full_sync": None}
        state["hashes"].update(sent)
        if full_done:
            state["last_full_sync"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)


def _full_sync_due(state):
    if state is None or not state.get("last_full_sync"):
        return True
    try:
        last = datetime.fromisoformat(state["last_full_sync"])
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last > timedelta(days=FULL_SYNC_EVERY_DAYS)


def _upload_records(supabase, records, batch_size, repaired_only, mark_sent):
    """Kirim record dalam batch. mark_sent(batch) dipanggil HANYA setelah batch berhasil. Return (ok, success_count)."""
    success_count = 0
    use_fallback = False

    # Tes batch pertama menggunakan PostgREST native upsert
    first_batch = records[:batch_size]
    try:
        supabase.table("jobs").upsert(first_batch, on_conflict="job_url").execute()
        success_count += len(first_batch)
        mark_sent(first_batch)
        print(f"Berhasil mengunggah batch 1 (native upsert): {len(first_batch)} lowongan.")
    except Exception as e:
        err_msg = str(e)
        if "42P10" in err_msg or "ON CONFLICT" in err_msg or "unique or exclusion constraint" in err_msg:
            print("\n[INFO] Tabel 'jobs' di Supabase belum memiliki UNIQUE constraint pada kolom 'job_url'.")
            print("[INFO] Mengaktifkan mode sinkronisasi cerdas (Insert New + Update Existing)...")
            use_fallback = True
        else:
            print(f"Gagal mengunggah batch 1: {e}")
            return False, success_count

    if not use_fallback:
        for i in range(batch_size, len(records), batch_size):
            batch = records[i:i + batch_size]
            try:
                supabase.table("jobs").upsert(batch, on_conflict="job_url").execute()
                success_count += len(batch)
                mark_sent(batch)
                print(f"Berhasil mengunggah batch {i // batch_size + 1}: {len(batch)} lowongan.")
            except Exception as e:
                print(f"Gagal mengunggah batch {i // batch_size + 1}: {e}")
                return False, success_count
    else:
        # Mode Fallback: Ambil URL & ID yang sudah ada di Supabase untuk memisahkan data baru vs data lama
        print("Mengambil pemetaan job_url & id yang sudah ada di Supabase...")
        url_to_id = {}
        last_id = -1
        while True:
            # keyset (id > last ORDER BY id): .range() tanpa ORDER BY bisa melewatkan baris saat tabel sedang ditulis
            res = supabase.table("jobs").select("id, job_url").gt("id", last_id).order("id").limit(1000).execute()
            if not res.data:
                break
            for r in res.data:
                u = r.get("job_url")
                if u:
                    url_to_id[u] = r.get("id")
            last_id = res.data[-1]["id"]
        print(f"Total URL terdaftar di Supabase: {len(url_to_id)}")

        new_records = [r for r in records if r.get("job_url") not in url_to_id]
        existing_records = [r for r in records if r.get("job_url") in url_to_id]

        print(f"Lowongan BARU yang akan di-insert : {len(new_records)}")
        print(f"Lowongan SUDAH ADA di database     : {len(existing_records)}")

        # 1. Insert data baru dalam batch
        if new_records:
            print(f"Mengunggah {len(new_records)} lowongan baru...")
            for i in range(0, len(new_records), batch_size):
                batch = new_records[i:i + batch_size]
                try:
                    supabase.table("jobs").insert(batch).execute()
                    success_count += len(batch)
                    mark_sent(batch)
                    print(f"Berhasil insert batch {i // batch_size + 1}: {len(batch)} lowongan baru.")
                except Exception as ie:
                    print(f"Gagal insert batch {i // batch_size + 1}: {ie}")
                    return False, success_count

        # 2. Update data lama jika mode repaired_only atau sync spesifik menggunakan batch upsert pada primary key 'id'
        if repaired_only and existing_records:
            print(f"Memperbarui {len(existing_records)} lowongan di Supabase via batch upsert (on_conflict='id')...")
            records_with_id = []
            for r in existing_records:
                rec_copy = dict(r)
                rec_copy["id"] = url_to_id[r["job_url"]]
                records_with_id.append(rec_copy)

            for i in range(0, len(records_with_id), batch_size):
                batch = records_with_id[i:i + batch_size]
                try:
                    supabase.table("jobs").upsert(batch, on_conflict="id").execute()
                    success_count += len(batch)
                    mark_sent(batch)
                    print(f"Berhasil update batch {i // batch_size + 1}: {len(batch)} lowongan.")
                except Exception as ue:
                    print(f"Gagal update batch {i // batch_size + 1}: {ue}")
                    return False, success_count

    return True, success_count


def upload_jobs_to_supabase(filepath="jobs_data.json", records_to_upload=None, repaired_only=False, urls=None,
                            batch_size=100, full=False, state_file=STATE_FILE):
    # Load environment variables from .env
    load_dotenv()

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL atau SUPABASE_KEY tidak ditemukan di file .env.")
        return False

    if records_to_upload is not None:
        raw_jobs = records_to_upload
    else:
        if not os.path.exists(filepath):
            print(f"Error: File {filepath} tidak ditemukan. Tidak ada data yang bisa diunggah.")
            return False

        with open(filepath, 'r', encoding='utf-8') as f:
            raw_jobs = json.load(f)

    if not raw_jobs:
        print("Warning: File data kosong. Tidak ada data untuk diunggah.")
        return True

    # Filter jika diperlukan
    jobs = []
    if urls:
        url_set = set(urls)
        jobs = [j for j in raw_jobs if j.get("job_url") in url_set]
    elif repaired_only:
        for j in raw_jobs:
            if j.get("platform") == "Jobstreet":
                desc = j.get("description", "")
                has_junk = JUNK_DESC_KEYWORD in desc.lower() or not desc.strip()
                # Sertakan lowongan aktif yang sudah bersih atau lowongan yang statusnya ditutup
                if not has_junk or j.get("is_active") is False:
                    jobs.append(j)
    else:
        jobs = raw_jobs

    print(f"Menghubungkan ke Supabase di: {supabase_url}")
    supabase: Client = create_client(supabase_url, supabase_key)

    print(f"Mempersiapkan {len(jobs)} entri lowongan kerja untuk di-upload...")

    # Format data for database schema
    records = []
    for job in jobs:
        record = {
            "job_title": job.get("job_title", "Unknown Title"),
            "company": job.get("company", "Unknown Company"),
            "platform": job.get("platform", "Unknown Platform"),
            "job_url": job.get("job_url"),
            "description": supabase_description(job),
            "job_desk": job.get("job_desk", []),       # Lists will automatically format to JSONB array
            "requirements": job.get("requirements", []),
            "is_active": job.get("is_active", True),
            "status_reason": job.get("status_reason", "Active"),
            "category": job.get("category", "Others / General"),
            "salary": job.get("salary")
        }

        # Format timestamp to ISO string if exists
        last_checked = job.get("last_checked")
        if last_checked:
            record["last_checked"] = last_checked

        if record["job_url"]:
            records.append(record)

    # Mode seluruh file (tanpa urls/records/repaired_only) = sinkron inkremental; mode lain mengirim persis yang diminta.
    whole_file = records_to_upload is None and not urls and not repaired_only
    hashes = {r["job_url"]: record_hash(r) for r in records}
    full_done = False
    if whole_file:
        state = load_state(state_file)
        if full or _full_sync_due(state):
            print("Sinkron PENUH" + (" (diminta)" if full else " (belum ada state / sudah lewat jadwal berkala)") + f": {len(records)} baris.")
            full_done = True
        else:
            known = state["hashes"]
            before = len(records)
            records = [r for r in records if known.get(r["job_url"]) != hashes[r["job_url"]]]
            print(f"Sinkron INKREMENTAL: {len(records)} baru/berubah dari {before} (sisanya sama dengan yang terakhir terkirim).")

    if not records:
        print("Tidak ada record yang perlu di-upload.")
        if full_done:
            save_state(state_file, {}, True)
        return True

    print(f"Mengunggah data dalam batch ke tabel 'jobs'...")

    sent = {}

    def mark_sent(batch):
        for r in batch:
            sent[r["job_url"]] = hashes[r["job_url"]]

    ok, success_count = False, 0
    try:
        ok, success_count = _upload_records(supabase, records, batch_size, repaired_only, mark_sent)
    finally:
        # Catat batch yang berhasil walau run gagal di tengah: run berikutnya hanya mengulang sisanya
        save_state(state_file, sent, full_done and ok)

    if not ok:
        return False
    print(f"\nETL Selesai. Total lowongan berhasil diproses ke Supabase: {success_count}/{len(records)}")
    return True

if __name__ == "__main__":
    import sys
    parser = argparse.ArgumentParser(description="Upload atau update lowongan kerja ke database Supabase.")
    parser.add_argument("--file", type=str, default="jobs_data.json", help="Path ke file jobs_data.json")
    parser.add_argument("--repaired-only", action="store_true", help="Hanya upload data Jobstreet yang sudah bersih/diperbaiki")
    parser.add_argument("--batch-size", type=int, default=100, help="Ukuran batch upsert (default: 100)")
    parser.add_argument("--full", action="store_true", help="Kirim ulang SEMUA baris (abaikan state inkremental)")
    args = parser.parse_args()

    success = upload_jobs_to_supabase(
        filepath=args.file,
        repaired_only=args.repaired_only,
        batch_size=args.batch_size,
        full=args.full
    )
    if not success:
        sys.exit(1)
