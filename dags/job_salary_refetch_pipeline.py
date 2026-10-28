import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Path of the project workspace
PROJECT_PATH = "/home/lightman/Documents/Project/job-scrapper"

# Default arguments for the DAG tasks
default_args = {
    'owner': 'data_engineer',
    'depends_on_past': False,
    'start_date': datetime(2026, 8, 6),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG for incremental salary refetch (Runs every 2 hours)
with DAG(
    'job_salary_refetch_pipeline',
    default_args=default_args,
    description=(
        'Incremental salary refetch: re-scrap salary for existing job listings '
        'from live portal pages (rate-limited, resume from checkpoint). '
        '(Daily until all OLD jobs without salary are processed, then pauses itself; new jobs get salary from the scrapers)'
    ),
    schedule_interval='20 3 * * *',  # Harian: backlog data lama tinggal sedikit; juga menyinkronkan salary ke Supabase
    catchup=False,
    max_active_runs=1,
    tags=['portfolio', 'scraping', 'salary-refetch', 'etl'],
) as dag:

    # Task: Refetch salary for a limited batch of existing jobs (150 URLs per run)
    refetch_salary = BashOperator(
        task_id='refetch_salary_batch',
        bash_command=(
            f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
            f"python -u salary_refetch.py --batch-size 150"
        ),
        execution_timeout=timedelta(minutes=60),  # progres tersimpan tiap 50 URL & saat SIGTERM
    )

    refetch_salary

    # Gerbang kualitas data: gagal bila ada salary placeholder berulang, salary 0, duplikat, atau Supabase tidak sinkron
    data_quality = BashOperator(
        task_id='data_quality_check',
        bash_command=(
            f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
            f"python -u data_quality_check.py; rc=$?; "
            # Selesai = semua data lama sudah diproses -> DAG mem-pause dirinya sendiri (tidak berulang selamanya).
            # Pause dilakukan di task TERAKHIR agar run yang sedang berjalan tidak tertahan di tengah.
            f"if [ -f salary_refetch_complete.flag ]; then "
            f"airflow dags pause job_salary_refetch_pipeline || true; fi; exit $rc"
        ),
        execution_timeout=timedelta(minutes=10),
        retries=0,
    )

    refetch_salary >> data_quality
