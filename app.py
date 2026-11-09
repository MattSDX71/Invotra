from __future__ import annotations

import csv
import io
import os
import secrets
import hashlib
import logging
import re
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Iterable, Optional
import sqlite3
from pathlib import Path
from datetime import timezone

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for, g
from flask_mail import Mail, Message
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, text, func, case
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from scripts.legacy_schema import migrate as migrate_legacy_schema


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def default_db_url() -> str:
    if os.path.isdir("/data"):
        return "sqlite:////data/invotra.db"
    if os.getenv("INVOTRA_ENV", "development").lower() == "production":
        raise RuntimeError("Render /data disk is not mounted. Stopping to prevent use of an ephemeral database.")
    return "sqlite:///invotra.db"


app = Flask(__name__)
IS_PRODUCTION = os.getenv("INVOTRA_ENV", "development").lower() == "production"
if IS_PRODUCTION and (not os.getenv("SECRET_KEY") or len(os.getenv("SECRET_KEY", "")) < 32):
    raise RuntimeError("Set a random SECRET_KEY of at least 32 characters in Render before launching Invotra.")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "local-development-only-not-for-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
db_override = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(db_override if db_override else default_db_url())
if IS_PRODUCTION and app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite") and not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:////data/"):
    raise RuntimeError("Production SQLite must be on /data. Remove an ephemeral DATABASE_URL or use persistent PostgreSQL.")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", os.getenv("MAIL_USERNAME", "sodexo.leardistrict@gmail.com"))
app.config["MAIL_SUPPRESS_SEND"] = os.getenv("MAIL_SUPPRESS_SEND", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 30}}

db = SQLAlchemy(app)
migrate = Migrate(app, db)
mail = Mail(app)


DEFAULT_GLS = [
    ("60110010", "BAKED GOODS"),
    ("60110020", "BEVERAGE"),
    ("60110030", "MILK & ICE CREAM"),
    ("60110040", "GROCERIES"),
    ("60110060", "MEAT EGGS & CHEESE"),
    ("60110070", "PRODUCE"),
    ("60220001", "CLEANING SUPPLIES"),
    ("60260001", "PAPER/PLASTIC N/T"),
    ("60270150", "MDSE SUNDRIES"),
    ("60270170", "MDSE CANDY"),
    ("60640001", "OFFICE SUP/EXP"),
]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="user")  # super_admin/admin/user
    status = db.Column(db.String(30), nullable=False, default="active")
    initial_password_reset_required = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True)
    active = db.Column(db.Boolean, default=True, nullable=False)


class ParentLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    company = db.relationship("Company", backref="parent_locations")


class SubLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parent_location.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    parent = db.relationship("ParentLocation", backref="sub_locations")

    @property
    def display_name(self) -> str:
        return f"{self.parent.company.name} / {self.parent.name} / {self.name}"


class UserSubLocationAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sub_location_id = db.Column(db.Integer, db.ForeignKey("sub_location.id"), nullable=False)
    user = db.relationship("User", backref="sub_location_access")
    sub_location = db.relationship("SubLocation", backref="user_access")
    __table_args__ = (db.UniqueConstraint("user_id", "sub_location_id", name="uq_user_sub_location"),)


class GLAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    code = db.Column(db.String(30), nullable=False)
    description = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    company = db.relationship("Company", backref="gl_accounts")
    __table_args__ = (db.UniqueConstraint("company_id", "code", name="uq_company_gl_code"),)

    @property
    def label(self) -> str:
        return f"{self.description} - {self.code}"


class ItemMaster(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    item_name = db.Column(db.String(255), nullable=False)
    each_upc = db.Column(db.String(100), index=True)
    each_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    case_upc = db.Column(db.String(100), index=True)
    case_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    company = db.relationship("Company", backref="items")
    gl_account = db.relationship("GLAccount")


class InventorySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sub_location_id = db.Column(db.Integer, db.ForeignKey("sub_location.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="in_progress")
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    rejection_note = db.Column(db.Text)
    total_quantity = db.Column(db.Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_value = db.Column(db.Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    sub_location = db.relationship("SubLocation")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])


class UnknownItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sub_location_id = db.Column(db.Integer, db.ForeignKey("sub_location.id"), nullable=False)
    scanned_upc = db.Column(db.String(100), nullable=False)
    proposed_name = db.Column(db.String(255), nullable=False)
    proposed_gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reviewed_at = db.Column(db.DateTime)
    converted_item_master_id = db.Column(db.Integer, db.ForeignKey("item_master.id"))
    session_ref = db.relationship("InventorySession", backref="unknown_items")
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_user_id])
    sub_location = db.relationship("SubLocation")
    proposed_gl_account = db.relationship("GLAccount")


class InventoryLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    item_master_id = db.Column(db.Integer, db.ForeignKey("item_master.id"))
    unknown_item_id = db.Column(db.Integer, db.ForeignKey("unknown_item.id"))
    scanned_upc = db.Column(db.String(100), nullable=False)
    scan_type = db.Column(db.String(20), default="each", nullable=False)  # each/case/unknown
    item_name_snapshot = db.Column(db.String(255), nullable=False)
    gl_account_id = db.Column(db.Integer, db.ForeignKey("gl_account.id"), nullable=False)
    gl_label_snapshot = db.Column(db.String(160), nullable=False)
    unit_cost_snapshot = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), default=Decimal("1.00"), nullable=False)
    extended_value = db.Column(db.Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    session_ref = db.relationship("InventorySession", backref="lines")
    item_master = db.relationship("ItemMaster")
    unknown_item = db.relationship("UnknownItem")
    gl_account = db.relationship("GLAccount")


class SubLocationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parent_location.id"), nullable=False)
    requested_name = db.Column(db.String(160), nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(30), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime)
    parent = db.relationship("ParentLocation")
    requested_by = db.relationship("User")


class PasswordResetToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)


class UserAccessRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    sub_location_id = db.Column(db.Integer, db.ForeignKey("sub_location.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    requested_by = db.relationship("User")
    sub_location = db.relationship("SubLocation")


class NotificationRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(40), nullable=False, unique=True)
    extra_recipients = db.Column(db.Text, nullable=False, default="")


class ParentNotificationRule(db.Model):
    # Optional extra recipients limited to one parent campus. Never send across companies.
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parent_location.id"), nullable=False)
    event = db.Column(db.String(40), nullable=False)
    extra_recipients = db.Column(db.Text, nullable=False, default="")
    __table_args__ = (db.UniqueConstraint("parent_id", "event", name="uq_parent_notification_event"),)


class LoginAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    identifier_hash = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class EmailDelivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(60), nullable=False)
    recipients = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(250), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ---------- helpers ----------
def money(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def parse_cost(raw, label="Cost") -> Decimal:
    raw = str(raw or "0").strip().replace("$", "").replace(",", "")
    try:
        val = Decimal(raw).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        raise ValueError(f"{label} must be a valid dollar amount")
    if not val.is_finite() or val < 0 or val > Decimal("99999999.99"):
        raise ValueError(f"{label} must be a nonnegative dollar amount")
    return val


def form_int(key: str) -> int:
    try:
        val = int(request.form.get(key, ""))
        if val < 1:
            raise ValueError()
        return val
    except (TypeError, ValueError):
        abort(400, description=f"Invalid {key}")


def unique_upcs(company_id: int, each_upc: str | None, case_upc: str | None,
                excluding_item_id: int | None = None) -> bool:
    if each_upc and case_upc and each_upc == case_upc:
        return False
    for code in (each_upc, case_upc):
        if not code:
            continue
        q = ItemMaster.query.filter(ItemMaster.company_id == company_id,
                 or_(ItemMaster.each_upc == code, ItemMaster.case_upc == code))
        if excluding_item_id is not None:
            q = q.filter(ItemMaster.id != excluding_item_id)
        if q.first():
            return False
    return True


def extra_recipients(event: str) -> list[str]:
    rule = NotificationRule.query.filter_by(event=event).first()
    if not rule:
        return []
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", rule.extra_recipients or "")))


def current_user() -> Optional[User]:
    uid = session.get("user_id")
    if not uid:
        return None
    user = db.session.get(User, uid)
    if not user or user.status != "active":
        session.clear()
        return None
    return user

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in.", "error")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user or user.role not in roles:
                flash("You do not have access to that page.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.context_processor
def inject_user():
    return {"user": current_user(), "now": datetime.utcnow()}


def user_sub_ids(user: User) -> list[int]:
    if user.role == "super_admin":
        return [s.id for s in SubLocation.query.filter_by(active=True).all()]
    return [a.sub_location_id for a in UserSubLocationAccess.query.filter_by(user_id=user.id).all()]


def can_access_sub(user: User, sub_location_id: int) -> bool:
    return user.role == "super_admin" or sub_location_id in user_sub_ids(user)


def parent_admins(parent_id: int) -> list[User]:
    sub_ids = [s.id for s in SubLocation.query.filter_by(parent_id=parent_id, active=True).all()]
    if not sub_ids:
        return []
    users = (User.query.join(UserSubLocationAccess, User.id == UserSubLocationAccess.user_id)
             .filter(User.role == "admin", User.status == "active", UserSubLocationAccess.sub_location_id.in_(sub_ids))
             .distinct().all())
    return users


def location_report_recipients(sub_location_id: int) -> list[str]:
    """All active assigned staff at the counted subunit, plus parent admins."""
    sub = db.session.get(SubLocation, sub_location_id)
    if not sub:
        return []
    assigned = (User.query.join(UserSubLocationAccess, UserSubLocationAccess.user_id == User.id)
                .filter(UserSubLocationAccess.sub_location_id == sub_location_id,
                        User.status == "active").distinct().all())
    return sorted({u.email for u in [*assigned, *parent_admins(sub.parent_id)]})


def parent_notification_recipients(parent_id: int, event: str) -> list[str]:
    rule = ParentNotificationRule.query.filter_by(parent_id=parent_id, event=event).first()
    if not rule:
        return []
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                                 rule.extra_recipients or "")))


def scan_quantity(raw: str) -> Decimal:
    """Constrain scan increments to four quarter-unit choices."""
    try:
        value = Decimal(str(raw or "1"))
    except InvalidOperation as exc:
        raise ValueError("Choose a valid scan amount.") from exc
    if value not in (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00")):
        raise ValueError("Choose ¼, ½, ¾, or 1 unit per scan.")
    return value


def edit_quantity(raw: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("Invalid quantity") from exc
    if not value.is_finite() or value < 0 or value > 100000 or value * 4 != (value * 4).to_integral_value():
        raise ValueError("Use increments of 0.25 between 0 and 100000")
    return value


def company_super_admins() -> list[User]:
    return User.query.filter_by(role="super_admin", status="active").all()


def send_email(to: str | Iterable[str], subject: str, body: str,
               attachments: list[tuple[str, str, bytes]] | None = None, event="general") -> bool:
    recipients = [to] if isinstance(to, str) else [r for r in to if r]
    recipients = sorted(set(r.strip() for r in recipients if r and r.strip()))
    if not recipients:
        return False
    ok = False
    error = None
    try:
        if not app.config.get("MAIL_SERVER"):
            raise RuntimeError("MAIL_SERVER is not configured")
        msg = Message(subject=subject, recipients=recipients[:1], bcc=recipients[1:], body=body,
                      sender=app.config.get("MAIL_DEFAULT_SENDER"))
        for filename, content_type, data in attachments or []:
            msg.attach(filename, content_type, data)
        mail.send(msg)
        ok = True
        app.logger.info("Invotra email delivered: event=%s recipients=%s", event, ",".join(recipients))
    except Exception as exc:
        error = str(exc)[:500]
        app.logger.error("Invotra email failed: event=%s error=%s", event, error)
    finally:
        try:
            db.session.add(EmailDelivery(event=event, recipients=", ".join(recipients),
                   subject=subject[:250], status="sent" if ok else "failed", error=error))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to save email delivery status")
    return ok

def session_csv_bytes(inv: InventorySession) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Session ID", "Location", "Status", "Item Name", "UPC Scanned", "Type", "GL Account", "Unit Cost", "Quantity", "Extended Value"])
    for line in inv.lines:
        writer.writerow([inv.id, inv.sub_location.display_name, inv.status, line.item_name_snapshot, line.scanned_upc, line.scan_type, line.gl_label_snapshot, line.unit_cost_snapshot, line.quantity, line.extended_value])
    return output.getvalue().encode("utf-8-sig")


def gl_totals(inv: InventorySession) -> list[tuple[str, Decimal]]:
    totals: dict[str, Decimal] = {}
    for line in inv.lines:
        totals.setdefault(line.gl_label_snapshot, Decimal("0.00"))
        totals[line.gl_label_snapshot] += Decimal(str(line.extended_value or 0))
    return sorted(totals.items(), key=lambda x: x[0])


def recalc_session(inv: InventorySession) -> None:
    # Query the database rather than an ORM relationship which might be cached before the new scan.
    db.session.flush()
    qty, val = db.session.query(func.coalesce(func.sum(InventoryLine.quantity), 0),
        func.coalesce(func.sum(InventoryLine.extended_value), 0)).filter(InventoryLine.session_id == inv.id).one()
    inv.total_quantity = Decimal(str(qty)).quantize(Decimal("0.01"))
    inv.total_value = Decimal(str(val)).quantize(Decimal("0.01"))

def find_item(company_id: int, upc: str) -> tuple[Optional[ItemMaster], Optional[str], Decimal]:
    item = ItemMaster.query.filter_by(company_id=company_id, each_upc=upc, active=True).first()
    if item:
        return item, "each", Decimal(str(item.each_cost or 0))
    item = ItemMaster.query.filter_by(company_id=company_id, case_upc=upc, active=True).first()
    if item:
        return item, "case", Decimal(str(item.case_cost or 0))
    return None, None, Decimal("0.00")


def locate_gl(company_id: int, raw: str) -> Optional[GLAccount]:
    value = (raw or "").strip()
    if not value:
        return None
    # Accept Description - Code, Code Description, code only, or description only.
    code_candidate = None
    desc_candidate = None
    if " - " in value:
        desc_candidate, code_candidate = [part.strip() for part in value.rsplit(" - ", 1)]
    else:
        parts = value.split(maxsplit=1)
        if parts and parts[0].isdigit():
            code_candidate = parts[0]
            desc_candidate = parts[1].strip() if len(parts) > 1 else None
        else:
            desc_candidate = value
    q = GLAccount.query.filter_by(company_id=company_id, active=True)
    if code_candidate:
        gl = q.filter(GLAccount.code == code_candidate).first()
        if gl:
            return gl
    if desc_candidate:
        return q.filter(GLAccount.description.ilike(desc_candidate)).first()
    return None


def seed_company_gls(company: Company) -> None:
    for code, desc in DEFAULT_GLS:
        gl = GLAccount.query.filter_by(company_id=company.id, code=code).first()
        if gl:
            gl.description = desc
            gl.active = True
        else:
            db.session.add(GLAccount(company_id=company.id, code=code, description=desc, active=True))


def ensure_legacy_user_phone_column() -> None:
    """Add one nullable column before any ORM queries against an older user table.

    Unlike a separate preflight script, this uses Flask-SQLAlchemy's *actual*
    configured database path, and runs even when Render was configured with
    `gunicorn app:app` instead of `bash start.sh`.

    It never recreates tables or alters existing user rows. A transactionally
    consistent database backup is written before making any schema change.
    """
    if db.engine.dialect.name != "sqlite":
        return  # PostgreSQL schema upgrades must use Alembic migrations.

    database_name = db.engine.url.database
    if not database_name:
        raise RuntimeError("Cannot determine SQLite database path for migration")
    database_path = Path(database_name).resolve()
    if not database_path.exists():
        return  # New install; create_all will create the phone field.

    with sqlite3.connect(str(database_path), timeout=30) as connection:
        user_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user'"
        ).fetchone()
        if not user_table:
            return
        fields = {row[1] for row in connection.execute('PRAGMA table_info("user")')}
        if "phone" in fields:
            return

        backup_dir = database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"invotra-before-user-phone-{stamp}.db"
        with sqlite3.connect(str(backup_path)) as backup:
            connection.backup(backup)
        app.logger.warning("Invotra legacy database backup: %s", backup_path)

        # SQLite DDL is idempotent with the metadata check above. If another
        # process migrated between inspection and ALTER, fail rather than
        # modifying something unexpected.
        connection.execute('ALTER TABLE "user" ADD COLUMN "phone" VARCHAR(50)')
        connection.commit()
        app.logger.warning("Invotra migration applied to %s: added user.phone", database_path)


def ensure_seed_data() -> None:
    # SQLAlchemy create_all only creates new tables; it never alters legacy tables.
    # Create hierarchy tables first, then migrate old location_id values with explicit
    # location mappings before issuing any ORM queries.
    ensure_legacy_user_phone_column()
    db.create_all()
    if db.engine.dialect.name == "sqlite":
        # Backup is taken by preflight (when start.sh is used). For direct gunicorn,
        # ensure a backup before changing historic records as well.
        dbpath = Path(db.engine.url.database).resolve()
        if dbpath.exists():
            backup_dir = dbpath.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            # Backup only for installations that require a legacy migration.
            with sqlite3.connect(str(dbpath)) as conn:
                fields = {r[1] for r in conn.execute('PRAGMA table_info("inventory_session")')}
                needs_migration = bool(fields) and 'sub_location_id' not in fields
            if needs_migration:
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                with sqlite3.connect(str(dbpath)) as src, sqlite3.connect(str(backup_dir / f'invotra-before-location-upgrade-{stamp}.db')) as dest:
                    src.backup(dest)
                app.logger.warning('Invotra backed up legacy database before hierarchy migration.')
            for operation in migrate_legacy_schema(dbpath):
                app.logger.warning('Invotra schema migration: %s', operation)
    # On a fresh install, bootstrap a super admin through Render secrets. Never install a public default password.
    if not User.query.first():
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if email and len(password) >= 12:
            admin = User(full_name="Invotra Super Admin", email=email, role="super_admin",
                         status="active", initial_password_reset_required=True)
            admin.set_password(password)
            db.session.add(admin)
        elif IS_PRODUCTION:
            raise RuntimeError("New database: set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD (12+ characters) in Render.")
    # Old v6 demo credentials were published in previous project builds. Rotate automatically
    # when the legacy password is still in use; never let a test launch expose it.
    legacy = User.query.filter_by(email="admin@invotra.local", role="super_admin", status="active").first()
    if legacy and legacy.check_password("ChangeMe123!"):
        replacement = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        new_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        if IS_PRODUCTION and (len(replacement) < 12 or not new_email):
            raise RuntimeError("Legacy insecure super-admin login detected. Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD in Render to rotate it safely.")
        if len(replacement) >= 12:
            if new_email and new_email != legacy.email and User.query.filter_by(email=new_email).first():
                raise RuntimeError("Cannot rotate legacy super admin to an email that already belongs to another user.")
            legacy.set_password(replacement)
            if new_email:
                legacy.email = new_email
            legacy.initial_password_reset_required = True
            app.logger.warning("Rotated insecure legacy super-admin credentials; a password change is required on login.")
    # Only seed demo hierarchy if explicitly opted in. Existing businesses/GLs are never reactivated.
    if os.getenv("INVOTRA_SEED_DEMO", "false").lower() == "true" and not Company.query.first():
        company = Company(name="Sodexo")
        db.session.add(company)
        db.session.flush()
        seed_company_gls(company)
        parent = ParentLocation(company_id=company.id, name="East Texas A&M University")
        db.session.add(parent)
        db.session.flush()
        db.session.add_all([SubLocation(parent_id=parent.id, name="C-Store"),
                            SubLocation(parent_id=parent.id, name="Dining Hall")])
    db.session.commit()


with app.app_context():
    ensure_seed_data()


@app.before_request
def protect_state_changing_requests():
    if request.endpoint == "static":
        return
    # token in session prevents cross-site writes (including account deletion and approvals)
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not app.config.get("TEST_DISABLE_CSRF", False):
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
            abort(400, description="Invalid form token. Refresh the page and try again.")
    user = current_user()
    if user and user.initial_password_reset_required and request.endpoint not in (
        "force_password_reset", "logout", "static"):
        return redirect(url_for("force_password_reset"))


@app.context_processor
def csrf_context():
    return {"csrf_token": session.get("csrf_token", "")}

# ---------- auth ----------
@app.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "unavailable"}, 503


@app.route("/")
def root():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        identity = hashlib.sha256(f"{request.remote_addr}|{email}".encode()).hexdigest()
        recent = datetime.utcnow() - timedelta(minutes=15)
        if LoginAttempt.query.filter_by(identifier_hash=identity).filter(LoginAttempt.created_at >= recent).count() >= 10:
            flash("Too many login attempts. Try again in 15 minutes.", "error")
            return redirect(url_for("login"))
        user = User.query.filter_by(email=email, status="active").first()
        if not user or not user.check_password(request.form.get("password", "")):
            db.session.add(LoginAttempt(identifier_hash=identity))
            db.session.commit()
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))
        LoginAttempt.query.filter_by(identifier_hash=identity).delete()
        db.session.commit()
        remember = bool(request.form.get("remember_me"))
        session.clear()
        session.permanent = remember
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("force_password_reset" if user.initial_password_reset_required else "dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


def valid_password(password: str) -> bool:
    return len(password) >= 12 and len(password) <= 128


@app.route("/force-password-reset", methods=["GET", "POST"])
@login_required
def force_password_reset():
    user = current_user()
    if request.method == "POST":
        password = request.form.get("password", "")
        if not valid_password(password) or password != request.form.get("confirm_password", ""):
            flash("Use a matching password of at least 12 characters.", "error")
            return redirect(url_for("force_password_reset"))
        user.set_password(password)
        user.initial_password_reset_required = False
        db.session.commit()
        flash("Password updated.", "success")
        return redirect(url_for("dashboard"))
    return render_template("force_password_reset.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower(), status="active").first()
        if user and PasswordResetToken.query.filter_by(user_id=user.id).filter(
                PasswordResetToken.expires_at >= datetime.utcnow() - timedelta(minutes=30)).count() < 3:
            token = secrets.token_urlsafe(32)
            record = PasswordResetToken(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                        expires_at=datetime.utcnow() + timedelta(minutes=30))
            db.session.add(record)
            db.session.commit()
            url = url_for("reset_password", token=token, _external=True)
            send_email(user.email, "Invotra password reset", f"Use this single-use password reset link (expires in 30 minutes):\n{url}")
        flash("If that account exists, a reset link has been sent.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    hashed = hashlib.sha256(token.encode()).hexdigest()
    record = PasswordResetToken.query.filter_by(token_hash=hashed, used_at=None).first()
    if not record or record.expires_at < datetime.utcnow():
        flash("That reset link has expired or was already used.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if not valid_password(password) or password != request.form.get("confirm_password", ""):
            flash("Use a matching password of at least 12 characters.", "error")
            return redirect(url_for("reset_password", token=token))
        user = db.session.get(User, record.user_id)
        if not user or user.status != "active":
            abort(404)
        user.set_password(password)
        user.initial_password_reset_required = False
        record.used_at = datetime.utcnow()
        db.session.commit()
        session.clear()
        flash("Password changed. You can sign in.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        user.full_name = request.form.get("full_name", user.full_name).strip() or user.full_name
        user.phone = request.form.get("phone", "").strip() or None
        new_password = request.form.get("new_password", "")
        if new_password:
            if not user.check_password(request.form.get("current_password", "")):
                flash("Enter your current password to change it.", "error")
                return redirect(url_for("profile"))
            if not valid_password(new_password) or new_password != request.form.get("confirm_password", ""):
                flash("Use a matching password of at least 12 characters.", "error")
                return redirect(url_for("profile"))
            user.set_password(new_password)
            user.initial_password_reset_required = False
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ---------- dashboard/inventory ----------
@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    active_sessions = InventorySession.query.filter_by(created_by_user_id=user.id, status="in_progress").order_by(InventorySession.started_at.desc()).all()
    recent_sessions = InventorySession.query.filter_by(created_by_user_id=user.id).order_by(InventorySession.started_at.desc()).limit(8).all()
    return render_template("dashboard.html", active_sessions=active_sessions, recent_sessions=recent_sessions)


@app.route("/inventory/start", methods=["GET", "POST"])
@login_required
def inventory_start():
    user = current_user()
    sub_locations = SubLocation.query.filter(SubLocation.id.in_(user_sub_ids(user)), SubLocation.active == True).all()
    if request.method == "POST":
        sub_id = form_int("sub_location_id")
        if not can_access_sub(user, sub_id) or not db.session.get(SubLocation, sub_id) or not db.session.get(SubLocation, sub_id).active:
            flash("You do not have access to that location.", "error")
            return redirect(url_for("inventory_start"))
        existing = InventorySession.query.filter_by(created_by_user_id=user.id, sub_location_id=sub_id, status="in_progress").first()
        if existing:
            return redirect(url_for("inventory_session", session_id=existing.id))
        inv = InventorySession(created_by_user_id=user.id, sub_location_id=sub_id)
        db.session.add(inv)
        db.session.commit()
        return redirect(url_for("inventory_session", session_id=inv.id))
    return render_template("inventory_start.html", sub_locations=sub_locations)


@app.route("/inventory/<int:session_id>")
@login_required
def inventory_session(session_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    if not inv or (inv.created_by_user_id != user.id and
        not (can_access_sub(user, inv.sub_location_id) and
             (user.role in ("admin", "super_admin") or inv.status == "approved"))):
        flash("Inventory session not found.", "error")
        return redirect(url_for("dashboard"))
    company_id = inv.sub_location.parent.company_id
    gl_accounts = GLAccount.query.filter_by(company_id=company_id, active=True).order_by(GLAccount.description).all()
    unknown_upc = request.args.get("unknown_upc")
    selected_scan_qty = request.args.get("scan_qty", "1")
    if selected_scan_qty not in ("0.25", "0.5", "0.75", "1"):
        selected_scan_qty = "1"
    return render_template("inventory_session.html", inv=inv, gl_totals=gl_totals(inv),
        gl_accounts=gl_accounts, unknown_upc=unknown_upc, selected_scan_qty=selected_scan_qty)


@app.route("/inventory/<int:session_id>/scan", methods=["POST"])
@login_required
def inventory_scan(session_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    if not inv or inv.created_by_user_id != user.id or inv.status != "in_progress":
        abort(403)
    upc = request.form.get("upc", "").strip()
    if not upc or len(upc) > 100:
        flash("Scan or enter a valid UPC.", "error")
        return redirect(url_for("inventory_session", session_id=session_id))
    try:
        increment = scan_quantity(request.form.get("scan_qty", "1"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory_session", session_id=session_id))
    scan_qty_url = str(increment.normalize())
    company_id = inv.sub_location.parent.company_id
    # When a pending unknown UPC is scanned again, increment the existing missing-item count.
    pending = (UnknownItem.query.filter_by(session_id=inv.id, scanned_upc=upc, status="pending")
               .first())
    if pending:
        line = InventoryLine.query.filter_by(unknown_item_id=pending.id, session_id=inv.id).first()
        if line:
            line.quantity += increment
            line.extended_value = line.unit_cost_snapshot * line.quantity
            recalc_session(inv)
            db.session.commit()
            flash("Missing item count increased. Admin will price it.", "success")
            return redirect(url_for("inventory_session", session_id=inv.id, scan_qty=scan_qty_url))
    item, scan_type, unit_cost = find_item(company_id, upc)
    if not item:
        return redirect(url_for("inventory_session", session_id=session_id, unknown_upc=upc, scan_qty=scan_qty_url))
    line = InventoryLine.query.filter_by(session_id=inv.id, scanned_upc=upc, scan_type=scan_type).first()
    if line:
        line.quantity += increment
        line.extended_value = Decimal(str(line.unit_cost_snapshot)) * line.quantity
    else:
        line = InventoryLine(session_id=inv.id, item_master_id=item.id, scanned_upc=upc,
            scan_type=scan_type, item_name_snapshot=item.item_name, gl_account_id=item.gl_account_id,
            gl_label_snapshot=item.gl_account.label, unit_cost_snapshot=unit_cost,
            quantity=increment, extended_value=unit_cost * increment)
        db.session.add(line)
    recalc_session(inv)
    db.session.commit()
    flash(f"Added {increment.normalize()} × {item.item_name} ({scan_type}).", "success")
    return redirect(url_for("inventory_session", session_id=session_id, scan_qty=scan_qty_url))


@app.route("/inventory/<int:session_id>/unknown", methods=["POST"])
@login_required
def inventory_unknown_submit(session_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    if not inv or inv.created_by_user_id != user.id or inv.status != "in_progress":
        abort(403)
    upc = request.form.get("scanned_upc", "").strip()
    try:
        increment = scan_quantity(request.form.get("scan_qty", "1"))
    except ValueError:
        abort(400)
    scan_qty_url = str(increment.normalize())
    name = request.form.get("proposed_name", "").strip()
    gl = db.session.get(GLAccount, form_int("proposed_gl_account_id"))
    company_id = inv.sub_location.parent.company_id
    if (not upc or len(upc)>100 or not name or len(name)>255 or not gl or not gl.active
        or gl.company_id != company_id):
        flash("Please enter a specific item name and a GL account from this company.", "error")
        return redirect(url_for("inventory_session", session_id=session_id, unknown_upc=upc))
    item, _, _ = find_item(company_id, upc)
    if item:
        flash("This barcode is already in the master list. Scan it again.", "error")
        return redirect(url_for("inventory_session", session_id=session_id))
    prior = UnknownItem.query.filter_by(session_id=inv.id, scanned_upc=upc, status="pending").first()
    if prior:
        flash("This missing item is already counted; scan it again to increase quantity.", "error")
        return redirect(url_for("inventory_session", session_id=session_id))
    unknown = UnknownItem(session_id=inv.id, submitted_by_user_id=user.id,
        sub_location_id=inv.sub_location_id, scanned_upc=upc,
        proposed_name=name, proposed_gl_account_id=gl.id)
    db.session.add(unknown)
    db.session.flush()
    db.session.add(InventoryLine(session_id=inv.id, unknown_item_id=unknown.id,
        scanned_upc=upc, scan_type="unknown", item_name_snapshot=name,
        gl_account_id=gl.id, gl_label_snapshot=gl.label, unit_cost_snapshot=Decimal("0.00"),
        quantity=increment, extended_value=Decimal("0.00")))
    recalc_session(inv)
    db.session.commit()
    flash("Missing item added and queued for admin pricing and approval.", "success")
    return redirect(url_for("inventory_session", session_id=session_id, scan_qty=scan_qty_url))

@app.route("/inventory/<int:session_id>/line/<int:line_id>", methods=["POST"])
@login_required
def edit_inventory_line(session_id, line_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    line = db.session.get(InventoryLine, line_id)
    if not inv or not line or line.session_id != inv.id or inv.created_by_user_id != user.id or inv.status != "in_progress":
        abort(403)
    try:
        quantity = edit_quantity(request.form.get("quantity", ""))
    except ValueError:
        abort(400)
    if quantity == 0:
        if line.unknown_item_id:
            unknown = db.session.get(UnknownItem, line.unknown_item_id)
            if unknown and unknown.status == "pending":
                unknown.status = "withdrawn"
        db.session.delete(line)
    else:
        line.quantity = quantity
        line.extended_value = Decimal(str(line.unit_cost_snapshot)) * quantity
    recalc_session(inv)
    db.session.commit()
    flash("Count adjusted.", "success")
    return redirect(url_for("inventory_session", session_id=session_id))


@app.route("/inventory/<int:session_id>/submit", methods=["POST"])
@login_required
def inventory_submit(session_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    if not inv or inv.created_by_user_id != user.id or inv.status != "in_progress":
        abort(403)
    if not inv.lines:
        flash("Scan at least one item before submitting.", "error")
        return redirect(url_for("inventory_session", session_id=session_id))
    inv.status = "submitted"
    inv.submitted_at = datetime.utcnow()
    recalc_session(inv)
    db.session.commit()
    attachment = [(f"inventory_session_{inv.id}.csv", "text/csv", session_csv_bytes(inv))]
    ok_user = send_email([user.email] + parent_notification_recipients(inv.sub_location.parent_id, "inventory_submitted_user"),
        f"Inventory submitted - {inv.sub_location.name}",
        f"Thank you for completing inventory for {inv.sub_location.display_name}. It has been sent for approval.",
        attachment, event="inventory_submitted_user")
    admins = parent_admins(inv.sub_location.parent_id)
    emails = [a.email for a in admins] + parent_notification_recipients(inv.sub_location.parent_id, "inventory_submitted_admin")
    ok_admin = send_email(emails, f"Inventory ready for approval - {inv.sub_location.display_name}",
        f"{user.full_name} has completed an inventory on {inv.submitted_at.strftime('%Y-%m-%d %H:%M UTC')}. "
        f"Please log in to approve the inventory.\n\nLocation: {inv.sub_location.display_name}\nSession: #{inv.id}\n"
        f"Items: {inv.total_quantity}\nPreliminary total: ${inv.total_value}\n\n"
        f"Attached: item counts CSV. Missing items require pricing before final approval.", attachment,
        event="inventory_submitted_admin") if emails else False
    if not ok_user or not ok_admin:
        flash("Inventory saved and submitted, but one or more emails could not be delivered. Ask your admin to check Invotra.", "error")
    else:
        flash("Inventory submitted for approval; notifications sent.", "success")
    return redirect(url_for("inventory_session", session_id=session_id))

# ---------- reports ----------
@app.route("/reports")
@login_required
def reports():
    user = current_user()
    sub_ids = user_sub_ids(user)
    sessions_q = InventorySession.query.filter(InventorySession.sub_location_id.in_(sub_ids))
    if user.role == "user":
        # Other users' unfinished sessions stay private. Approved reports are
        # visible to all active users assigned to that subunit.
        sessions_q = sessions_q.filter(or_(InventorySession.created_by_user_id == user.id,
                                           InventorySession.status == "approved"))
    sessions_q = sessions_q.order_by(InventorySession.started_at.desc())
    selected = request.args.get("sub_location_id", type=int)
    if selected and selected not in sub_ids:
        abort(403)
    if selected:
        sessions_q = sessions_q.filter_by(sub_location_id=selected)
    sessions = sessions_q.all()
    sub_locations = SubLocation.query.filter(SubLocation.id.in_(sub_ids)).order_by(SubLocation.name).all()
    latest_approved = {}
    for inv in sessions:
        if inv.status == "approved" and inv.sub_location_id not in latest_approved:
            latest_approved[inv.sub_location_id] = inv
    return render_template("reports.html", sessions=sessions, sub_locations=sub_locations,
                           selected=selected, latest_approved=list(latest_approved.values()), gl_totals=gl_totals)


# ---------- admin ----------
@app.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin", "super_admin")
def admin_dashboard():
    user = current_user()
    sub_ids = user_sub_ids(user)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "request_sub_location":
            parent_id = form_int("parent_id")
            name = request.form.get("requested_name", "").strip()
            parent = db.session.get(ParentLocation, parent_id)
            if not parent or not name or len(name) > 160 or not any(s.parent_id == parent_id for s in SubLocation.query.filter(SubLocation.id.in_(sub_ids)).all()):
                abort(403)
            if (SubLocation.query.filter_by(parent_id=parent_id, name=name).first() or
                SubLocationRequest.query.filter_by(parent_id=parent_id, requested_name=name, status="pending").first()):
                flash("This location already exists or is awaiting review.", "error")
            else:
                req = SubLocationRequest(parent_id=parent_id, requested_name=name, requested_by_id=user.id)
                db.session.add(req)
                db.session.commit()
                emails = [u.email for u in company_super_admins()] + extra_recipients("subunit_requested")
                sent = send_email(emails, "Invotra subunit request", f"{user.full_name} requested a new subunit under {req.parent.name}: {name}", event="subunit_requested")
                flash("Subunit request sent for approval." if sent else "Subunit request saved; email notification failed.", "success" if sent else "error")
        elif action == "request_user":
            sub_id = form_int("sub_location_id")
            if sub_id not in sub_ids:
                abort(403)
            name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            if not name or len(name)>150 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Provide a valid name and email address.", "error")
            elif User.query.filter_by(email=email).first():
                flash("That user already has an account. Ask a super admin to assign the location.", "error")
            else:
                db.session.add(UserAccessRequest(requested_by_id=user.id, full_name=name, email=email, sub_location_id=sub_id))
                db.session.commit()
                send_email([u.email for u in company_super_admins()], "Invotra account request",
                           f"{user.full_name} requested an account for {name} ({email}).", event="account_requested")
                flash("Account request sent to super admins.", "success")
        return redirect(url_for("admin_dashboard"))

    parent_locations = (ParentLocation.query.join(SubLocation, ParentLocation.id == SubLocation.parent_id)
        .filter(SubLocation.id.in_(sub_ids)).distinct().order_by(ParentLocation.name).all())
    parent_filter = request.args.get("parent_id", type=int)
    if parent_filter and parent_filter not in {p.id for p in parent_locations}:
        abort(403)
    if not parent_filter and parent_locations:
        parent_filter = parent_locations[0].id
    display_ids = ([sub.id for sub in SubLocation.query.filter(SubLocation.id.in_(sub_ids),
                    SubLocation.parent_id == parent_filter).all()] if parent_filter else sub_ids)
    pending_sessions = (InventorySession.query.filter(InventorySession.sub_location_id.in_(display_ids),
        InventorySession.status == "submitted").order_by(InventorySession.submitted_at.desc()).all())
    pending_unknowns = (UnknownItem.query.filter(UnknownItem.sub_location_id.in_(display_ids),
        UnknownItem.status == "pending").order_by(UnknownItem.id.desc()).all())
    users = (User.query.join(UserSubLocationAccess, User.id == UserSubLocationAccess.user_id)
        .filter(UserSubLocationAccess.sub_location_id.in_(display_ids), User.status == "active")
        .distinct().order_by(User.full_name).all())
    sub_locations = SubLocation.query.filter(SubLocation.id.in_(sub_ids), SubLocation.active == True).order_by(SubLocation.name).all()
    return render_template("admin_dashboard.html", parent_filter=parent_filter,
        display_sub_ids=set(display_ids), pending_sessions=pending_sessions,
        pending_unknowns=pending_unknowns, users=users, sub_locations=sub_locations, parent_locations=parent_locations)

@app.route("/admin/session/<int:session_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "super_admin")
def admin_review_session(session_id):
    user = current_user()
    inv = db.session.get(InventorySession, session_id)
    if not inv or not can_access_sub(user, inv.sub_location_id):
        abort(403)
    if request.method == "POST":
        if inv.status != "submitted":
            flash("Only submitted inventories can be reviewed.", "error")
            return redirect(url_for("admin_review_session", session_id=session_id))
        action = request.form.get("action")
        if action == "approve":
            if UnknownItem.query.filter_by(session_id=inv.id, status="pending").count():
                flash("Review all missing items before approving inventory.", "error")
                return redirect(url_for("admin_review_session", session_id=session_id))
            inv.status = "approved"
            inv.approved_at = datetime.utcnow()
            inv.approved_by_id = user.id
            recalc_session(inv)
            db.session.commit()
            body = (f"Inventory #{inv.id} approved.\nLocation: {inv.sub_location.display_name}\n"
                    f"Approved by: {user.full_name}\nTotal units: {inv.total_quantity}\n"
                    f"Total inventory value: ${inv.total_value}\n\nGL Summary\n")
            body += "\n".join(f"{gl}: ${total}" for gl, total in gl_totals(inv)) or "No GL totals available."
            recipients = (location_report_recipients(inv.sub_location_id) + [inv.created_by.email]
                          + parent_notification_recipients(inv.sub_location.parent_id, "inventory_approved"))
            sent = send_email(recipients, f"Inventory approved - {inv.sub_location.name}", body,
                [(f"inventory_session_{inv.id}.csv", "text/csv", session_csv_bytes(inv))], event="inventory_approved")
            flash("Inventory approved." + (" Email notification failed." if not sent else ""), "success" if sent else "error")
        elif action == "reject":
            inv.status = "rejected"
            inv.rejection_note = request.form.get("rejection_note", "").strip() or "No note provided"
            db.session.commit()
            send_email([inv.created_by.email] + parent_notification_recipients(inv.sub_location.parent_id, "inventory_rejected"),
                       f"Inventory rejected - {inv.sub_location.name}",
                       f"Inventory session #{inv.id} was rejected.\nReason: {inv.rejection_note}", event="inventory_rejected")
            flash("Inventory rejected; it remains available in history.", "success")
        else:
            abort(400)
        return redirect(url_for("admin_dashboard"))
    return render_template("review_session.html", inv=inv, gl_totals=gl_totals(inv),
        pending_unknowns=UnknownItem.query.filter_by(session_id=inv.id, status="pending").all())

@app.route("/admin/unknown/<int:unknown_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "super_admin")
def admin_review_unknown(unknown_id):
    user = current_user()
    unknown = db.session.get(UnknownItem, unknown_id)
    if not unknown or not can_access_sub(user, unknown.sub_location_id):
        abort(403)
    inv = unknown.session_ref
    company_id = unknown.sub_location.parent.company_id
    gl_accounts = GLAccount.query.filter_by(company_id=company_id, active=True).order_by(GLAccount.description).all()
    if request.method == "POST":
        if unknown.status != "pending" or inv.status not in ("in_progress", "submitted"):
            abort(409, description="Item is already reviewed or the inventory is locked")
        action = request.form.get("action")
        line = InventoryLine.query.filter_by(unknown_item_id=unknown.id).first()
        if action == "reject":
            unknown.status = "rejected"
            unknown.reviewed_by_id = user.id
            unknown.reviewed_at = datetime.utcnow()
            if line:
                db.session.delete(line)  # remove rejected item from financial total, preserve audit in UnknownItem
            recalc_session(inv)
            db.session.commit()
            flash("Missing item rejected and removed from the count.", "success")
            return redirect(url_for("admin_dashboard"))
        if action != "approve":
            abort(400)
        item_name = request.form.get("item_name", "").strip()
        each_upc = request.form.get("each_upc", "").strip() or None
        case_upc = request.form.get("case_upc", "").strip() or None
        gl = db.session.get(GLAccount, form_int("gl_account_id"))
        try:
            each_cost = parse_cost(request.form.get("each_cost"), "Each cost")
            case_cost = parse_cost(request.form.get("case_cost"), "Case cost")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin_review_unknown", unknown_id=unknown.id))
        if (not item_name or len(item_name) > 255 or not gl or not gl.active or gl.company_id != company_id or
            not (each_upc or case_upc) or not (unknown.scanned_upc == each_upc or unknown.scanned_upc == case_upc)):
            flash("Enter a valid name, company GL, and the scanned barcode as either each or case UPC.", "error")
            return redirect(url_for("admin_review_unknown", unknown_id=unknown.id))
        if not unique_upcs(company_id, each_upc, case_upc):
            existing, _, _ = find_item(company_id, unknown.scanned_upc)
            if existing:
                item = existing
            else:
                flash("Those UPCs already belong to another master item.", "error")
                return redirect(url_for("admin_review_unknown", unknown_id=unknown.id))
        else:
            item = ItemMaster(company_id=company_id, item_name=item_name,
                each_upc=each_upc, each_cost=each_cost, case_upc=case_upc,
                case_cost=case_cost, gl_account_id=gl.id)
            db.session.add(item)
            db.session.flush()
        unknown.status = "approved"
        unknown.reviewed_by_id = user.id
        unknown.reviewed_at = datetime.utcnow()
        unknown.converted_item_master_id = item.id
        if line:
            scan_type = "case" if unknown.scanned_upc == item.case_upc else "each"
            line.item_master_id = item.id
            line.item_name_snapshot = item.item_name
            line.gl_account_id = item.gl_account_id
            line.gl_label_snapshot = item.gl_account.label
            line.scan_type = scan_type
            line.unit_cost_snapshot = item.case_cost if scan_type == "case" else item.each_cost
            line.extended_value = line.unit_cost_snapshot * line.quantity
            recalc_session(inv)
        db.session.commit()
        flash("Missing item approved and priced in the inventory.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("review_unknown_item.html", unknown=unknown, gl_accounts=gl_accounts)

@app.route("/admin/remove-user-location", methods=["POST"])
@login_required
@role_required("admin", "super_admin")
def remove_user_location():
    manager = current_user()
    target = db.session.get(User, form_int("user_id"))
    sub_id = form_int("sub_location_id")
    if (not can_access_sub(manager, sub_id) or not target or
        (manager.role != "super_admin" and target.role != "user") or target.id == manager.id):
        abort(403)
    access = UserSubLocationAccess.query.filter_by(user_id=target.id, sub_location_id=sub_id).first()
    if access:
        db.session.delete(access)
        db.session.commit()
        flash("User removed from this location.", "success")
    return redirect(url_for("admin_dashboard"))

# ---------- super admin ----------
@app.route("/super-admin", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def super_admin_dashboard():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_company":
            name = request.form.get("company_name", "").strip()
            if not name or len(name)>160 or Company.query.filter(func.lower(Company.name) == name.lower()).first():
                flash("Enter a new unique company name.", "error")
            else:
                company = Company(name=name)
                db.session.add(company)
                db.session.flush()
                seed_company_gls(company)
                db.session.commit()
                flash("Company created with default GL accounts.", "success")
        elif action == "create_parent":
            company = db.session.get(Company, form_int("company_id"))
            name = request.form.get("parent_name", "").strip()
            if not company or not company.active or not name or len(name)>160 or ParentLocation.query.filter_by(company_id=company.id, name=name).first():
                flash("Choose an active company and a unique parent location name.", "error")
            else:
                db.session.add(ParentLocation(company_id=company.id, name=name))
                db.session.commit()
                flash("Parent location created.", "success")
        elif action == "create_sub":
            parent = db.session.get(ParentLocation, form_int("parent_id"))
            name = request.form.get("sub_name", "").strip()
            if not parent or not parent.active or not name or len(name)>160 or SubLocation.query.filter_by(parent_id=parent.id, name=name).first():
                flash("Choose an active parent and a unique subunit name.", "error")
            else:
                db.session.add(SubLocation(parent_id=parent.id, name=name))
                db.session.commit()
                flash("Subunit created.", "success")
        elif action == "create_gl":
            company = db.session.get(Company, form_int("company_id"))
            code = request.form.get("code", "").strip()
            description = request.form.get("description", "").strip().upper()
            if not company or not code or not description or len(code)>30 or len(description)>120:
                flash("Provide a company, GL code and description.", "error")
            else:
                gl = GLAccount.query.filter_by(company_id=company.id, code=code).first()
                if gl:
                    gl.active = True; gl.description = description
                else:
                    db.session.add(GLAccount(company_id=company.id, code=code, description=description))
                db.session.commit()
                flash("GL account saved.", "success")
        elif action == "reset_gls":
            company = db.session.get(Company, form_int("company_id"))
            if not company:
                abort(404)
            # Never delete GL records referenced by historical inventory or master items.
            for gl in GLAccount.query.filter_by(company_id=company.id):
                gl.active = False
            seed_company_gls(company)
            db.session.commit()
            flash("Default GL accounts restored; others hidden, with historical data preserved.", "success")
        elif action == "create_user":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("full_name", "").strip()
            role = request.form.get("role", "user")
            sub_ids = {int(x) for x in request.form.getlist("sub_location_ids") if x.isdecimal()}
            if not name or len(name)>150 or role not in ("user", "admin", "super_admin") or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Enter a valid user name, email and role.", "error")
            elif User.query.filter_by(email=email).first():
                flash("That email already exists.", "error")
            elif any(not db.session.get(SubLocation, sub_id) for sub_id in sub_ids):
                flash("Invalid location selection.", "error")
            else:
                temporary = secrets.token_urlsafe(15)
                u = User(full_name=name, email=email, phone=request.form.get("phone", "").strip() or None,
                    role=role, status="active", initial_password_reset_required=True)
                u.set_password(temporary)
                db.session.add(u)
                db.session.flush()
                for sub_id in sub_ids:
                    db.session.add(UserSubLocationAccess(user_id=u.id, sub_location_id=sub_id))
                db.session.commit()
                # Initial password delivered as requested; must be changed immediately after login.
                sent = send_email(u.email, "Your Invotra account is ready",
                    f"Welcome to Invotra.\nEmail: {u.email}\nOne-time temporary password: {temporary}\n"
                    f"Change it on first sign in.\nIf you did not expect this message, contact your administrator.", event="account_created")
                flash("Account created." + (" Welcome email failed; use password reset to activate." if not sent else " Welcome email sent."),
                      "success" if sent else "error")
        elif action == "assign_user":
            u = db.session.get(User, form_int("user_id"))
            sub = db.session.get(SubLocation, form_int("sub_location_id"))
            if not u or u.status != "active" or not sub or not sub.active:
                abort(400)
            if not UserSubLocationAccess.query.filter_by(user_id=u.id, sub_location_id=sub.id).first():
                db.session.add(UserSubLocationAccess(user_id=u.id, sub_location_id=sub.id))
                db.session.commit()
            flash("User assigned to location.", "success")
        elif action == "save_notifications":
            parent_id = form_int("notification_parent_id")
            if not db.session.get(ParentLocation, parent_id):
                abort(400)
            for event in ("inventory_submitted_user", "inventory_submitted_admin",
                          "inventory_approved", "inventory_rejected"):
                extra = request.form.get(event, "").strip()
                if len(extra) > 2000:
                    abort(400)
                rule = ParentNotificationRule.query.filter_by(parent_id=parent_id, event=event).first()
                if not rule:
                    rule = ParentNotificationRule(parent_id=parent_id, event=event)
                    db.session.add(rule)
                rule.extra_recipients = extra
            db.session.commit()
            flash("Email routing saved for this parent location.", "success")
        else:
            abort(400)
        if action == "save_notifications":
            return redirect(url_for("super_admin_dashboard", notification_parent_id=parent_id))
        return redirect(url_for("super_admin_dashboard"))
    companies = Company.query.order_by(Company.name).all()
    parents = ParentLocation.query.order_by(ParentLocation.name).all()
    subs = SubLocation.query.order_by(SubLocation.name).all()
    users = User.query.order_by(User.full_name).all()
    selected_parent_id = request.args.get("parent_id", type=int)
    if selected_parent_id and not any(p.id == selected_parent_id for p in parents):
        abort(404)
    if selected_parent_id:
        displayed_sub_ids = {sub.id for sub in subs if sub.parent_id == selected_parent_id}
        displayed_users = (User.query.join(UserSubLocationAccess, User.id == UserSubLocationAccess.user_id)
                           .filter(UserSubLocationAccess.sub_location_id.in_(displayed_sub_ids))
                           .distinct().order_by(User.full_name).all())
    else:
        displayed_sub_ids = set()
        displayed_users = []
    notification_parent_id = request.args.get("notification_parent_id", type=int)
    if notification_parent_id and not any(p.id == notification_parent_id for p in parents):
        abort(404)
    notification_rules = {r.event: r.extra_recipients for r in ParentNotificationRule.query.filter_by(parent_id=notification_parent_id).all()} if notification_parent_id else {}
    gls = GLAccount.query.filter_by(active=True).order_by(GLAccount.company_id, GLAccount.description).all()
    requests = SubLocationRequest.query.filter_by(status="pending").order_by(SubLocationRequest.created_at.desc()).all()
    account_requests = UserAccessRequest.query.filter_by(status="pending").order_by(UserAccessRequest.created_at.desc()).all()
    notifications = {r.event: r.extra_recipients for r in NotificationRule.query.all()}
    deliveries = EmailDelivery.query.order_by(EmailDelivery.id.desc()).limit(15).all()
    return render_template("super_admin_dashboard.html", companies=companies, parents=parents,
        subs=subs, users=users, gls=gls, requests=requests, account_requests=account_requests,
        notifications=notifications, deliveries=deliveries,
        displayed_users=displayed_users, displayed_sub_ids=displayed_sub_ids,
        selected_parent_id=selected_parent_id, notification_parent_id=notification_parent_id,
        notification_rules=notification_rules)

@app.route("/super-admin/delete-user/<int:user_id>", methods=["POST"])
@login_required
@role_required("super_admin")
def super_admin_delete_user(user_id):
    target = db.session.get(User, user_id)
    if not target or target.id == current_user().id or target.status != "active":
        abort(403)
    if target.role == "super_admin" and User.query.filter_by(role="super_admin", status="active").count() <= 1:
        abort(403)
    # Archive user, preserve signed inventory records and remove access immediately.
    UserSubLocationAccess.query.filter_by(user_id=target.id).delete()
    target.status = "archived"
    target.email = f"archived-user-{target.id}@invalid.invotra"
    db.session.commit()
    flash("User archived and access revoked. Inventory audit history remains intact.", "success")
    return redirect(url_for("super_admin_dashboard"))


@app.route("/super-admin/delete-gl/<int:gl_id>", methods=["POST"])
@login_required
@role_required("super_admin")
def super_admin_delete_gl(gl_id):
    gl = db.session.get(GLAccount, gl_id)
    if not gl:
        abort(404)
    # Deactivation is intentional: foreign keys and approved inventory history remain valid.
    gl.active = False
    db.session.commit()
    flash("GL account removed from active selection; historical records preserved.", "success")
    return redirect(url_for("super_admin_dashboard"))

@app.route("/super-admin/sub-request/<int:req_id>/<decision>", methods=["POST"])
@login_required
@role_required("super_admin")
def super_admin_sub_request(req_id, decision):
    req = db.session.get(SubLocationRequest, req_id)
    if not req or req.status != "pending" or decision not in ("approve", "reject"):
        abort(400)
    if decision == "approve":
        existing = SubLocation.query.filter_by(parent_id=req.parent_id, name=req.requested_name).first()
        if existing:
            flash("That subunit already exists; request rejected.", "error")
            req.status = "rejected"
        else:
            sub = SubLocation(parent_id=req.parent_id, name=req.requested_name)
            db.session.add(sub)
            db.session.flush()
            if not UserSubLocationAccess.query.filter_by(user_id=req.requested_by_id, sub_location_id=sub.id).first():
                db.session.add(UserSubLocationAccess(user_id=req.requested_by_id, sub_location_id=sub.id))
            req.status = "approved"
    else:
        req.status = "rejected"
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Subunit request reviewed.", "success")
    return redirect(url_for("super_admin_dashboard"))


@app.route("/super-admin/account-request/<int:req_id>/<decision>", methods=["POST"])
@login_required
@role_required("super_admin")
def super_admin_user_request(req_id, decision):
    req = db.session.get(UserAccessRequest, req_id)
    if not req or req.status != "pending" or decision not in ("approve", "reject"):
        abort(400)
    if decision == "approve":
        existing = User.query.filter_by(email=req.email).first()
        if existing:
            flash("Account already exists; assign the location manually.", "error")
            return redirect(url_for("super_admin_dashboard"))
        temporary = secrets.token_urlsafe(15)
        u = User(full_name=req.full_name, email=req.email, role="user", status="active", initial_password_reset_required=True)
        u.set_password(temporary)
        db.session.add(u)
        db.session.flush()
        db.session.add(UserSubLocationAccess(user_id=u.id, sub_location_id=req.sub_location_id))
        req.status = "approved"
        db.session.commit()
        send_email(u.email, "Your Invotra account is ready",
                   f"Your temporary password is: {temporary}\nPlease change it on first sign in.", event="account_created")
    else:
        req.status = "rejected"
        db.session.commit()
    flash("Account request reviewed.", "success")
    return redirect(url_for("super_admin_dashboard"))


@app.route("/super-admin/upload-master", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def upload_master_sheet():
    companies = Company.query.order_by(Company.name).all()
    if request.method == "POST":
        company = db.session.get(Company, form_int("company_id"))
        upload = request.files.get("file")
        if not company or not company.active or not upload or not upload.filename.lower().endswith(".csv"):
            flash("Choose a company and a .csv file.", "error")
            return redirect(url_for("upload_master_sheet"))
        try:
            reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))
            required = {"Item Name", "Each UPC Code", "Each Cost", "Case UPC Code", "Case Cost", "GL Account"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError("CSV headers must be: " + ", ".join(sorted(required)))
            changes = []
            seen_codes = set()
            for lineno, row in enumerate(reader, 2):
                name = (row.get("Item Name") or "").strip()
                each = (row.get("Each UPC Code") or "").strip() or None
                case_upc = (row.get("Case UPC Code") or "").strip() or None
                if not name and not each and not case_upc and not (row.get("GL Account") or "").strip():
                    continue
                gl = locate_gl(company.id, row.get("GL Account") or "")
                if not name or len(name)>255 or not gl or not (each or case_upc):
                    raise ValueError(f"Row {lineno}: missing name, active GL or UPC")
                if (each and len(each)>100) or (case_upc and len(case_upc)>100) or (each and case_upc and each==case_upc):
                    raise ValueError(f"Row {lineno}: invalid or duplicated each/case UPC")
                for code in (each, case_upc):
                    if code and code in seen_codes:
                        raise ValueError(f"Row {lineno}: duplicate UPC {code} in this file")
                    if code:
                        seen_codes.add(code)
                ec = parse_cost(row.get("Each Cost"), f"Row {lineno} each cost")
                cc = parse_cost(row.get("Case Cost"), f"Row {lineno} case cost")
                matches = (ItemMaster.query.filter(ItemMaster.company_id==company.id,
                           or_(ItemMaster.each_upc.in_([c for c in (each, case_upc) if c]),
                               ItemMaster.case_upc.in_([c for c in (each, case_upc) if c]))).all())
                if len(matches)>1:
                    raise ValueError(f"Row {lineno}: UPCs conflict with multiple existing products")
                existing = matches[0] if matches else None
                if not unique_upcs(company.id, each, case_upc, existing.id if existing else None):
                    raise ValueError(f"Row {lineno}: UPC belongs to another item")
                changes.append((existing, name, each, ec, case_upc, cc, gl.id))
            added=updated=0
            for existing, name, each, ec, case_upc, cc, gl_id in changes:
                if existing:
                    existing.item_name=name; existing.each_upc=each; existing.each_cost=ec
                    existing.case_upc=case_upc; existing.case_cost=cc; existing.gl_account_id=gl_id
                    existing.active=True; updated+=1
                else:
                    db.session.add(ItemMaster(company_id=company.id, item_name=name,
                        each_upc=each, each_cost=ec, case_upc=case_upc, case_cost=cc, gl_account_id=gl_id))
                    added+=1
            db.session.commit()
            flash(f"Master sheet imported: {added} added, {updated} updated.", "success")
            return redirect(url_for("super_admin_dashboard"))
        except (ValueError, UnicodeDecodeError, csv.Error) as exc:
            db.session.rollback()
            flash("Nothing imported. " + str(exc), "error")
            return redirect(url_for("upload_master_sheet"))
    return render_template("upload_master_sheet.html", companies=companies)


@app.route("/super-admin/download-master/<int:company_id>")
@login_required
@role_required("super_admin")
def download_master(company_id):
    if not db.session.get(Company, company_id):
        abort(404)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Item Name", "Each UPC Code", "Each Cost", "Case UPC Code", "Case Cost", "GL Account"])
    for item in ItemMaster.query.filter_by(company_id=company_id, active=True).order_by(ItemMaster.item_name).all():
        writer.writerow([item.item_name, item.each_upc or "", item.each_cost,
                         item.case_upc or "", item.case_cost, item.gl_account.label])
    return Response(output.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=invotra_master_company_{company_id}.csv"})


@app.route("/inventory/<int:session_id>/csv")
@login_required
def inventory_csv_download(session_id):
    inv = db.session.get(InventorySession, session_id)
    user = current_user()
    if not inv or (inv.created_by_user_id != user.id and
        not (can_access_sub(user, inv.sub_location_id) and
             (user.role in ("admin", "super_admin") or inv.status == "approved"))):
        abort(403)
    return Response(session_csv_bytes(inv), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=invotra_inventory_{session_id}.csv"})


@app.route("/super-admin/test-email", methods=["POST"])
@login_required
@role_required("super_admin")
def test_email():
    recipient = request.form.get("recipient", "").strip() or current_user().email
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient):
        abort(400)
    ok = send_email(recipient, "Invotra Test Email", f"Invotra SMTP test at {datetime.utcnow().isoformat()} UTC", event="test")
    flash("Test email sent." if ok else "Email failed. Check the delivery log and Render SMTP configuration.",
          "success" if ok else "error")
    return redirect(url_for("super_admin_dashboard"))

if __name__ == "__main__":
    app.run(debug=True)
