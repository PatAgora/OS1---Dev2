# approver_portal.py — Approver Portal Blueprint (timesheet approval)
#
# Registered at the /approver prefix. This is a self-contained portal, the
# same relationship the Associate Portal has with the OS1 admin app:
#   - its own login page and its own session key (approver_user_id)
#   - it does NOT use Flask-Login, so an approver is never authenticated
#     against the OS1 admin app and cannot reach OS1 admin pages
#   - its own templates (templates_approver/) and base layout
# Approvers are rows in the shared `users` table with role='approver'.
from __future__ import annotations

import importlib
import os
from functools import wraps
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, send_from_directory, current_app,
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import Session as SASession
from sqlalchemy import select, text, func

approver_bp = Blueprint(
    "approver",
    __name__,
    template_folder="templates_approver",
    static_folder="static_approver",
    static_url_path="/approver_static",
)

SESSION_KEY = "approver_user_id"
PENDING_2FA_KEY = "approver_2fa_uid"


# ---------------------------------------------------------------------------
# Lazy access to app.py (avoid circular import)
# ---------------------------------------------------------------------------
def _app():
    return importlib.import_module("app")


def _engine():
    return _app().engine


def _model(name):
    return getattr(_app(), name, None)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _approver_info():
    """Return {id, name, email} for the logged-in approver, or None.

    Re-validates against the DB on every call: the user must still exist,
    still have role='approver', and still be active."""
    uid = session.get(SESSION_KEY)
    if not uid:
        return None
    try:
        with SASession(_engine()) as s:
            row = s.execute(text(
                "SELECT id, name, email, role, is_active FROM users WHERE id = :id"
            ).bindparams(id=uid)).first()
        if row and (row.role or "").lower() == "approver" and (row.is_active is not False):
            return {"id": row.id, "name": row.name, "email": row.email}
    except Exception:
        return None
    return None


@approver_bp.context_processor
def _inject_approver():
    return {"approver": _approver_info()}


def _require_approver_login(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _approver_info():
            flash("Please sign in to the approver portal.", "warning")
            return redirect(url_for("approver.login"))
        return f(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@approver_bp.route("/login", methods=["GET", "POST"])
def login():
    """Email + password (+ TOTP if the approver has 2FA enabled)."""
    User = _model("User")
    if request.method == "GET":
        if _approver_info():
            return redirect(url_for("approver.dashboard"))
        stage = "2fa" if session.get(PENDING_2FA_KEY) else "password"
        return render_template("approver/login.html", stage=stage)

    # --- 2FA stage: a code submitted against a pending login ---
    pending_uid = session.get(PENDING_2FA_KEY)
    if pending_uid and request.form.get("totp_code"):
        code = (request.form.get("totp_code") or "").strip()
        with SASession(_engine()) as s:
            u = s.get(User, pending_uid)
            if not u:
                session.pop(PENDING_2FA_KEY, None)
                flash("Session expired — please sign in again.", "warning")
                return redirect(url_for("approver.login"))
            ok = False
            try:
                ok = u.verify_totp(code)
            except Exception:
                ok = False
            if not ok:
                flash("Invalid authenticator code. Please try again.", "danger")
                return render_template("approver/login.html", stage="2fa")
            u.last_login = datetime.utcnow()
            s.commit()
            uid = u.id
        session.pop(PENDING_2FA_KEY, None)
        session[SESSION_KEY] = uid
        session.permanent = True
        flash("Signed in.", "success")
        return redirect(url_for("approver.dashboard"))

    # --- password stage ---
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not email or not password:
        flash("Enter your email and password.", "danger")
        return redirect(url_for("approver.login"))

    with SASession(_engine()) as s:
        u = s.execute(
            select(User).where(func.lower(User.email) == email)
        ).scalars().first()
        # Only role='approver' accounts may use this portal.
        if not u or (u.role or "").lower() != "approver":
            flash("Invalid email or password.", "danger")
            return redirect(url_for("approver.login"))
        if u.is_active is False:
            flash("This approver account is deactivated. Contact the Optimus team.", "danger")
            return redirect(url_for("approver.login"))
        if not u.password_hash:
            flash("Your account isn't set up yet — use the invite link emailed to you.", "info")
            return redirect(url_for("approver.login"))
        if not check_password_hash(u.password_hash, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("approver.login"))

        # Password OK. Step up to 2FA if the account has it enabled.
        if getattr(u, "totp_enabled", False) and getattr(u, "totp_secret", None):
            session[PENDING_2FA_KEY] = u.id
            return render_template("approver/login.html", stage="2fa")

        u.last_login = datetime.utcnow()
        s.commit()
        uid = u.id

    session.pop(PENDING_2FA_KEY, None)
    session[SESSION_KEY] = uid
    session.permanent = True
    flash("Signed in.", "success")
    return redirect(url_for("approver.dashboard"))


@approver_bp.route("/logout")
def logout():
    for k in (SESSION_KEY, PENDING_2FA_KEY):
        session.pop(k, None)
    flash("You have been signed out.", "success")
    return redirect(url_for("approver.login"))


@approver_bp.route("/onboard/<token>", methods=["GET", "POST"])
def onboard(token):
    """Magic-link landing for a newly-created approver: set a password,
    then they're signed straight into the portal."""
    User = _model("User")
    with SASession(_engine()) as s:
        u = s.execute(
            select(User).where(User.magic_token == token)
        ).scalars().first()
        if not u or (u.role or "").lower() != "approver":
            flash("This invite link is invalid.", "danger")
            return redirect(url_for("approver.login"))
        expires = getattr(u, "magic_token_expires", None)
        if expires and expires < datetime.utcnow():
            flash("This invite link has expired. Ask the Optimus team for a new one.", "danger")
            return redirect(url_for("approver.login"))

        if request.method == "GET":
            return render_template("approver/onboard.html", token=token, name=u.name or u.email)

        pw1 = request.form.get("password") or ""
        pw2 = request.form.get("password_confirm") or ""
        if len(pw1) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("approver/onboard.html", token=token, name=u.name or u.email)
        if pw1 != pw2:
            flash("Passwords do not match.", "danger")
            return render_template("approver/onboard.html", token=token, name=u.name or u.email)

        u.password_hash = generate_password_hash(pw1, method="pbkdf2:sha256")
        u.magic_token = None
        u.magic_token_expires = None
        u.last_login = datetime.utcnow()
        s.commit()
        uid = u.id

    session.pop(PENDING_2FA_KEY, None)
    session[SESSION_KEY] = uid
    session.permanent = True
    flash("Password set — welcome to the Approver Portal.", "success")
    return redirect(url_for("approver.dashboard"))


# ---------------------------------------------------------------------------
# Portal routes (timesheet approval)
# ---------------------------------------------------------------------------
@approver_bp.route("/", methods=["GET"])
@approver_bp.route("/dashboard", methods=["GET"])
@_require_approver_login
def dashboard():
    """TS 11 — timesheets allocated to this approver, filterable by status."""
    app = _app()
    uid = session[SESSION_KEY]
    status_filter = (request.args.get("status") or "submitted").strip().lower() or None
    if status_filter == "all":
        status_filter = None
    timesheets = app._approver_timesheet_query(uid, status_filter=status_filter)
    clients = sorted({r["client_name"] for r in timesheets if r["client_name"]})
    projects = sorted({r["engagement_name"] for r in timesheets if r["engagement_name"]})
    associates = sorted({r["associate_name"] for r in timesheets if r["associate_name"]})
    return render_template(
        "approver/dashboard.html",
        timesheets=timesheets,
        clients=clients,
        projects=projects,
        associates=associates,
        status_filter=status_filter or "all",
    )


@approver_bp.route("/history", methods=["GET"])
@_require_approver_login
def history():
    app = _app()
    uid = session[SESSION_KEY]
    timesheets = app._approver_timesheet_query(uid, status_filter="approved")
    return render_template("approver/history.html", timesheets=timesheets)


@approver_bp.route("/timesheet/<int:ts_id>", methods=["GET"])
@_require_approver_login
def timesheet_detail(ts_id):
    app = _app()
    Timesheet = _model("Timesheet")
    uid = session[SESSION_KEY]
    import datetime as _dt
    with SASession(_engine()) as s:
        if not app._approver_allowed(s, uid, ts_id):
            flash("That timesheet isn't allocated to you.", "danger")
            return redirect(url_for("approver.dashboard"))
        ts = s.get(Timesheet, ts_id)
        if not ts:
            abort(404)
        cand = s.execute(text("SELECT id, name, email FROM candidates WHERE id = :id")
                         .bindparams(id=ts.user_id)).first()
        eng = s.execute(text("SELECT id, name, client FROM engagements WHERE id = :id")
                        .bindparams(id=ts.engagement_id)).first()
        week_days = []
        if ts.period_start:
            for i in range(7):
                d = ts.period_start + _dt.timedelta(days=i)
                week_days.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "short": d.strftime("%a"),
                    "dom": d.strftime("%d"),
                })
        entries_grid = {}
        ot_multipliers = {}
        time_types_used = []
        rows = s.execute(text(
            "SELECT entry_date, time_type, value FROM timesheet_entries "
            "WHERE timesheet_id = :tid"
        ).bindparams(tid=ts_id)).all()
        for e in rows:
            v = float(e.value or 0)
            if v <= 0:
                continue
            d_iso = e.entry_date.strftime("%Y-%m-%d") if e.entry_date else ""
            tt = e.time_type or ""
            if not d_iso or not tt:
                continue
            if tt == "OT Multiplier":
                ot_multipliers[d_iso] = v
            else:
                entries_grid[(d_iso, tt)] = v
                if tt not in time_types_used:
                    time_types_used.append(tt)
        preferred_order = ["Standard Time", "Overtime", "Holiday", "Sickness", "Unplanned Absence"]
        time_types_used.sort(
            key=lambda t: preferred_order.index(t) if t in preferred_order else 999,
        )
        if not time_types_used:
            time_types_used = ["Standard Time"]
        has_ot = any("overtime" in tt.lower() for tt in time_types_used)
        expenses = s.execute(text(
            "SELECT id, expense_type, description, amount, vat_rate_pct, vat_amount, "
            "       date_of_expense, distance_miles, receipt_doc_id "
            "FROM timesheet_expenses WHERE timesheet_id = :tid"
        ).bindparams(tid=ts_id)).all()
    return render_template(
        "approver/detail.html",
        ts=ts, cand=cand, eng=eng,
        week_days=week_days, entries_grid=entries_grid,
        time_types_used=time_types_used, ot_multipliers=ot_multipliers,
        has_ot=has_ot, expenses=expenses,
    )


@approver_bp.route("/timesheet/<int:ts_id>/approve", methods=["POST"])
@_require_approver_login
def approve_timesheet(ts_id):
    app = _app()
    Timesheet = _model("Timesheet")
    uid = session[SESSION_KEY]
    with SASession(_engine()) as s:
        if not app._approver_allowed(s, uid, ts_id):
            flash("That timesheet isn't allocated to you.", "danger")
            return redirect(url_for("approver.dashboard"))
        ts = s.get(Timesheet, ts_id)
        if not ts:
            abort(404)
        app._apply_timesheet_approval(s, ts, uid)
        s.commit()
    flash(f"Timesheet #{ts_id} approved.", "success")
    return redirect(url_for("approver.dashboard"))


@approver_bp.route("/timesheet/<int:ts_id>/reject", methods=["POST"])
@_require_approver_login
def reject_timesheet(ts_id):
    app = _app()
    Timesheet = _model("Timesheet")
    uid = session[SESSION_KEY]
    reason = (request.form.get("reject_reason") or "").strip()
    with SASession(_engine()) as s:
        if not app._approver_allowed(s, uid, ts_id):
            flash("That timesheet isn't allocated to you.", "danger")
            return redirect(url_for("approver.dashboard"))
        ts = s.get(Timesheet, ts_id)
        if not ts:
            abort(404)
        app._apply_timesheet_rejection(s, ts, uid, reason)
        s.commit()
    flash(f"Timesheet #{ts_id} rejected.", "success")
    return redirect(url_for("approver.dashboard"))


@approver_bp.route("/receipt/<int:doc_id>", methods=["GET"])
@_require_approver_login
def receipt(doc_id):
    """Serve an expense-receipt document to the logged-in approver."""
    app = _app()
    Document = _model("Document")
    with SASession(_engine()) as s:
        doc = s.get(Document, doc_id)
        if not doc:
            abort(404)
        try:
            path = app._doc_file_path(doc)
        except Exception:
            abort(404)
    if path and os.path.exists(path):
        return send_from_directory(
            os.path.dirname(path), os.path.basename(path),
            as_attachment=True, download_name=(doc.original_name or os.path.basename(path)),
        )
    abort(404)
