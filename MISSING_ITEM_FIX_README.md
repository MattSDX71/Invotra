# Invotra missing-item submission — legacy SQLite compatibility fix

## Error fixed
`sqlite3.IntegrityError: NOT NULL constraint failed: unknown_item.company_id` when a user submits an unrecognized barcode.

The older SQLite table retains `company_id` (and sometimes other old fields) as required, but the current Invotra code correctly uses `sub_location_id` and derives the company from the parent location. The old required fields are no longer supplied on INSERT.

## Files
- `scripts/legacy_schema.py` (updated non-destructive compatibility migration)
- `tests/test_missing_item_legacy_constraints.py` (new regression tests)

## Installation
Replace `scripts/legacy_schema.py` in the **existing** deployed Invotra repository. Commit and deploy the same Render service, keeping the `/data` persistent disk mounted. With the current `app.py` startup logic, the database migration runs before routes execute. You do **not** need to change the app or recreate the database.

The compatibility migration automatically creates a timestamped SQLite backup in `/data/backups` (when the database is under `/data`) before rebuilding any affected legacy table. It preserves rows, relationships, explicit indexes and triggers, and validates foreign keys. It makes only obsolete fields nullable; current required fields stay required. The change is idempotent.

## Tests performed
`PYTHONPATH=. pytest -q tests/test_missing_item_legacy_constraints.py tests/test_legacy_schema.py` — 5 passed, on simulated legacy SQLite schemas. Actual live Render DB not tested.

## Smoke test on Render
1. Log in and resume an in-progress inventory.
2. Scan a new UPC, enter a specific name and suggested GL account, then choose Add Missing Item.
3. Confirm success and that the pending item is listed in the inventory.
4. Go to Admin and approve the missing item; confirm GL and cost update in the report.
5. Confirm a legacy inventory report still displays prior history.

If a new error occurs, send the first `sqlite3.IntegrityError` or `OperationalError` message and its INSERT/SELECT statement. Do not delete `/data/invotra.db`.
