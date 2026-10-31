import os
import sys
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Path of the project workspace
PROJECT_PATH = "/home/lightman/Documents/Project/job-scrapper"

# Default arguments for the DAG tasks
default_args = {
    'owner': 'data_engineer',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 20),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    # SequentialExecutor menjalankan 1 task sekali: scraper didahulukan dari salary refetch/repair bila antre
    'priority_weight': 10,
    'weight_rule': 'absolute',
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG for Scraping and ETL (Every 4 hours)
with DAG(
    'job_scraper_and_etl',
    default_args=default_args,
    description='Automated Job Scraping, PySpark Cleanse & Supabase Upload (Runs every 12 hours)',
    schedule_interval='15 */12 * * *',  # Setiap 12 jam, menit :15 (satu siklus penuh bisa berjam-jam)
    max_active_runs=1,
    catchup=False,
    tags=['portfolio', 'scraping', 'etl'],
) as dag:

    # Task 1a: Indonesia, platform BROWSER (Glints/Jobstreet/LinkedIn; semua lowongan, keyword data diprioritaskan).
    # 4 keyword data + 12 keyword bergilir dari pool 79 keyword (seluruh pool tuntas dalam ~7 run). ~40 menit.
    scrape_jobs = BashOperator(
        task_id='scrape_jobs',
        bash_command=(f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
                      "python -u scraper.py --platform glints,jobstreet,linkedin --max-jobs 50 --remote-regions ''"),
        execution_timeout=timedelta(minutes=75),
    )

    # Task 1a': Indonesia, Kalibrr (JSON API, tanpa browser) berjalan PARALEL dengan scrape_jobs: semua ~83 keyword
    # tiap run, 30 lowongan baru per keyword. Dipisah agar waktu 20-30 menitnya tidak menambah durasi scrape_jobs.
    scrape_kalibrr = BashOperator(
        task_id='scrape_kalibrr',
        bash_command=(f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
                      "python -u scraper.py --platform kalibrr --remote-regions ''"),
        execution_timeout=timedelta(minutes=60),
    )

    # Task 1b: LinkedIn pencarian 'Worldwide', HANYA remote. Negara tertentu (US/EU/SEA) dihapus: lowongan remote yang dibatasi
    # negara ditolak (remote_eligibility), jadi hasilnya hampir selalu 0 dan memakan ~40 menit per run.
    scrape_remote_global = BashOperator(
        task_id='scrape_remote_global',
        bash_command=(f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
                      "python -u scraper.py --platform linkedin --skip-home --max-jobs 30 "
                      "--remote-regions 'Worldwide'"),
        execution_timeout=timedelta(minutes=60),
    )

    # Task 2: Transform raw JSON data using PySpark
    transform_spark = BashOperator(
        task_id='transform_spark',
        bash_command=f"cd {PROJECT_PATH} && source ../venv/bin/activate && python -u spark_transform.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Task 3: Load processed jobs into Supabase (ETL)
    upload_supabase = BashOperator(
        task_id='upload_supabase',
        bash_command=f"cd {PROJECT_PATH} && source ../venv/bin/activate && python -u upload_to_supabase.py",
        execution_timeout=timedelta(minutes=15),
    )


    # Gerbang kualitas data: gagal bila ada salary placeholder berulang, salary 0, duplikat, atau Supabase tidak sinkron
    data_quality = BashOperator(
        task_id='data_quality_check',
        bash_command=f"cd {PROJECT_PATH} && source ../venv/bin/activate && python -u data_quality_check.py",
        execution_timeout=timedelta(minutes=10),
        retries=0,
    )

    # Set task execution dependencies
    [scrape_jobs, scrape_kalibrr] >> scrape_remote_global >> transform_spark >> upload_supabase >> data_quality
