# Invotra missing `user.phone` startup fix

This release adds an application-initialization compatibility migration for legacy SQLite databases. It identifies the *actual* database configured in SQLAlchemy, creates a SQLite backup alongside it in a `backups/` directory, and adds the missing nullable `user.phone` column before the first user query. No existing records are deleted or rewritten. It only fixes this specific column; any further missing legacy columns require separate review.

## Deploy

1. Back up the existing Render `/data` persistent disk if possible. Do not delete it.
2. Upload the included **full project** to the repo root (not an extra subfolder) and commit/push.
3. Use start command `bash start.sh` in Render. If Render uses `gunicorn app:app`, the app-level migration still covers this column.
4. If using a persistent SQLite database, remove an unwanted `DATABASE_URL` override or make sure it points to the intended persistent SQLite file.
5. Redeploy and watch Render logs for `Invotra migration applied to ...: added user.phone` or for the preflight's `Invotra migration: added nullable user.phone`.
6. Confirm login and user profile; then run the end-to-end inventory tests.

Do not change the persistent disk's mount path (`/data`), delete the DB, or create a fresh service just to clear this error.

If the error persists, copy the startup logs **from the beginning**, including any `Invotra preflight` / `Invotra migration` lines, and share the exact Render start command (redact secrets).
