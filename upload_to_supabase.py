import os
import json
from dotenv import load_dotenv
from supabase import create_client, Client

def upload_jobs_to_supabase(filepath="jobs_data.json"):
    # Load environment variables from .env
    load_dotenv()
    
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL atau SUPABASE_KEY tidak ditemukan di file .env.")
        return False
        
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} tidak ditemukan. Tidak ada data yang bisa diunggah.")
        return False
        
    with open(filepath, 'r', encoding='utf-8') as f:
        jobs = json.load(f)
        
    if not jobs:
        print("Warning: File data kosong. Tidak ada data untuk diunggah.")
        return True
        
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
            "description": job.get("description", ""),
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
            
    print(f"Mengunggah data dalam batch ke tabel 'jobs'...")
    
    # Chunk uploading into batches of 100 to prevent payload limits
    batch_size = 100
    success_count = 0
    
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            # PostgREST upsert with resolution key on_conflict="job_url"
            response = supabase.table("jobs").upsert(batch, on_conflict="job_url").execute()
            success_count += len(batch)
            print(f"Berhasil mengunggah batch {i // batch_size + 1}: {len(batch)} lowongan.")
        except Exception as e:
            print(f"Gagal mengunggah batch {i // batch_size + 1}: {e}")
            return False
            
    print(f"\nETL Selesai. Total lowongan berhasil di-upsert ke Supabase: {success_count}/{len(records)}")
    return True

if __name__ == "__main__":
    upload_jobs_to_supabase("jobs_data.json")
