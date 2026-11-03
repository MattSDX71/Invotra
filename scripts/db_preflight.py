"""Invotra SQLite start-up guard: check schema and back up the current DB before loading Flask.

The script never deletes or modifies source tables; sqlite3.backup is transactionally safe.
It is not an Alembic migration and will stop on a mismatched legacy schema.
"""
from __future__ import annotations
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

CHECK = {
    "user": {"id", "full_name", "email", "phone", "password_hash", "role", "status", "initial_password_reset_required"},
    "company": {"id", "name", "active"},
    "parent_location": {"id", "company_id", "name"},
    "sub_location": {"id", "parent_id", "name"},
    "gl_account": {"id", "company_id", "code", "description", "active"},
    "item_master": {"id", "company_id", "item_name", "each_upc", "case_upc", "each_cost", "case_cost", "gl_account_id"},
    "inventory_session": {"id", "sub_location_id", "created_by_user_id", "status", "total_quantity", "total_value"},
    "inventory_line": {"id", "session_id", "quantity", "extended_value", "scan_type"},
    "unknown_item": {"id", "session_id", "status", "scanned_upc", "proposed_gl_account_id"},
    "user_sub_location_access": {"id", "user_id", "sub_location_id"},
    "sub_location_request": {"id", "parent_id", "status"},
}


def main():
    database_url = os.getenv("DATABASE_URL", "")
    if database_url and not database_url.startswith("sqlite:"):
        print("Invotra preflight: PostgreSQL database configured. Use your provider's backups/snapshots.")
        return 0
    if database_url.startswith("sqlite:////"):
        path = Path(database_url.replace("sqlite:////", "/", 1))
    elif Path("/data").is_dir():
        path = Path("/data/invotra.db")
    elif os.getenv("INVOTRA_ENV") == "production":
        raise RuntimeError("Production /data disk is not mounted; refusing ephemeral database")
    else:
        print("Invotra preflight: local database; no production snapshot required.")
        return 0

    if not path.is_file():
        print(f"Invotra preflight: no prior database at {path}; creating a new database at first launch.")
        return 0
    print(f"Invotra preflight: found existing DB at {path}")
    source = sqlite3.connect(str(path), timeout=30)
    try:
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            print("Invotra preflight: database file is empty")
            return 0
        errors=[]
        for table, fields in CHECK.items():
            if table not in tables:
                errors.append(f"missing table: {table}")
                continue
            found={row[1] for row in source.execute(f'PRAGMA table_info("{table}")')}
            missing=fields-found
            if missing:
                errors.append(f"table {table} missing columns: {', '.join(sorted(missing))}")
        # Snapshot even an incompatible database before stopping, to protect its data.
        backups=path.parent/'backups'
        backups.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination=backups/f"invotra-before-start-{stamp}.db"
        with sqlite3.connect(str(destination)) as dest:
            source.backup(dest)
        print(f"Invotra preflight: backup saved to {destination}")
        old=sorted(backups.glob('invotra-before-start-*.db'))
        for extra in old[:-5]:
            extra.unlink()
        # Safe, additive migration for databases created before the profile phone field.
        # Only apply AFTER writing the backup. Never drop/recreate tables or modify rows.
        if 'user' in tables:
            existing_user_columns = {row[1] for row in source.execute('PRAGMA table_info("user")')}
            if 'phone' not in existing_user_columns:
                source.execute('ALTER TABLE "user" ADD COLUMN "phone" VARCHAR(50)')
                source.commit()
                print('Invotra migration: added nullable user.phone; users and historical records preserved.')
                errors = [err for err in errors if err != 'table user missing columns: phone']
        if errors:
            print("SCHEMA INCOMPATIBLE — deployment stopped safely. No data was deleted.")
            for err in errors:
                print("  ", err)
            return 1
        integrity=source.execute('PRAGMA quick_check').fetchone()[0]
        if integrity != 'ok':
            print(f"Database integrity check failed: {integrity}")
            return 1
        print('Invotra preflight: required v6+ schema present and integrity check OK')
        return 0
    finally:
        source.close()

if __name__=='__main__':
    sys.exit(main())
