#!/usr/bin/env bash
# Migrasi metadata Airflow: SQLite + SequentialExecutor  ->  PostgreSQL + LocalExecutor
# Alasan: SequentialExecutor menjalankan SATU task sekali (run remote 06:00 UTC baru selesai ~13:07 UTC karena antre
# di belakang salary refetch). SQLite tidak bisa dipakai dengan LocalExecutor.
#
#   bash scripts/migrate_airflow_to_postgres.sh --check    # hanya pemeriksaan, tidak mengubah apa pun
#   bash scripts/migrate_airflow_to_postgres.sh --apply    # eksekusi (menunggu Airflow idle sebelum dialihkan)
#
# Sudo: memakai SUDO_PASS dari .env lewat stdin (tidak pernah dicetak). Password DB dibuat acak, hanya disimpan
# di airflow.cfg. Login 'admin' dipertahankan (hash password lama disalin). Riwayat run/task di SQLite tidak dipindah
# (tetap ada di backup & file log).
set -euo pipefail

MODE="${1:---check}"
PROJECT=/home/lightman/Documents/Project/job-scrapper
VENV=/home/lightman/Documents/Project/venv
AF="$VENV/bin/airflow"
CFG=/home/lightman/airflow/airflow.cfg
DB=/home/lightman/airflow/airflow.db
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/home/lightman/airflow/backup_$STAMP
PARALLELISM=3        # 4 core / 16 GB; task berat = Camoufox (~1 GB) & Spark JVM (~1-2 GB)
PER_DAG=2
MAX_WAIT_MIN=${MAX_WAIT_MIN:-240}

set +x
set -a; . "$PROJECT/.env"; set +a
: "${SUDO_PASS:?SUDO_PASS tidak ada di .env}"
s() { printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$@"; }        # sudo non-interaktif, password lewat stdin

q() { "$VENV/bin/python" - "$@" <<'PY'
import sqlite3,sys
c=sqlite3.connect('file:/home/lightman/airflow/airflow.db?mode=ro',uri=True,timeout=30)
print(c.execute(sys.argv[1]).fetchone()[0])
PY
}
# Hanya DAG yang masih aktif: dag_run 'running' dari DAG yang sudah dihapus (zombie sejak Juli) tidak boleh menahan migrasi.
busy() { echo $(( $(q "select count(*) from dag_run where state='running' and dag_id in (select dag_id from dag where is_active=1)") + $(q "select count(*) from task_instance where state in ('running','queued','scheduled') and dag_id in (select dag_id from dag where is_active=1)") )); }

log() { echo "[$(date +%T)] $*"; }

log "== Pemeriksaan awal =="
log "DAG run berjalan + task aktif: $(busy) (harus 0 saat pengalihan; skrip akan menunggu)"
UNPAUSED=$("$VENV/bin/python" - <<'PY'
import sqlite3
c=sqlite3.connect('file:/home/lightman/airflow/airflow.db?mode=ro',uri=True)
print(" ".join(r[0] for r in c.execute("select dag_id from dag where is_paused=0 and is_active=1 order by dag_id")))
PY
)
log "DAG aktif yang akan diaktifkan lagi: $UNPAUSED"
command -v psql >/dev/null && log "postgresql: terpasang" || log "postgresql: belum terpasang (akan di-install)"
[ "$MODE" = "--apply" ] || { log "Mode --check selesai."; exit 0; }

log "== 1. Backup =="
mkdir -p "$BACKUP"; cp "$CFG" "$BACKUP/airflow.cfg"; cp "$DB" "$BACKUP/airflow.db"; chmod 700 "$BACKUP"
echo "$UNPAUSED" > "$BACKUP/unpaused_dags.txt"
"$VENV/bin/python" - <<PY > "$BACKUP/admin_hash.txt"
import sqlite3
c=sqlite3.connect('file:/home/lightman/airflow/airflow.db?mode=ro',uri=True)
print(c.execute("select username||'|'||password from ab_user where username='admin'").fetchone()[0])
PY
chmod 600 "$BACKUP/admin_hash.txt"; log "backup: $BACKUP"

log "== 2. PostgreSQL (tidak mengganggu Airflow yang sedang jalan) =="
PGPW=$(openssl rand -hex 24)
s apt-get update -qq
s env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql libpq-dev >/dev/null
s systemctl enable --now postgresql >/dev/null 2>&1 || true
if s -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='airflow'" | grep -q 1; then
  s -u postgres psql -qc "ALTER USER airflow WITH PASSWORD '$PGPW';"
else
  s -u postgres psql -qc "CREATE USER airflow WITH PASSWORD '$PGPW';"
fi
s -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1 || s -u postgres psql -qc "CREATE DATABASE airflow OWNER airflow;"
"$VENV/bin/pip" install -q psycopg2-binary
PGPASSWORD="$PGPW" psql -h localhost -U airflow -d airflow -tAc "select 'postgres ok'" || { log "ABORT: tidak bisa login ke Postgres"; exit 1; }

log "== 3. Menunggu Airflow idle (maks $MAX_WAIT_MIN menit) =="
waited=0
while [ "$(busy)" != "0" ]; do
  [ $waited -lt $((MAX_WAIT_MIN*60)) ] || { log "ABORT: Airflow masih sibuk setelah $MAX_WAIT_MIN menit; tidak ada yang diubah pada Airflow"; exit 1; }
  sleep 20; waited=$((waited+20)); [ $((waited % 300)) -eq 0 ] && log "  masih sibuk ($(busy) aktif), menunggu..."
done
log "Airflow idle -> mengalihkan"

log "== 4. Hentikan Airflow, ubah konfigurasi =="
s systemctl stop airflow-scheduler airflow-webserver
cp "$DB" "$BACKUP/airflow.db.final"       # salinan terakhir setelah semuanya berhenti
sed -i "s|^sql_alchemy_conn = .*|sql_alchemy_conn = postgresql+psycopg2://airflow:$PGPW@localhost/airflow|" "$CFG"
sed -i "s|^executor = .*|executor = LocalExecutor|" "$CFG"
sed -i "s|^parallelism = .*|parallelism = $PARALLELISM|" "$CFG"
sed -i "s|^max_active_tasks_per_dag = .*|max_active_tasks_per_dag = $PER_DAG|" "$CFG"

rollback() { log "GAGAL -> rollback ke SQLite"; cp "$BACKUP/airflow.cfg" "$CFG"; s systemctl start airflow-scheduler airflow-webserver; }
trap 'rollback' ERR

log "== 5. Migrasi skema & akun =="
export AIRFLOW_HOME=/home/lightman/airflow
$AF db migrate 2>&1 | tail -3
RAND_PW=$(openssl rand -hex 12)
$AF users create --username admin --firstname Admin --lastname Admin --role Admin --email admin@example.com --password "$RAND_PW" >/dev/null 2>&1 || true
HASH=$(cut -d'|' -f2- "$BACKUP/admin_hash.txt")
HASH="$HASH" PGPASSWORD="$PGPW" "$VENV/bin/python" - <<'PY'
import os,psycopg2
c=psycopg2.connect(host="localhost",user="airflow",dbname="airflow",password=os.environ["PGPASSWORD"])
cur=c.cursor(); cur.execute("update ab_user set password=%s where username='admin'",(os.environ["HASH"],)); c.commit()
print("admin: hash password lama dipulihkan, baris diperbarui =",cur.rowcount)
PY
trap - ERR

log "== 6. Start & aktifkan lagi DAG =="
s systemctl start airflow-scheduler airflow-webserver
for i in $(seq 1 18); do
  n=$($AF dags list 2>/dev/null | grep -cE "job_scraper_and_etl|remote_data_engineering_pipeline|job_salary_refetch_pipeline|job_link_checker_and_etl" || true)
  [ "$n" -ge 4 ] && break; sleep 10
done
for d in $UNPAUSED; do $AF dags unpause "$d" 2>&1 | tail -1; done
log "executor: $($AF config get-value core executor) | parallelism: $($AF config get-value core parallelism)"
$AF dags list 2>/dev/null | awk -F'|' 'NR>2{print $1,$NF}' | head -8
log "SELESAI. Rollback: s systemctl stop airflow-*; cp $BACKUP/airflow.cfg $CFG; s systemctl start airflow-*"
