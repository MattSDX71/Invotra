# Invotra – Mobile Counting & Location-Scoped Administration Update

## Changes

- Start Inventory and scanner screens redesigned for small Android/Zebra displays. Scanner controls and submit button remain visible without traversing long lists; item counts and GL totals are in an expandable section.
- Scanner increments selectable as **0.25 / 0.50 / 0.75 / 1.00** of the scanned packaging type (each or case). Scans, manual count adjustments, unknown-item counts, CSV exports, GL totals and approval accounting retain fractional quantities.
- **Important:** the scan fraction is of the *package UPC scanned*. For example, scanning a case UPC at 0.25 costs 25% of that case's cost; scanning an each UPC at 0.25 costs 25% of the each price. The UI keeps the selected scan fraction after each scan.
- Super Admin User Directory is blank until a parent is selected. Parent filters apply to the displayed user list, displayed location badges and subunit assignment dropdown. Create User has an independent show/hide filter for parent groups.
- Admin approval queue, missing item queue and location access table are filtered to a selected **authorized** parent; a default parent is preselected.
- Super Admin notification rules now support optional recipient lists **per parent location**. Old global inventory-specific recipients are deliberately not carried over to other parents to avoid cross-campus email exposure. Global account/subunit request email routing remains unchanged.
- Submissions go to the counter and active admins responsible for the parent. Approvals send the GL summary and CSV to **all active users with access to the counted subunit**, the original counter, and parent admins. Additional scoped recipients can be set under Super Admin → Email Notifications by Parent Location.
- Approved sessions and CSVs are visible to assigned users at the counted subunit; other employees' in-progress/submitted inventory sessions remain inaccessible.
- Multi-recipient email is addressed individually/BCC to avoid disclosing the whole recipient list in the `To` header.

## Data & migration safety

- The existing `/data/invotra.db` file is retained, along with existing inventory sessions and history.
- `ParentNotificationRule` is a new independent table created with `db.create_all` during initialization. Existing notification rules are not deleted or overwritten.
- Fractional counts are represented as Decimal / SQLAlchemy Numeric(12,2) values. **SQLite** permits noninteger numeric values in old INTEGER-affinity columns, and needs no destructive table rebuild. This release is intended for the existing persistent **SQLite on Render** deployment. If you use PostgreSQL, migrate the `inventory_line.quantity` and `inventory_session.total_quantity` columns to NUMERIC(12,2) before deployment.
- Keep your persistent Render disk mounted at `/data`, and make a Render disk backup before deploying.

## Verification

- Python syntax compilation and Jinja2 template parse checks completed.
- SQLite integer-affinity fractional storage tested in isolation.
- Added `tests/test_qol_release.py` for fractional scan/count math, unknown-item fractional admin pricing, multi-session independence, parent filters, location-specific approval recipients and approved report access.
- **Full Flask/Render/Zebra integration tests have not been executed here** because Flask-SQLAlchemy/Flask-Mail are not available in this environment. Run `python -m pytest -q` after installing `requirements.txt` in a disposable environment. Do not treat this package as production-validated until those tests and a two-device scan test pass.

## Render launch checklist

1. Take a disk/database snapshot of `/data/invotra.db` before deployment; do not remove or reinitialize the disk.
2. Deploy the full project to your existing service; keep your existing Render secrets and `bash start.sh` command.
3. Run `python -m pytest -q` in a staging/test environment, not against production data.
4. Test 0.25/0.50/0.75/1.00 scans for each and case UPCs; test a missing item at 0.75, then approval/pricing.
5. Use two Zebra devices simultaneously on the same subunit with distinct user logins and verify totals are isolated.
6. Test filtering users in Super Admin, and filtering the approval queue as a campus admin.
7. Submit a test inventory, verify counter and parent admins are emailed; approve it and verify assigned subunit users receive GL summary + CSV, while unrelated campuses receive nothing.
