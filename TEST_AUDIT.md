# Invotra launch audit — November 3, 2026

## Source
Full rebuild package from April 2026 (`invotra_full_rebuild.zip`). The existing production database and live Render service were not accessible in this session.

## Defects fixed in this release candidate

| Area | Earlier risk | Repair |
| --- | --- | --- |
| Database seed | Deleted GLs were reactivated every boot | Defaults created only for brand-new companies or an explicit reset |
| Inventory totals | Relationship caching could leave totals stale | Recompute with SQL SUM after flush, per session |
| Multiple counters | Risk of mixed counts/overwrites | Session ID scoped to user/location, serialized SQLite request handling for pilot |
| Missing item | Duplicate unknown scans generated multiple proposals | Pending unknown re-scan increments the existing line |
| Missing item pricing | $0 placeholder could become approved total | Final approval blocks on unresolved missing items |
| Missing item review | Corrected GL/cost not guaranteed in inventory | Admin approval updates the line snapshot and session total before final approval |
| Historical data | Changing master could affect valuation | Snapshot unit cost, GL label, extended value retained on inventory lines |
| User deletion | Unsafe hard deletes can break foreign keys | Archive account and remove access without removing audit history |
| GL deletion | Hard deletion risks references | Deactivate GL, retain historical links, hide inactive from active controls |
| Login security | Published default credentials / weak secret | Rotate legacy default on launch, require strong secret in production, force initial password reset |
| Password recovery | Emailed temporary passwords | 30-minute single-use reset URL |
| Forms | Cross-site request forgery risk | Session-bound CSRF token on all POST forms |
| Reports | Current-only view / missing latest summaries | All accessible inventory history, per-location latest approved GL totals, CSV export |
| Email delivery | Silent placeholder/logging only | Flask-Mail transport, parent admin/user routing, CSV attachments, delivery status page |
| Data loss on deploy | Ephemeral SQLite fallback / no backup | Require /data for production SQLite, pre-start backup and schema preflight |
| Admin workflow | Account requests and approvals missing | Restored scoped account requests, approval panel, subunit requests |
| UX | Missing item panel persistent | Show only after unknown scan; allow quantity adjustments while active |

## Checks executed in this environment

- Python compile/AST validation: passed (`app.py`, startup script, tests)
- Jinja2 compilation of **15** templates: passed
- All referenced template endpoint names checked against **29** route definitions: passed
- CSRF field coverage of POST forms: checked
- SQLite preflight backup and schema-mismatch tests: **2 passed**

## Tests supplied but NOT executed here

`tests/test_workflows.py` contains integration scenarios for initial password change, simultaneous inventory sessions, each/case costs, unknown item review, submit/approval emails, CSV outputs, immutable financial snapshots, master import errors, archive/deactivate actions, role boundaries, account requests, and CSRF. These require Flask runtime dependencies, which could not be installed in the current offline runtime. Run `python -m pytest -q tests` in your GitHub Actions environment or on a machine that can install the `requirements.txt` dependencies.

## Outside this test environment

The following require live acceptance testing before staff use:

1. Existing production database compatibility, history retention and Render disk mount `/data`
2. Actual Gmail SMTP authentication, delivery to Sodexo email addresses and CSV attachments
3. Zebra DataWedge keyboard wedge and scanner Enter-suffix behavior
4. User/admin login and role assignments on the live domain
5. Simultaneous real-world use across multiple Zebra devices and unstable Wi-Fi

**Launch recommendation:** Use a test company and subunit, get the automated workflow suite green, complete the live checklist in README, and only then open Invotra to your full staff. Do not delete/recreate an existing disk in response to an upgrade issue.
