import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Path direktori proyek job-scrapper
PROJECT_PATH = "/home/lightman/Documents/Project/job-scrapper"
DAG_ID = "jobstreet_description_repair_pipeline"

# Pengaturan default task
default_args = {
    'owner': 'data_engineer',
    'depends_on_past': False,
    'start_date': datetime(2026, 9, 25),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# Inisialisasi DAG Airflow untuk perbaikan data deskripsi Jobstreet
with DAG(
    dag_id=DAG_ID,
    default_args=default_args,
    description=(
        'Scheduled incremental repair for Jobstreet descriptions: '
        'memperbaiki lowongan boilerplate "Bergabunglah..." dengan Camoufox stealth browser. '
        'Berjalan tiap 2 jam (50 lowongan/batch) & otomatis PAUSE saat data sampah habis.'
    ),
    schedule_interval='40 */2 * * *',  # Setiap 2 jam sekali
    catchup=False,
    tags=['scraping', 'jobstreet', 'repair', 'camoufox', 'etl'],
) as dag:

    # Task perbaikan batch:
    # - Menggunakan Camoufox stealth browser
    # - Jeda acak 2.0 - 4.0 detik antar URL agar aman dari rate limiting / blocking IP
    # - Flag --auto-pause-dag akan otomatis mematikan DAG ini saat sisa data corrupt = 0
    repair_batch = BashOperator(
        task_id='repair_jobstreet_batch',
        bash_command=(
            f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
            f"python -u repair_jobstreet_descriptions.py "
            f"--batch-size 50 "
            f"--delay-min 2.0 "
            f"--delay-max 4.0 "
            f"--reverse "
            f"--sync-supabase "
            f"--auto-pause-dag {DAG_ID}"
        ),
        execution_timeout=timedelta(minutes=30),
    )

    repair_batch
