# Invotra legacy database compatibility repair

Error addressed: `sqlite3.OperationalError: no such column: inventory_session.sub_location_id` after an older Invotra installation is upgraded to the November 2026 application.

The prior Invotra installation used `location` and `inventory_session.location_id`, while the rebuilt Invotra uses `parent_location`, `sub_location`, and `inventory_session.sub_location_id`. Adding the new column alone does not preserve associations; this repair maps old locations to new company/parent/subunit records, copies legacy inventory line columns into new snapshots, and keeps original data intact.

## Install

Use the **full release** ZIP if your project is based on the latest Invotra November 2026 test-launch project. Or copy only these three files into the same relative paths:

- `app.py`
- `scripts/db_preflight.py`
- `scripts/legacy_schema.py` **(new; do not omit)**

Preserve the `scripts/` directory. Render's start command should be `bash start.sh`. If start command is still `gunicorn app:app`, the application-level migration is also run on initialization.

The preflight script makes an SQLite backup in `/data/backups` before application startup. The application migrates the previous database structure after creating any new tables and before querying inventories. The older columns and tables remain untouched so historical records are available for audit or rollback. The legacy location-ID mapping is saved to the `invotra_legacy_location_map` table.

## Important

- Deploy to the **same Render service** with the **same `/data` persistent disk**, and leave `DATABASE_URL` unset for SQLite (or point it to the same persisted SQLite file).
- Do not remove, reset, or overwrite `/data/invotra.db`.
- If your old schema is significantly different from v7, the migration may report an exception. Do not keep redeploying or attempt to force-drop tables; share the error and schema with the developer.
- This migration has passed a synthetic legacy-v7 SQLite test with preserved user, location assignment, inventory line, totals, GL and unknown item. It has **not** been tested on your actual Render database; run a full backup and validate all pages before launching.
- Flask live integration was not available locally here; still perform a Render login, active inventory, history, admin review, GL report and email test.
