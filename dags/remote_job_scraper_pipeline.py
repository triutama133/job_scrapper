import os
import sys
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_PATH = "/home/lightman/Documents/Project/job-scrapper"
DAG_ID = "remote_data_engineering_pipeline"

default_args = {
    'owner': 'data_engineer',
    'depends_on_past': False,
    'start_date': datetime(2026, 10, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id=DAG_ID,
    default_args=default_args,
    description='Automated Global Remote Data Engineering Jobs & Upwork Gigs Scraper with Supabase ETL',
    schedule_interval='0 */6 * * *',  # Every 6 hours
    catchup=False,
    tags=['scraping', 'remote', 'data_engineering', 'upwork', 'etl'],
) as dag:

    # Task 1: Scrape remote data engineering jobs across multiple global platforms
    scrape_remote = BashOperator(
        task_id='scrape_remote_jobs',
        bash_command=(
            f"cd {PROJECT_PATH} && source ../venv/bin/activate && "
            f"python -u remote_scraper.py "
            f"--keyword 'data engineer' "
            f"--limit 25 "
            f"--upload-supabase"
        ),
        execution_timeout=timedelta(minutes=30),
    )

    # Task 2: Transform raw JSON data using PySpark
    transform_spark = BashOperator(
        task_id='transform_spark',
        bash_command=f"cd {PROJECT_PATH} && source ../venv/bin/activate && python -u spark_transform.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Task 3: Load/Sync processed jobs into Supabase
    upload_supabase = BashOperator(
        task_id='upload_supabase',
        bash_command=f"cd {PROJECT_PATH} && source ../venv/bin/activate && python -u upload_to_supabase.py",
        execution_timeout=timedelta(minutes=15),
    )

    scrape_remote >> transform_spark >> upload_supabase
