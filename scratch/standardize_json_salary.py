import json
import os

json_path = "/home/lightman/Documents/Project/jobs_data.json"

if os.path.exists(json_path):
    print("Reading jobs_data.json...")
    with open(json_path, 'r', encoding='utf-8') as f:
        try:
            jobs = json.load(f)
        except json.JSONDecodeError:
            # Maybe it's JSON Lines? Let's check
            f.seek(0)
            jobs = []
            for line in f:
                if line.strip():
                    jobs.append(json.loads(line.strip()))
    
    print(f"Loaded {len(jobs)} jobs. Standardizing keys...")
    
    updated_count = 0
    for job in jobs:
        if 'salary' not in job:
            job['salary'] = None
            updated_count += 1
            
    print(f"Added 'salary': null to {updated_count} jobs.")
    
    # Save back as pretty JSON list (or line-by-line if needed, let's check current jobs_data.json format)
    # The grep search showed lines like "        \"salary\": \"IDR 6 – 8\"" which indicates formatted JSON list
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=4, ensure_ascii=False)
    print("Successfully updated jobs_data.json")
else:
    print("jobs_data.json not found")
