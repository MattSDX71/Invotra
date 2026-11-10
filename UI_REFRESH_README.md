# Invotra – Corporate UI Refresh (November 2026)

This is a **presentation-only release** based on the last Mobile Counting & Location Updates project. It keeps the current `app.py`, database models, inventory rules, mail routing and migrations unchanged.

## Improvements

- New responsive style system in `static/invotra-ui.css`: navy-and-orange identity retained, more consistent typography, spacing, forms and tables.
- Desktop: fixed left navigation, clear active section, compact account dropdown, readable table layouts.
- Mobile: slide-out navigation instead of a tall full-width sidebar; tap-friendly controls and an overlay; scanning page stays compact.
- Super Admin: five focused, keyboard-accessible tabs: Organization, People & Access, Catalog & GL, Notifications, Requests.
- Organization directory displays **Company → Parent Location → Subunits** in expandable groups, followed by clear three-step creation forms.
- Removed the **Assign Existing User** UI. The existing backend action remains untouched for compatibility; other account creation/access and archive controls are retained.
- User Directory and email-routing filters submit the same GET routes as before but preserve scroll position and the active section. Admin parent filtering uses the same behavior. No longer uses an inline `this.form.submit()` that navigates without restoring position.
- Create User parent checkbox selects all its subunits, and updates the parent checkbox when individual subunits are toggled.
- Account dropdown now opens on click and closes on outside click or Escape, preventing the old hover gap problem.
- Dashboard, Admin, and login layouts updated to match the same design system.

## Install on Render

1. Back up your `/data/invotra.db` before any deployment.
2. Deploy this project to your **existing Render service** and keep its existing disk and environment variables. Do not recreate the service or reset its database.
3. Keep your current build/start commands (the package includes `render.yaml` and `start.sh`).
4. Test Super Admin → People & Access parent filter while scrolled down: the page should return to the same section and maintain scroll rather than jump to the top.
5. Test notification parent selection, Admin parent filtering, profile dropdown and the hamburger menu on mobile.
6. Confirm a known and unknown barcode scan, fractional quantity counting, account creation, upload/download master CSV, and approval email routing as part of the launch checklist.

## Validation

- All HTML templates parsed with Jinja2.
- Python files compiled.
- `tests/test_ui_contract.py` runs static template, navigation and feature-retention checks.
- This environment does not have Flask installed, so full route/integration/Zebra browser tests were **not** run. These must be verified in staging before production launch.

## Important scope

This release changes the front-end templates and adds one CSS file, plus a small static test. It makes **no database migrations**, modifies no business logic, and does not delete any records. The old `assign_user` backend branch remains in `app.py` but is not exposed by the new interface.
