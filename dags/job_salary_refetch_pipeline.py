import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Path of the project workspace
PROJECT_PATH = "/home/lightman/Documents/Project"

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

# Define the DAG for incremental salary refetch (Runs daily at 02:00)
with DAG(
    'job_salary_refetch_pipeline',
    default_args=default_args,
    description=(
        'Incremental salary refetch: re-scrap salary for existing job listings '
        'from live portal pages (rate-limited, resume from checkpoint). '
        '(Runs daily at 02:00 WIB)'
    ),
    schedule_interval='0 2 * * *',  # Every day at 02:00
    catchup=False,
    tags=['portfolio', 'scraping', 'salary-refetch', 'etl'],
) as dag:

    # Task: Refetch salary for a limited batch of existing jobs (250 URLs per run)
    refetch_salary = BashOperator(
        task_id='refetch_salary_batch',
        bash_command=(
            f"cd {PROJECT_PATH} && source venv/bin/activate && "
            f"python -u salary_refetch.py --batch-size 250"
        ),
        execution_timeout=timedelta(minutes=150),
    )

    refetch_salary