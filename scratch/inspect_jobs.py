import json
import os
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Inspect local JSON
try:
    with open("jobs_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        print("Local jobs count:", len(data))
        has_salary = [j for j in data if j.get("salary") is not None]
        print("Local jobs with salary:", len(has_salary))
        if has_salary:
            print("Sample local jobs with salary:")
            for j in has_salary[:3]:
                print(f" - {j.get('job_title')} ({j.get('company')}): {j.get('salary')}")
except Exception as e:
    print("Error reading jobs_data.json:", e)

# 2. Inspect Supabase jobs table
load_dotenv()
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if supabase_url and supabase_key:
    try:
        supabase: Client = create_client(supabase_url, supabase_key)
        # Check total jobs in supabase
        res_count = supabase.table("jobs").select("id", count="exact").execute()
        total_sb = res_count.count if hasattr(res_count, 'count') else len(res_count.data)
        print(f"\nSupabase total jobs: {total_sb}")
        
        # Check jobs with non-null salary in supabase
        res_sal = supabase.table("jobs").select("job_title, company, salary").not_.is_("salary", "null").limit(10).execute()
        print("Supabase jobs with non-null salary (sample limit 10):", len(res_sal.data))
        for j in res_sal.data:
            print(f" - {j.get('job_title')} ({j.get('company')}): {j.get('salary')}")
            
        # Count total in db with non-null salary
        res_sal_count = supabase.table("jobs").select("id", count="exact").not_.is_("salary", "null").execute()
        total_sal_sb = res_sal_count.count if hasattr(res_sal_count, 'count') else len(res_sal_count.data)
        print(f"Total jobs with non-null salary in Supabase: {total_sal_sb}")
    except Exception as e:
        print("\nError querying Supabase:", e)
else:
    print("\nSupabase credentials not found in env.")

