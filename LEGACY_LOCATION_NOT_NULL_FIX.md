# Invotra: legacy inventory_session.location_id fix

## What this fixes
Older Invotra databases enforce inventory_session.location_id NOT NULL. Current Invotra writes sub_location_id for new sessions, so clicking Start Inventory caused SQLAlchemy IntegrityError.

## Install
1. Back up the Render /data database and keep the disk attached.
2. Replace ONLY scripts/legacy_schema.py in your current GitHub project with the version in this package.
3. Commit and redeploy the existing Render service. Keep the start command `bash start.sh`.
4. In Render logs look for: `Invotra schema migration: inventory_session.location_id now nullable (historical values preserved)`.
5. Try Start Inventory, then scan an item and submit a test session.

The migration saves a new SQLite backup to /data/backups immediately before the one-time table rebuild, changes old location_id to nullable, and preserves old location IDs, rows, indexes and triggers. It runs once; later runs detect the already-fixed schema.

## Note
This repairs the shown SQLite constraint error, not proof of successful testing on your Render database. If a new error occurs, capture the full traceback.
