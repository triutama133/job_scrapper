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

# Define the DAG for Link Checking and Sync (Runs Daily)
with DAG(
    'job_link_checker_and_etl',
    default_args=default_args,
    description='Automated Link Validation & Supabase Sync (Runs daily at 2:00 AM UTC)',
    schedule_interval='0 2 * * *',  # Daily
    catchup=False,
    tags=['portfolio', 'link_checking', 'etl'],
) as dag:

    # Task 1: Validate keaktifan link (Link Checker)
    validate_links = BashOperator(
        task_id='validate_links',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u link_checker.py",
        execution_timeout=timedelta(minutes=45),
    )

    # Task 2: Transform updated JSON data using PySpark
    transform_spark = BashOperator(
        task_id='transform_spark',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u spark_transform.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Task 3: Load updated jobs into Supabase (ETL)
    upload_supabase = BashOperator(
        task_id='upload_supabase',
        bash_command=f"cd {PROJECT_PATH} && source venv/bin/activate && python -u upload_to_supabase.py",
        execution_timeout=timedelta(minutes=15),
    )

    # Set task execution dependencies
    validate_links >> transform_spark >> upload_supabase
