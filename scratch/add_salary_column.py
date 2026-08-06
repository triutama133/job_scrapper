import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# Load from alumni_RESTAPI/.env where DB URL is defined
load_dotenv("alumni_RESTAPI/.env")
db_url = os.environ.get("SUPABASE_DB_URL")

async def main():
    if not db_url:
        print("Error: SUPABASE_DB_URL not found in environment.")
        return
        
    print(f"Connecting to database to add salary column...")
    try:
        conn = await asyncpg.connect(db_url)
        print("Connected! Executing ALTER TABLE...")
        
        # 1. Add column if not exists
        await conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary TEXT;")
        print("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary TEXT; -> Selesai.")
        
        # 2. Verify columns
        rows = await conn.fetch("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'jobs';
        """)
        print("\nColumns in jobs table:")
        for r in rows:
            print(f"- {r['column_name']}: {r['data_type']}")
            
        await conn.close()
    except Exception as e:
        print("Error altering table:", e)

if __name__ == "__main__":
    asyncio.run(main())
