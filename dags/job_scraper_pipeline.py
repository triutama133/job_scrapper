import os
import sys
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Path of the project workspace
PROJECT_PATH = "/home/lightman/Documents/Project"

# Default arguments for the DAG tasks
default_args = {
    'owner': 'data_engineer',
    'depends_on_past': False,
    'start_date': datetime(2026, 7, 20),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG for Scraping and ETL (Every 4 hours)
with DAG(
    'job_scraper_and_etl',
    default_args=default_args,
    description='Automated Job Scraping, PySpark Cleanse & Supabase Upload (Runs every 4 hours)',
    schedule_interval='0 */4 * * *',  # Every 4 hours
    catchup=False,
    tags=['portfolio', 'scraping', 'etl'],
) as dag:

    # Task 1: Scrape new job listings from LinkedIn and Kalibrr
    scrape_jobs = BashOperator(
        task_id='scrape_jobs',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u scraper.py --platform all --max-jobs 50",
        execution_timeout=timedelta(minutes=45),
    )

    # Task 2: Transform raw JSON data using PySpark
    transform_spark = BashOperator(
        task_id='transform_spark',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u spark_transform.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Task 3: Load processed jobs into Supabase (ETL)
    upload_supabase = BashOperator(
        task_id='upload_supabase',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u upload_to_supabase.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Set task execution dependencies
    scrape_jobs >> transform_spark >> upload_supabase
