#!/usr/bin/env bash
# ZKTeco Attendance Puller — database backup (Linux)
# Runs pg_dump as the postgres OS user (matches how backups/restores are
# normally run on this server, e.g. `sudo -u postgres psql -d zkteco < ...`).
# Writes a timestamped plain-SQL dump into db_backup/, restorable with:
#   sudo -u postgres psql -d <dbname> < db_backup/<dbname>_YYYYMMDD.sql
#
# Usage: ./backup.sh
# (will prompt for the sudo password interactively)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_FILE="db_config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: $CONFIG_FILE not found."
    echo "Create it from the example: cp db_config.json.example db_config.json"
    exit 1
fi

mkdir -p db_backup

PYTHON=$(command -v python3 || command -v python)

# ── read dbname out of db_config.json ───────────────────────────────────────
eval "$("$PYTHON" -c "
import json
c = json.load(open('$CONFIG_FILE'))
print('DBNAME=' + str(c.get('dbname', 'zkteco')))
")"

TODAY="$(date +%Y%m%d)"
BACKUP_FILE="db_backup/${DBNAME}_${TODAY}.sql"
TMP_FILE="/tmp/${DBNAME}_${TODAY}_$$.sql"

echo "Backing up database \"$DBNAME\" to $BACKUP_FILE ..."

# dump to /tmp first — the postgres user can always write there, regardless
# of who owns db_backup/ — then move it back and hand ownership to us
sudo -u postgres pg_dump -d "$DBNAME" -f "$TMP_FILE"
sudo mv "$TMP_FILE" "$BACKUP_FILE"
sudo chown "$(id -un)":"$(id -gn)" "$BACKUP_FILE"

echo ""
echo "Backup complete: $BACKUP_FILE"

