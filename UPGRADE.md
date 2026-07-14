# Upgrading an Existing Installation

This guide is for a server that already has an **earlier version** of ZKTecePuller
running in production. It's an in-place **extend**, not a clean setup — existing
data, devices, and users are preserved.

---

## 1. Backup the database first (critical)

On this server, Postgres uses peer auth via the `postgres` OS user, so backups
are run with `sudo -u postgres` (you'll be prompted for the sudo password).

```bash
sudo -u postgres pg_dump -d zkteco -f db_backup/zkteco_$(date +%Y%m%d).sql
```

Or just run the automated script (does the same thing, uses `db_config.json`
for the database name):

```bash
./backup.sh
```

- `db_backup/` holds dumps containing employee PII — keep it out of git (see `.gitignore`).
- Confirm the dump file was actually created and has a non-zero size before continuing.

To restore from this backup if something goes wrong:

```bash
sudo -u postgres psql -d zkteco < db_backup/zkteco_YYYYMMDD.sql
```

## 2. Stop the running service

Stop whatever currently runs the app (Windows Service, systemd unit, or the
`start_web.bat` / `start_web.sh` process) before touching any files.

## 3. Preserve local config

These files are gitignored and untouched by `git pull`, but confirm they still
exist after the update:

- `.env`
- `db_config.json`
- `devices.json`
- `users.json`
- `auto_attend_config.json` *(new — only needed if you use the auto-attend feature; copy from `auto_attend_config.json.example` and fill in `user_id` / `device_ids`)*

## 4. Pull the new code

```bash
git pull origin master
```

## 5. Update Python dependencies

Activate the venv first — installing into system Python on Debian/Ubuntu
fails with `error: externally-managed-environment`:

```bash
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

`pywin32` (Windows-only, used by the optional Windows Service wrapper) is
marked `; sys_platform == 'win32'` in `requirements.txt`, so `pip` skips it
automatically on Linux/Mac — no error expected there anymore.

## 6. Restart the app

Schema changes are **additive and auto-applied on startup** via `init_schema`
in `db.py` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) — safe to run against
existing data, no manual SQL required.

## 7. Verify

- Check `logs/` for startup errors.
- Log in and confirm the dashboard and reports load.
- Spot-check that new columns exist:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'global_users'
ORDER BY ordinal_position;
```

---

# Running pgAdmin in the Browser

pgAdmin lets you inspect the `zkteco` database (tables, run queries, verify
migrations) through a web UI.

## Option A — pgAdmin 4 Desktop (Windows)

If pgAdmin 4 is already installed (it ships as an option with the PostgreSQL
Windows installer):

1. Open **pgAdmin 4** from the Start Menu.
2. It launches its own local web server and opens automatically in your
   default browser at an address like `http://127.0.0.1:PORT/browser/`
   (pgAdmin picks the port automatically).
3. On first launch you'll set a **master password** for pgAdmin itself.
4. In the left tree: right-click **Servers → Register → Server…**
   - **General tab** → Name: `ZKTeco DB` (or anything)
   - **Connection tab**:
     - Host: `localhost` (or the DB host from your `.env` / `db_config.json`)
     - Port: `5432`
     - Maintenance database: `zkteco`
     - Username: `postgres` (or the user from `db_config.json`)
     - Password: from `db_config.json` / `.env`
   - Click **Save**.
5. Expand **ZKTeco DB → Databases → zkteco → Schemas → public → Tables** to
   browse tables, or use **Tools → Query Tool** to run SQL.

## Option B — pgAdmin 4 Web mode (Linux server)

If you're on Ubuntu/Linux and want pgAdmin accessible over HTTP instead of a
desktop app:

```bash
sudo apt install pgadmin4-web
sudo /usr/pgadmin4/bin/setup-web.sh
```

The setup script prompts for an admin email/password for pgAdmin's own login.
Once done, open in a browser:

```
http://localhost/pgadmin4
```

(replace `localhost` with the server's IP/hostname if browsing from another
machine, and make sure port 80/443 is reachable through any firewall).

Then register the server the same way as steps 4–5 in Option A above, using
the `zkteco` database credentials.

## Quick sanity queries once connected

```sql
-- confirm connection / row counts
SELECT count(*) FROM global_users;
SELECT count(*) FROM attendance_logs;

-- confirm this upgrade's new columns exist
SELECT column_name FROM information_schema.columns
WHERE table_name = 'devices' AND column_name IN ('force_udp', 'connection_timeout');
```
