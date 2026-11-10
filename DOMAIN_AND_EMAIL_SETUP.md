# Invotra — public website, application subdomain, and email routing

## Recommended architecture

| URL | Role | Render service | Storage |
| --- | --- | --- | --- |
| https://invotra.us | Public marketing website | **New Static Site** serving `website/index.html` | None |
| https://dashboard.invotra.us | Existing Flask inventory application | **Existing Web Service** | Keep its existing `/data` disk |

Do **not** create a new Flask web service or move your database. The public website is the exact HTML preview supplied. Its buttons already point to `https://dashboard.invotra.us/`.

## Migration steps: protect the existing live site

1. Deploy the updated Flask project to the **existing** Render Web Service. Keep its existing disk, `DATABASE_URL` policy, `SECRET_KEY`, and Gmail environment settings. Back up `/data/invotra.db` before deploying. Add environment variable `PUBLIC_APP_URL=https://dashboard.invotra.us`.
2. In that existing Web Service, go to **Settings → Custom Domains → Add Custom Domain** and add `dashboard.invotra.us`. At your domain registrar/DNS host, create a `CNAME` record: **Host** `dashboard`, **Value** your exact existing Web Service hostname, e.g. `your-service.onrender.com` (copy it from Render). Verify the new custom domain in Render.
3. Confirm `https://dashboard.invotra.us/login`, inventory pages, and password reset work and have valid HTTPS. **Do not remove `invotra.us` from the Web Service until this works.**
4. Create a **new Render Static Site** from the same Git repository: set **Root Directory** `website`, **Publish Directory** `.`, **Build Command** blank or `echo "Static Invotra website"`. Preview the static site's assigned `*.onrender.com` hostname before changing DNS.
5. Remove `invotra.us` and the automatically paired `www.invotra.us` from the old Web Service's custom domains. Add `invotra.us` on the new Static Site's Settings → Custom Domains. The corresponding `www` redirect is managed by Render. Follow the exact record values shown by Render for the static site. For root domains, use an `ANAME`/`ALIAS` to your static site's `onrender.com` hostname when your DNS provider supports it, or Render's documented apex `A` target (`216.24.57.1`) where appropriate. Do not modify MX or TXT email records.
6. Verify `https://invotra.us/` serves the public site and its Dashboard link reaches `dashboard.invotra.us`; also verify `www.invotra.us` redirects properly. There may be a short DNS transition when moving `invotra.us` between Render services. Keep the app available at the new subdomain first.

**Never** point the root domain and the dashboard CNAME to different random Render instances. The public Static Site is separate and does not hold inventory data.

## Gmail notification checks

In **existing Web Service → Environment**, verify:

```
PUBLIC_APP_URL=https://dashboard.invotra.us
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_USERNAME=sodexo.leardistrict@gmail.com
MAIL_PASSWORD=<your 16-character Google app password>
MAIL_DEFAULT_SENDER=sodexo.leardistrict@gmail.com
MAIL_SUPPRESS_SEND=false
```

Keep the password only in Render's secret environment variable controls; **never commit it to Git**. The account must support Google app passwords.

### Message behavior

- **New account created/approved:** recipient gets their login link and one-time password; forced password change remains enabled.
- **Inventory submitted:** counter gets a confirmation; parent-location admins get a direct hyperlink to review/approve. Optional per-parent recipients remain supported. An item-count CSV is attached to the administrators’ **submission/review email**, as requested previously; final approval summaries have no attachment.
- **Inventory approved:** only active parent admins and additional approval recipients for that parent receive a **GL-code total summary** and a direct link to the report. No item-level inventory CSV is attached. Staff do not get the financial summary by default.
- **Super Admin → Notifications → Email message templates:** edit the subject and body of the invitation, submission and approval messages. Placeholders are listed in the defaults. These templates are stored in a newly created `email_template` database table, retaining your existing data.

### How to confirm delivery

1. Create or select a parent location with an **active admin assigned to at least one subunit** of the parent. If there are no active parent admins, the app logs that there are no recipients and cannot send that location's admin summary.
2. Click **Send test email** to check Gmail SMTP, then **Send sample approved summary** to send the latest approved inventory's GL-only summary **to yourself only**.
3. Check **Super Admin → Notifications → Recent delivery attempts** and Render logs. `sent` means Gmail's SMTP server accepted the message; it does **not** guarantee inbox delivery. Check spam, recipient validity, and corporate spam filtering.
4. Run an end-to-end inventory: submit → verify approval link → approve → verify correct parent admins receive the GL summary. Delivery errors are recorded in `email_delivery`.

`PUBLIC_APP_URL` must be just the origin, without `/login` or a trailing path.

## Test and caveats

- Python and Jinja syntax can be checked locally; live Render/SMTP delivery needs deployment.
- The deployment does not move or replace the persistent inventory database.
- Existing account invitations still include temporary passwords in email; forcing the first-login password change reduces but does not eliminate the security risk of sending passwords by email. A later invite-link-only flow would be stronger.
