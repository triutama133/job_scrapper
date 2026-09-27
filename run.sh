#!/bin/bash

# Arahkan ke direktori proyek job-scrapper
cd /home/lightman/Documents/Project/job-scrapper

# Aktifkan virtual environment (berada di direktori parent / root workspace)
source ../venv/bin/activate

# Jalankan pipeline dengan batasan 150 lowongan baru per hari (agar aman dari block IP)
# Hasil log eksekusi akan dicatat ke pipeline.log
python run_pipeline.py --max-jobs 150 >> /home/lightman/Documents/Project/job-scrapper/pipeline.log 2>&1

echo "Pipeline executed at $(date)" >> /home/lightman/Documents/Project/job-scrapper/pipeline.log
