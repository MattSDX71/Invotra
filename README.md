# Invotra — test-launch release candidate

**Release candidate:** 2026-11-03  
**Starting point:** `invotra_full_rebuild.zip` (April 2026)  
**Platform:** Flask web app, Render, SQLite on persistent `/data` disk, Zebra scanner (DataWedge keyboard wedge)

This is a **test-launch candidate**, not an externally verified production deployment. The Python and Jinja templates compile, the routes have been cross-checked, and the database backup/preflight script passes isolated tests. Full Flask integration tests are included but must be run in a Python environment where dependencies can be installed before opening it to staff.

## 1. Before updating your EXISTING Render service

1. **Back up `/data/invotra.db` before changing anything**. The included `scripts/db_preflight.py` creates an automatic SQLite snapshot under `/data/backups/` at every start, retaining the five most recent snapshots. You can also make an independent off-server copy from the Render shell.
2. **Deploy into the existing Render web service**, with its existing persistent disk still mounted at exactly `/data`. Do **not** deploy a brand-new service with a new empty disk if you intend to keep the existing accounts and inventory history.
3. Replace the full application folder/repository, including `app.py`, `templates/`, `static/`, `scripts/`, `start.sh`, and `requirements.txt`. Do not mix templates from older patch versions.
4. Configure the build command as `pip install -r requirements.txt`, and the start command as `bash start.sh`.
5. Review Render > Environment variables, as below. If using the attached SQLite disk, **remove `DATABASE_URL`** unless you intentionally use a persistent PostgreSQL database. The app now refuses to use an ephemeral SQLite database in production.
6. The first launch creates **new tables only** (e.g., password-reset tokens, notification routing, email logs). It does **not** erase or replace existing tables. If the existing database has an incompatible older schema, preflight stops safely and reports the missing tables/columns. Do not delete the disk or your database to clear that error; a specific migration will be needed.

## 2. Render environment variables

| Key | Value / instructions |
| --- | --- |
| `INVOTRA_ENV` | `production` |
| `SECRET_KEY` | At least 32 random characters; keep this stable across deploys |
| `BOOTSTRAP_ADMIN_EMAIL` | Your real admin email (required for a new DB and insecure legacy-admin rotation) |
| `BOOTSTRAP_ADMIN_PASSWORD` | Strong unique password of at least 12 characters (required for new DB/legacy rotation) |
| `MAIL_SERVER` | `smtp.gmail.com` |
| `MAIL_PORT` | `587` |
| `MAIL_USE_TLS` | `true` |
| `MAIL_USE_SSL` | `false` |
| `MAIL_USERNAME` | Your Gmail account |
| `MAIL_PASSWORD` | Google app password, **not** your regular Gmail password |
| `MAIL_DEFAULT_SENDER` | Same Gmail account as `MAIL_USERNAME` |
| `MAIL_SUPPRESS_SEND` | `false` |
| `INVOTRA_SEED_DEMO` | `false` for live/test launch on real data |
| `DATABASE_URL` | **Unset** if you use `/data/invotra.db`; otherwise persistent PostgreSQL URL |

### Security note about the old default login

Previous test builds published `admin@invotra.local` / `ChangeMe123!`. If this password still exists, the application **requires you to rotate it** using the two `BOOTSTRAP_ADMIN_*` variables on production startup and forces a fresh change on login. Existing users whose passwords have already been changed are retained unchanged. **Do not distribute the old default login**.

## 3. What was repaired and retained

- Company → parent location → subunit hierarchy, with company-scoped master catalogs and GL accounts
- Super admin create companies, parent locations, subunits, users, and assignments
- Selecting a parent location visibly checks all the subunits when creating a user
- Admin-submitted account requests restricted to their own subunits; super-admin approval
- Admin new-subunit requests with super-admin email notification and approval
- Zebra keyboard-wedge scan input: each and case UPCs use different costs
- Per-user inventory sessions: multiple people can work independently in the same store
- Repeated scans increment counts; staff can correct an in-progress quantity or set it to zero
- Unknown UPC prompts only then for name and suggested company GL; scanning it again adds another count
- Admin missing-item review sets item name, one or both UPCs, costs, and correct GL
- Unknown items begin at $0 until priced; **inventory cannot receive final approval while unknown items remain pending**
- Submitted, rejected and approved sessions remain in historical reports with CSV exports
- Submitted user email, parent-location admins' review email (with CSV attachment), and final GL-summary approval email (with CSV attachment)
- Extra email recipients configurable by super admin, SMTP test button, recent delivery status
- GL accounts can be hidden from active selection without damaging audit history; deleting a GL no longer gets reversed by app startup
- Super admin can archive any other user and revoke assignments without losing historical sessions
- Profile phone/name and password updates; forced password change after first login; secure single-use password reset links
- CSRF tokens on state-changing forms, safer cookies, login rate limiting, production-only secret requirements
- Data persistence via mounted `/data/invotra.db`, with startup SQLite backup and compatibility check
- New `/healthz` endpoint (database connectivity)

## 4. Master-item CSV layout

Upload from **Super Admin → Upload Master Items**. All six header names must match exactly:

```csv
Item Name,Each UPC Code,Each Cost,Case UPC Code,Case Cost,GL Account
Coca Cola Cherry 20 oz,049000123456,1.55,00049000123456,29.50,BEVERAGE - 60110020
Candy Bag,012345678900,0.80,,,MDSE CANDY - 60270170
Paper Towels,,,987654321000,21.00,PAPER/PLASTIC N/T - 60260001
```

At least one UPC is required; each or case information may be blank. UPCs must be unique within a company, including across each/case fields. **Format UPC columns as text in Excel to preserve leading zeros.** Imports validate every row before changing any item; a bad row rejects the whole file instead of silently skipping products. A master change never re-prices a previously approved inventory.

## 5. Tests

For a machine or CI runner with internet access:

```bash
python -m pip install -r requirements.txt
python -m pytest -q tests
```

The included `.github/workflows/test.yml` executes these tests on GitHub pushes and pull requests. They use a disposable test DB and mock email transport; **they do not send real Gmail messages or edit your Render database**.

Also review these live smoke checks **in a test company/subunit**:

- [ ] Open `/healthz`: HTTP 200 with `{"status":"ok"}`
- [ ] Log in as super admin and open all management sections
- [ ] Create a parent + two subunits; create an admin and two users with distinct location assignments
- [ ] Select a parent when creating a user; verify all subunit checkboxes visibly check
- [ ] Upload a small company master CSV; download and verify its UPCs/costs/GLs
- [ ] Open **two users'** inventories at the same time in the same subunit and scan each + case UPC; counts/totals should remain separate
- [ ] Scan an unknown UPC: popup should request **only name + suggested GL**; scan twice to make quantity 2
- [ ] Submit inventory; staff gets confirmation, parent admin gets approval request and **CSV attachment**
- [ ] Attempt to approve with pending unknown item: should be blocked
- [ ] Review unknown item and set price/UPC; verify corrected GL total and final inventory value
- [ ] Approve: both user + parent admin get GL summary plus count CSV
- [ ] Download submitted/approved report history and verify old counts survive master item updates
- [ ] Archive a test user; ensure they cannot log in but old reports remain accessible
- [ ] Remove a test GL, redeploy, and verify it stays hidden
- [ ] Redeploy to the **same Render service** and verify users, sessions, reports, and master items survive
- [ ] Test a password reset link (expires after 30 minutes; one use)
- [ ] Test SMTP with the Super Admin **Send Test Email** control and check delivery history

## 6. Limits and launch cautions

- **This is still a browser app**, not yet a native Android APK. On Zebra scanners use DataWedge keystroke output to the UPC field and configure an Enter suffix. Test on your actual scanner hardware.
- **Network access is required**. Offline counting and automatic retry queues are not implemented.
- SQLite with one Gunicorn worker is appropriate for an initial small pilot on one Render disk. For multiple app instances, substantial concurrent usage, or branch-wide rollout, use PostgreSQL and load-test transactions first.
- SMTP delivery is synchronous. A failed SMTP call does not erase inventory; the app logs failure and tells the user. For a larger rollout, add an outbox/retry worker.
- Existing DBs older than the April 2026 Invotra rebuild may need a specific migration. The preflight checker **stops instead of risking a destructive or ambiguous conversion**.
- These tests don't replace a security review (e.g. network configuration, backup retention, incident response), physical Zebra testing, or a live Render/Gmail acceptance test.

## Support files

- `scripts/db_preflight.py`: schema compatibility check and safe SQLite backup
- `tests/test_preflight.py`: data-protection test (runs with stdlib + pytest)
- `tests/test_workflows.py`: login/inventory/approval/report/permission test suite (needs Flask deps)
- `master_sheet_sample.csv`: starter master with both each/case and single-pack examples
- `.env.example`: environment-variable checklist

Dependency note: Flask is pinned to 3.1.3 to include an upstream session/cache header security fix available as of this release.

## Public website and dashboard subdomain
See [DOMAIN_AND_EMAIL_SETUP.md](DOMAIN_AND_EMAIL_SETUP.md). The supplied public preview is in `website/index.html` and should be deployed as its own Render Static Site; the Flask Web Service stays connected to its `/data` disk on `dashboard.invotra.us`. Account and approval notification wording is editable in Super Admin → Notifications.
