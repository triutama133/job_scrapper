import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("ERROR: SUPABASE_URL / SUPABASE_KEY tidak ditemukan")
    exit(1)

supabase = create_client(supabase_url, supabase_key)

# Count jobs with non-null salary
resp = supabase.table("jobs").select("id", count="exact").not_.is_("salary", "null").execute()
print(f"Jobs dengan salary (non-null): {resp.count}")

# Count total active jobs
resp2 = supabase.table("jobs").select("id", count="exact").eq("is_active", True).execute()
print(f"Jobs aktif: {resp2.count}")

# Sample check
resp3 = supabase.table("jobs").select("job_title", "company", "salary").not_.is_("salary", "null").limit(5).execute()
print("\nContoh 5 jobs dengan salary:")
for j in resp3.data:
    print(f"  - {j.get('job_title')} @ {j.get('company')}: {j.get('salary')}")