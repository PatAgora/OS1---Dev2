# associate_portal.py — Associate (Candidate) Portal Blueprint
# Registered at /portal/ prefix
from __future__ import annotations

import os
import io
import json
import base64
import importlib
import secrets
from datetime import datetime, timedelta, date
from functools import wraps
from typing import Optional, List
from uuid import uuid4

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
    abort,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename
from sqlalchemy.orm import Session as SASession
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Date,
    ForeignKey,
    select,
    Float,
    text,
    func,
)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

associate_bp = Blueprint(
    "associate",
    __name__,
    template_folder="templates_associate",
    static_folder="static_associate",
    static_url_path="/associate_static",
)


@associate_bp.before_request
def _ensure_portal_models_before_request():
    """Guarantee portal models are defined on EVERY request,
    not just lazily on first _portal_model() call. This runs
    before any route handler, so by the time the handler calls
    _portal_model(), the classes are always registered."""
    try:
        _ensure_models()
    except Exception as exc:
        print(f"[PORTAL] _ensure_models failed in before_request: {exc}", flush=True)


def _apply_rate_limits(app):
    """Apply rate limits to portal auth routes. Called after blueprint registration."""
    try:
        from app import limiter
        if limiter and hasattr(limiter, 'limit'):
            limiter.limit("10 per minute")(login)
            limiter.limit("10 per minute")(register)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lazy imports to avoid circular import with app.py
# ---------------------------------------------------------------------------

def _app_module():
    """Return the app module lazily."""
    return importlib.import_module("app")


def _engine():
    return getattr(_app_module(), "engine")


def _base():
    return getattr(_app_module(), "Base")


def _model(name: str):
    """Load a model class from app by name. Returns None if missing."""
    try:
        return getattr(_app_module(), name, None)
    except Exception:
        return None


def _load(name: str):
    """Load any attribute from app at runtime."""
    try:
        return getattr(_app_module(), name, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# New models (Associate Portal specific)
# ---------------------------------------------------------------------------
# These are defined lazily so that Base is resolved at import-time of app.py
# We use a module-level flag to avoid re-defining.

_MODELS_DEFINED = False
_models = {}  # Model registry for external access


def _ensure_models():
    """Define portal-specific SQLAlchemy models once, using app.Base."""
    global _MODELS_DEFINED
    if _MODELS_DEFINED:
        return
    # NOTE: do NOT flip _MODELS_DEFINED here — if a class definition raises
    # below we'd be stuck without the remaining models ever being registered.
    # We only set the flag at the very bottom, after all classes succeed.

    Base = _base()

    # Guard against re-registration if classes are already in the
    # mapper registry for THIS process. We check for a specific
    # portal class (CompanyDetails), not just the table name —
    # because Base.metadata.tables is populated by create_all()
    # at import time (before Gunicorn fork), but the class mappers
    # are per-process. If the table exists but the class doesn't,
    # we MUST define the classes.
    PORTAL_MODEL_NAMES = {
        "AssociateProfile", "CompanyDetails", "ConsentRecord",
        "DeclarationRecord", "EmploymentHistory", "AddressHistory",
        "QualificationRecord", "ProfessionalRegistration",
        "ReferenceContact", "FlaggedReferenceHouse",
        "TimesheetConfig", "TimesheetEntry", "TimesheetExpense",
    }
    registered_names = {m.class_.__name__ for m in Base.registry.mappers}
    if "CompanyDetails" in registered_names:
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            if cls.__name__ in PORTAL_MODEL_NAMES:
                _models[cls.__name__] = cls
        _MODELS_DEFINED = True
        return

    class AssociateProfile(Base):
        __tablename__ = "associate_profiles"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, unique=True, index=True)
        title = Column(String(20), default="")
        first_name = Column(String(200), default="")
        surname = Column(String(200), default="")
        aliases = Column(Text, default="")
        previous_names = Column(Text, default="")
        previous_first_names = Column(String(500), default="")
        previous_surnames = Column(String(500), default="")
        most_recent_employer = Column(String(300), default="")
        contact_current_employer = Column(Boolean, default=True)
        unsubscribed = Column(Boolean, default=False)
        profile_picture = Column(String(500), default="")
        dob = Column(Date, nullable=True)
        address_line1 = Column(String(500), default="")
        address_line2 = Column(String(500), default="")
        city = Column(String(200), default="")
        postcode = Column(String(20), default="")
        country = Column(String(100), default="United Kingdom")
        # Req 26 — drives the DBS criminal-record variant (England & Wales vs
        # Scotland) and, in future, the regional vetting package.
        current_address_in_scotland = Column(Boolean, nullable=True)
        contact_number = Column(String(50), default="")
        emergency_contact_name = Column(String(200), default="")
        emergency_contact_phone = Column(String(50), default="")
        emergency_contact_relationship = Column(String(100), default="")
        current_salary = Column(String(100), default="")
        expected_salary = Column(String(100), default="")
        available_from = Column(Date, nullable=True)
        national_insurance_number = Column(String(20), default="")
        # P8: Gender field for vetting checks
        gender = Column(String(20), default="")
        # Identity documents for Verifile client entry
        passport_number = Column(String(20), default="")
        passport_issuing_country = Column(String(10), default="GB")
        passport_issue_date = Column(Date, nullable=True)
        passport_expiry_date = Column(Date, nullable=True)
        driving_licence_number = Column(String(20), default="")
        driving_licence_issuing_country = Column(String(10), default="GB")
        driving_licence_issue_date = Column(Date, nullable=True)
        country_of_birth = Column(String(100), default="")
        town_of_birth = Column(String(100), default="")
        mother_maiden_name = Column(String(100), default="")
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class CompanyDetails(Base):
        __tablename__ = "company_details"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, unique=True, index=True)
        contracting_type = Column(String(20), default="")  # umbrella / limited
        company_name = Column(String(300), default="")
        registration_number = Column(String(50), default="")
        vat_registered = Column(Boolean, default=False)
        vat_number = Column(String(50), default="")
        bank_account_number = Column(String(20), default="")
        bank_sort_code = Column(String(10), default="")
        umbrella_company_name = Column(String(300), default="")
        # Contact email for the contracting entity (umbrella's payroll/contracting
        # contact OR the candidate's own limited-company email) — used when
        # staff issue contracts so the right mailbox gets the e-sign request.
        contact_email = Column(String(254), default="")
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class ConsentRecord(Base):
        __tablename__ = "consent_records"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        consent_given = Column(Boolean, default=False)
        reference_consent = Column(Boolean, default=False)
        secondary_employment = Column(Boolean, default=False)
        secondary_employment_details = Column(Text, default="")
        legal_name = Column(String(300), default="")
        signed_date = Column(DateTime, nullable=True)
        ip_address = Column(String(50), default="")
        created_at = Column(DateTime, default=datetime.utcnow)
        # Signable envelope ID returned when the consent form is sent for e-signature.
        # Holds "stub-<uuid>" while SIGNABLE_LIVE_CALLS is disabled.
        signable_envelope_id = Column(String(64), nullable=True, default=None)

    class DeclarationRecord(Base):
        __tablename__ = "declaration_records"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        work_restrictions = Column(Boolean, default=False)
        work_restrictions_detail = Column(Text, default="")
        criminal_convictions = Column(Boolean, default=False)
        criminal_convictions_detail = Column(Text, default="")
        ccj_debt = Column(Boolean, default=False)
        ccj_debt_detail = Column(Text, default="")
        bankruptcy = Column(Boolean, default=False)
        bankruptcy_detail = Column(Text, default="")
        dismissed = Column(Boolean, default=False)
        dismissed_detail = Column(Text, default="")
        referencing_issues = Column(Boolean, default=False)
        referencing_issues_detail = Column(Text, default="")
        disclosure_text = Column(Text, default="")
        legal_name = Column(String(300), default="")
        signed_date = Column(DateTime, nullable=True)
        ip_address = Column(String(50), default="")
        created_at = Column(DateTime, default=datetime.utcnow)
        # Req-005: Signable envelope ID returned when the declaration is sent for e-signature.
        # Holds "stub-<uuid>" while SIGNABLE_LIVE_CALLS is disabled.
        signable_envelope_id = Column(String(64), nullable=True, default=None)

    class EmploymentHistory(Base):
        __tablename__ = "employment_history"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        company_name = Column(String(300), default="")
        agency_name = Column(String(300), default="")
        # Req 22 — split referee email into company-side and agency-side so
        # the references list can offer "Send to Agency" / "Send to Company".
        # Legacy `referee_email` is kept and treated as the Company Email
        # for rows that pre-date the split.
        referee_email = Column(String(300), default="")
        company_email = Column(String(300), default="")
        agency_email = Column(String(300), default="")
        company_address = Column(Text, default="")
        start_date = Column(Date, nullable=True)
        end_date = Column(Date, nullable=True)
        job_title = Column(String(300), default="")
        reason_for_leaving = Column(Text, default="")
        is_gap = Column(Boolean, default=False)
        gap_reason = Column(Text, default="")
        gap_evidence_doc_id = Column(Integer, nullable=True)
        permission_to_request = Column(Boolean, default=True)
        permission_delay_reason = Column(Text, default="")
        permission_future_date = Column(Date, nullable=True)
        reference_status = Column(String(30), default="not_sent")  # not_sent/sent/received/flagged
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class QualificationRecord(Base):
        __tablename__ = "qualification_records"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        qualification_name = Column(String(300), default="")
        qualification_type = Column(String(100), default="")
        grade = Column(String(100), default="")
        institution = Column(String(300), default="")
        level = Column(String(100), default="")  # Verifile: CheckAcademicLevelOfQualification
        institution_street = Column(String(300), default="")  # Verifile: institution address street
        institution_town = Column(String(200), default="")  # Verifile: institution address town
        institution_country = Column(String(10), default="GB")  # Verifile: institution address country
        start_date = Column(Date, nullable=True)
        end_date = Column(Date, nullable=True)
        permission_to_request = Column(Boolean, default=True)
        permission_delay_reason = Column(Text, default="")
        created_at = Column(DateTime, default=datetime.utcnow)

    class ProfessionalRegistration(Base):
        """Professional membership / registration records for Verifile ProfessionalMembershipQualificationUK."""
        __tablename__ = "professional_registrations"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        association_name = Column(String(300), default="")
        membership_number = Column(String(100), default="")
        membership_level = Column(String(100), default="")
        membership_status = Column(String(50), default="Current")  # Current/Lapsed/Expired/etc
        created_at = Column(DateTime, default=datetime.utcnow)

    class ReferenceContact(Base):
        __tablename__ = "reference_contacts"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        company_name = Column(String(300), nullable=False, index=True)
        referee_email = Column(String(300), default="")
        last_amended = Column(DateTime, default=datetime.utcnow)

    class FlaggedReferenceHouse(Base):
        __tablename__ = "flagged_reference_houses"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String(300), nullable=False, index=True)
        candidate_count = Column(Integer, default=0)
        end_clients = Column(Text, default="")
        website = Column(String(500), default="")
        companies_house_url = Column(String(500), default="")
        notes = Column(Text, default="")
        created_at = Column(DateTime, default=datetime.utcnow)

    class TimesheetConfig(Base):
        """Admin-configurable timesheet settings per engagement."""
        __tablename__ = "timesheet_configs"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True)
        engagement_id = Column(Integer, index=True)
        time_types = Column(Text, default='["Standard Time","Overtime","Holiday","Sickness","Unplanned Absence"]')  # JSON
        overtime_enabled = Column(Boolean, default=False)
        overtime_multiplier = Column(Float, default=1.5)
        overtime_rate_type = Column(String(20), default="hourly")  # hourly or daily
        expense_enabled = Column(Boolean, default=False)
        expense_types = Column(Text, default='["Travel","Accommodation","Meals","Equipment","Other"]')  # JSON
        expense_limits = Column(Text, default="{}")  # JSON: {"Travel": 100}
        day_rate = Column(Float, default=0)
        overtime_rate = Column(Float, default=0)
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class TimesheetEntry(Base):
        """Individual day entry for a timesheet (one row per time-type per day)."""
        __tablename__ = "timesheet_entries"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True)
        timesheet_id = Column(Integer, index=True)
        entry_date = Column(Date)
        time_type = Column(String(100))  # "Standard Time", "Overtime", etc.
        value = Column(Float, default=0)  # 1.0=full day, 0.5=half day, or hours for OT
        value_unit = Column(String(20), default="days")  # "days" or "hours"
        created_at = Column(DateTime, default=datetime.utcnow)

    class TimesheetExpense(Base):
        """Expense line item attached to a timesheet."""
        __tablename__ = "timesheet_expenses"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True)
        timesheet_id = Column(Integer, index=True)
        expense_type = Column(String(100))
        description = Column(String(500))
        amount = Column(Float, default=0)
        receipt_doc_id = Column(Integer, nullable=True)
        created_at = Column(DateTime, default=datetime.utcnow)
        # Phase 2 / TS 12-15, 25-29 — category-driven VAT, per-line date,
        # mileage distance, and a per-line approved stamp. All nullable so
        # legacy rows keep working untouched.
        expense_category_id = Column(Integer, nullable=True)        # FK -> expense_categories.id
        vat_rate_pct = Column(Float, default=0.0)
        vat_amount = Column(Float, default=0.0)
        date_of_expense = Column(Date, nullable=True)
        distance_miles = Column(Float, nullable=True)
        approved_at = Column(DateTime, nullable=True)

    # P7: 5-year address history for DBS/Credit checks
    class AddressHistory(Base):
        """Associate address history — 5 years required for DBS/Credit checks."""
        __tablename__ = "address_history"
        __table_args__ = {"extend_existing": True}
        id = Column(Integer, primary_key=True)
        candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
        address_line1 = Column(String(500), default="")
        address_line2 = Column(String(500), default="")
        city = Column(String(200), default="")
        postcode = Column(String(20), default="")
        country = Column(String(100), default="United Kingdom")
        from_date = Column(Date, nullable=True)
        to_date = Column(Date, nullable=True)
        is_current = Column(Boolean, default=False)
        created_at = Column(DateTime, default=datetime.utcnow)

    # Register ALL portal models in the _models dict for external access.
    # Previous code only registered 4 — missing classes caused lookup
    # failures on some workers.
    _models["AssociateProfile"] = AssociateProfile
    _models["CompanyDetails"] = CompanyDetails
    _models["ConsentRecord"] = ConsentRecord
    _models["DeclarationRecord"] = DeclarationRecord
    _models["EmploymentHistory"] = EmploymentHistory
    _models["QualificationRecord"] = QualificationRecord
    _models["ProfessionalRegistration"] = ProfessionalRegistration
    _models["ReferenceContact"] = ReferenceContact
    _models["FlaggedReferenceHouse"] = FlaggedReferenceHouse
    _models["TimesheetConfig"] = TimesheetConfig
    _models["TimesheetEntry"] = TimesheetEntry
    _models["TimesheetExpense"] = TimesheetExpense
    _models["AddressHistory"] = AddressHistory

    _MODELS_DEFINED = True


def _portal_model(name: str):
    """Get a portal-specific model class. Checks the _models dict first
    (populated by _ensure_models), then falls back to the mapper registry.
    Retries once if the first lookup fails."""
    global _MODELS_DEFINED

    # Fast path: already in _models dict
    if name in _models:
        return _models[name]

    for attempt in range(2):
        _ensure_models()
        if name in _models:
            return _models[name]
        Base = _base()
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            if cls.__name__ == name:
                _models[name] = cls
                return cls
        # Not found — log the mappers we did see and try one retry after
        # clearing the guard.
        try:
            registered = sorted({m.class_.__name__ for m in Base.registry.mappers})
            current_app.logger.error(
                "_portal_model('%s') not found on attempt %d. Registered: %s",
                name, attempt + 1, ", ".join(registered),
            )
        except Exception:
            pass
        _MODELS_DEFINED = False
    return None


# ---------------------------------------------------------------------------
# Session / Auth helpers
# ---------------------------------------------------------------------------

def _get_associate_id() -> Optional[int]:
    return session.get("associate_user_id")


def _next_vetting_stage_url(cand_id: Optional[int]) -> str:
    """Return the URL of the next outstanding vetting stage for the associate.
    Used to auto-advance them after a successful stage submission so they're
    not stuck on the page they just completed.

    Order: Consent → Declaration → Secondary Job → Employment Reference
    Declaration. The walk DELIBERATELY STOPS at the Employment Reference
    Declaration — it does NOT chain on to /portal/documents. Once all four
    stages are signed the helper returns /portal/vetting-progress so the
    associate lands on the overview, not Documents.
    """
    fallback = url_for("associate.vetting_progress")
    if not cand_id:
        return fallback

    Candidate = _model("Candidate")
    ConsentRecord = _portal_model("ConsentRecord")
    DeclarationRecord = _portal_model("DeclarationRecord")
    if not Candidate:
        return fallback

    try:
        with SASession(_engine()) as s:
            cand = s.get(Candidate, cand_id)
            if not cand:
                return fallback

            consent_signed = False
            if ConsentRecord:
                rec = s.query(ConsentRecord).filter_by(candidate_id=cand_id).first()
                consent_signed = bool(rec and getattr(rec, "consent_given", False))

            declaration_signed = False
            if DeclarationRecord:
                rec = s.query(DeclarationRecord).filter_by(candidate_id=cand_id).first()
                declaration_signed = bool(rec and getattr(rec, "signed_date", None))

            secondary_signed = bool(getattr(cand, "secondary_job_declaration_signed", False))
            emp_ref_signed = bool(getattr(cand, "employment_ref_declaration_signed", False))

        if not consent_signed:
            return url_for("associate.consent_form")
        if not declaration_signed:
            return url_for("associate.declaration_form")
        if not secondary_signed:
            return url_for("associate.secondary_job_declaration")
        if not emp_ref_signed:
            return url_for("associate.references_employment")
        return fallback
    except Exception:
        current_app.logger.exception("_next_vetting_stage_url failed")
        return fallback


def _get_current_associate():
    """Return the Candidate object for the logged-in associate, or None."""
    cid = _get_associate_id()
    if not cid:
        return None
    Candidate = _model("Candidate")
    if not Candidate:
        return None
    with SASession(_engine()) as s:
        return s.get(Candidate, cid)


def _require_login(f):
    """Decorator: redirect to associate login if not authenticated.
    Also enforces 2FA setup — if the associate hasn't set up TOTP yet,
    redirect them to the setup page before allowing access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        cand_id = _get_associate_id()
        if not cand_id:
            flash("Please sign in to access the portal.", "warning")
            return redirect(url_for("associate.login", next=request.url))
        # Enforce 2FA setup — skip if already on the setup page
        if request.endpoint != "associate.setup_2fa":
            try:
                Candidate = _model("Candidate")
                if Candidate:
                    engine = _engine()
                    with SASession(engine) as _s:
                        _cand = _s.get(Candidate, cand_id)
                        if _cand and not getattr(_cand, "totp_enabled", False):
                            session["associate_setup_2fa_id"] = cand_id
                            session["associate_setup_2fa_ts"] = datetime.utcnow().timestamp()
                            flash("Please set up two-factor authentication to continue.", "info")
                            return redirect(url_for("associate.setup_2fa"))
            except Exception:
                pass
        return f(*args, **kwargs)
    return decorated


def _sanitise(value: str) -> str:
    """Sanitise user input with bleach."""
    try:
        import bleach
        return bleach.clean(value or "", strip=True)
    except ImportError:
        # Fallback: basic HTML entity escaping
        return (value or "").replace("<", "&lt;").replace(">", "&gt;")


def _widget_url_with_meta(base_url: str, cand_id) -> str:
    """Append envelope_meta=<cand_id> to a Signable widget URL so the
    webhook payload carries the candidate id back and we don't rely on
    fragile name-based matching."""
    if not base_url or cand_id is None:
        return base_url or ""
    try:
        from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse
        parsed = urlparse(base_url)
        existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
        existing["envelope_meta"] = str(cand_id)
        return urlunparse(parsed._replace(query=urlencode(existing)))
    except Exception:
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}envelope_meta={cand_id}"


def _portal_signer():
    """Timed serializer for magic link tokens."""
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"], salt="associate-portal-link"
    )


def _send_magic_link(email: str, name: str, is_signup: bool = True, next_url: str = ""):
    """Generate and send a magic-link email to the associate."""
    send_email = _load("send_email")
    PORTAL_BASE_URL = _load("PORTAL_BASE_URL") or _load("APP_BASE_URL") or "http://localhost:5000"

    token_data = {"email": email, "name": name, "is_signup": is_signup}
    if next_url:
        token_data["next"] = next_url
    token = _portal_signer().dumps(token_data)
    verify_url = f"{PORTAL_BASE_URL}/portal/verify-email?token={token}"

    subject = (
        "Complete Your Registration - Optimus"
        if is_signup
        else "Password Reset - Optimus"
    )

    html_body = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#0a1628;padding:30px;border-radius:12px 12px 0 0;text-align:center;">
            <h1 style="color:#00d4ff;margin:0;font-size:24px;">Optimus</h1>
            <p style="color:#94a3b8;margin:10px 0 0;font-size:14px;">Associate Portal</p>
        </div>
        <div style="background:#fff;padding:40px 30px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;">
            <h2 style="color:#0a1628;margin:0 0 20px;font-size:20px;">Hello, {_sanitise(name)}!</h2>
            <p style="color:#334155;margin:0 0 20px;line-height:1.6;">
                {'Thank you for registering. Click below to verify your email and set up your account.' if is_signup else 'Click below to reset your password.'}
            </p>
            <div style="text-align:center;margin:30px 0;">
                <a href="{verify_url}" style="background:#0066cc;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block;">
                    {'Complete Registration' if is_signup else 'Reset Password'}
                </a>
            </div>
            <p style="color:#64748b;font-size:14px;margin:20px 0 0;">
                This link expires in 30 minutes. If you did not request this, ignore this email.
            </p>
        </div>
        <div style="text-align:center;padding:20px;color:#94a3b8;font-size:12px;">
            <p>&copy; 2026 Optimus. All rights reserved.</p>
        </div>
    </div>
    """

    if callable(send_email):
        send_email(to_email=email, subject=subject, html_body=html_body)
        current_app.logger.info("Associate magic link sent to %s", email)
    else:
        current_app.logger.warning("send_email not available; magic link NOT sent to %s", email)


def _audit_portal(event_type: str, action: str, candidate_id: int = None,
                   details: dict = None, status: str = "success"):
    """Log audit event for portal activity. Non-blocking — never raises."""
    try:
        log_fn = _load("log_audit_event")
        if log_fn:
            log_fn(event_type, "portal_auth", action,
                   resource_type="candidate", resource_id=candidate_id,
                   details=details, status=status)
    except Exception:
        pass  # Audit logging must never break the portal


def _safe_next_url(url: str) -> str:
    """Validate redirect URL to prevent open redirect attacks."""
    if not url or not isinstance(url, str):
        return url_for("associate.dashboard")
    url = url.strip()
    # Must be a relative path starting with / but not //
    if url.startswith("/") and not url.startswith("//"):
        return url
    return url_for("associate.dashboard")


def _validate_password(password: str) -> tuple:
    """Return (is_valid, error_message)."""
    if len(password) < 12:
        return False, "Password must be at least 12 characters."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        return False, "Password must contain at least one special character."
    return True, ""


def _upload_dir() -> str:
    """Return the absolute upload directory for associate docs, creating it if needed."""
    # Use /data/uploads on Railway (persistent volume) or static/uploads for dev
    if os.path.isdir("/data"):
        d = "/data/uploads/associate_docs"
    else:
        root = current_app.root_path
        d = os.path.join(root, "static", "uploads", "associate_docs")
    os.makedirs(d, exist_ok=True)
    return d


def _save_file(file_storage) -> Optional[dict]:
    """Save an uploaded file. Returns dict(filename, original_name) or None."""
    if not file_storage or not (file_storage.filename or "").strip():
        return None
    orig = secure_filename(file_storage.filename)
    ext = os.path.splitext(orig)[1].lower() or ".pdf"
    new_name = f"{uuid4().hex}{ext}"
    dest = os.path.join(_upload_dir(), new_name)
    file_storage.save(dest)
    return {"filename": f"uploads/associate_docs/{new_name}", "original_name": orig}


def _add_note(s, candidate_id: int, content: str, note_type: str = "system"):
    """Create a CandidateNote audit entry."""
    CandidateNote = _model("CandidateNote")
    if not CandidateNote:
        return
    try:
        note = CandidateNote(
            candidate_id=candidate_id,
            user_email="associate-portal",
            note_type=note_type,
            content=_sanitise(content),
            created_at=datetime.utcnow(),
        )
        s.add(note)
    except Exception:
        current_app.logger.exception("Failed to add CandidateNote")


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string (YYYY-MM-DD) safely."""
    if not value:
        return None
    try:
        from dateutil import parser as dtparser
        return dtparser.parse(value).date()
    except Exception:
        return None


def _parse_bool(value) -> bool:
    """Parse a form boolean value."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Profile completeness calculation
# ---------------------------------------------------------------------------

def _calc_completeness(s, candidate_id: int) -> dict:
    """
    Calculate profile completeness percentages for the three dashboard sections.
    Returns dict with personal_pct, company_pct, checks_pct, overall_pct.
    """
    Candidate = _model("Candidate")
    AssociateProfile = _portal_model("AssociateProfile")
    CompanyDetails = _portal_model("CompanyDetails")
    VettingCheck = _model("VettingCheck")
    ConsentRecord = _portal_model("ConsentRecord")
    DeclarationRecord = _portal_model("DeclarationRecord")
    Document = _model("Document")
    EmploymentHistory = _portal_model("EmploymentHistory")

    # --- Personal Information ---
    personal_score = 0
    personal_total = 8

    cand = s.get(Candidate, candidate_id) if Candidate else None
    if cand:
        if cand.name:
            personal_score += 1
        if cand.email:
            personal_score += 1
        if cand.phone:
            personal_score += 1

    profile = None
    if AssociateProfile:
        profile = s.query(AssociateProfile).filter_by(candidate_id=candidate_id).first()
    if profile:
        if profile.first_name:
            personal_score += 1
        if profile.surname:
            personal_score += 1
        if profile.dob:
            personal_score += 1
        if profile.address_line1:
            personal_score += 1
        if profile.emergency_contact_name:
            personal_score += 1

    personal_pct = int(round(personal_score / personal_total * 100)) if personal_total else 0

    # --- Company Details ---
    company_score = 0
    company_total = 3
    comp = None
    if CompanyDetails:
        comp = s.query(CompanyDetails).filter_by(candidate_id=candidate_id).first()
    if comp is None:
        try:
            _cd = s.execute(text(
                "SELECT contracting_type, umbrella_company_name, company_name, "
                "       registration_number FROM company_details WHERE candidate_id = :cid"
            ).bindparams(cid=candidate_id)).first()
            if _cd:
                comp = type("CD", (), {
                    "contracting_type": _cd[0], "umbrella_company_name": _cd[1],
                    "company_name": _cd[2], "registration_number": _cd[3],
                })()
        except Exception:
            pass
    if comp:
        if comp.contracting_type:
            company_score += 1
        if comp.contracting_type == "umbrella" and comp.umbrella_company_name:
            company_score += 1
        elif comp.contracting_type == "limited" and comp.company_name:
            company_score += 1
        if comp.contracting_type == "limited" and comp.registration_number:
            company_score += 1
        elif comp.contracting_type == "umbrella":
            company_score += 1  # no reg number needed

    company_pct = int(round(company_score / company_total * 100)) if company_total else 0

    # --- Background Checks & References ---
    checks_score = 0
    checks_total = 5

    # Consent form
    if ConsentRecord:
        consent = s.query(ConsentRecord).filter_by(candidate_id=candidate_id).first()
        if consent and consent.consent_given:
            checks_score += 1

    # Declaration form
    if DeclarationRecord:
        decl = s.query(DeclarationRecord).filter_by(candidate_id=candidate_id).first()
        if decl and decl.legal_name:
            checks_score += 1

    # Documents uploaded
    if Document:
        doc_count = s.query(Document).filter_by(candidate_id=candidate_id).count()
        if doc_count >= 2:
            checks_score += 1

    # Employment history — must cover 5 years back from today
    if EmploymentHistory:
        emp_rows = s.query(EmploymentHistory).filter_by(candidate_id=candidate_id).filter(
            EmploymentHistory.is_gap == False  # noqa: E712
        ).order_by(EmploymentHistory.start_date.asc()).all()
        if emp_rows:
            five_years_ago = (datetime.utcnow() - timedelta(days=5*365)).date()
            earliest_start = None
            for e in emp_rows:
                sd = getattr(e, "start_date", None)
                if sd:
                    if earliest_start is None or sd < earliest_start:
                        earliest_start = sd
            if earliest_start and earliest_start <= five_years_ago:
                checks_score += 1

    # Vetting checks — all live checks must be complete. Req 26 retired
    # Employment History and Address History, so stale rows of those types
    # on in-flight candidates are excluded from both counts.
    if VettingCheck:
        _live_check = VettingCheck.check_type.notin_(["Employment History", "Address History"])
        total_vc = s.query(VettingCheck).filter_by(candidate_id=candidate_id).filter(
            _live_check
        ).count()
        completed_vc = s.query(VettingCheck).filter_by(candidate_id=candidate_id).filter(
            _live_check,
            func.upper(VettingCheck.status).in_(["COMPLETE", "N/A", "QC COMPLETE", "QC NOT REQUIRED", "REFERRAL APPROVED"])
        ).count()
        if total_vc >= 10 and completed_vc >= total_vc:
            checks_score += 1

    checks_pct = int(round(checks_score / checks_total * 100)) if checks_total else 0

    overall_pct = int(round((personal_pct + company_pct + checks_pct) / 3))

    return {
        "personal_pct": min(personal_pct, 100),
        "company_pct": min(company_pct, 100),
        "checks_pct": min(checks_pct, 100),
        "overall_pct": min(overall_pct, 100),
    }


# ---------------------------------------------------------------------------
# Gap detection for employment history
# ---------------------------------------------------------------------------

def _detect_gaps(entries: list, threshold_days: int = 90) -> list:
    """
    Given a list of EmploymentHistory objects sorted by start_date,
    return a list of dicts describing gaps > threshold_days.
    """
    gaps = []
    # Sort by start_date ascending, filter out entries without dates
    dated = [e for e in entries if e.start_date and e.end_date]
    dated.sort(key=lambda e: e.start_date)

    for i in range(len(dated) - 1):
        gap_start = dated[i].end_date
        gap_end = dated[i + 1].start_date
        if gap_start and gap_end:
            delta = (gap_end - gap_start).days
            if delta > threshold_days:
                gaps.append({
                    "from_date": gap_start,
                    "to_date": gap_end,
                    "days": delta,
                    "after_company": dated[i].company_name,
                    "before_company": dated[i + 1].company_name,
                })
    return gaps


# ---------------------------------------------------------------------------
# Ensure tables exist
# ---------------------------------------------------------------------------

@associate_bp.before_app_request
def _create_portal_tables():
    """Create portal-specific tables on first request if they don't exist."""
    if getattr(current_app, "_associate_tables_created", False):
        return
    try:
        _ensure_models()
        Base = _base()
        engine = _engine()
        # Only create tables that don't yet exist (checkfirst=True is default)
        Base.metadata.create_all(engine, checkfirst=True)
        # Ensure new columns exist on tables that may pre-date this code
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                conn.execute(text("ALTER TABLE consent_records ADD COLUMN reference_consent BOOLEAN DEFAULT FALSE"))
            except Exception:
                pass  # Column already exists
            try:
                conn.execute(text("ALTER TABLE consent_records ADD COLUMN signable_envelope_id VARCHAR(64)"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE associate_profiles ADD COLUMN most_recent_employer VARCHAR(300) DEFAULT ''"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE associate_profiles ADD COLUMN contact_current_employer BOOLEAN DEFAULT TRUE"))
            except Exception:
                pass
            for col_stmt in [
                "ALTER TABLE associate_profiles ADD COLUMN passport_number VARCHAR(20) DEFAULT ''",
                "ALTER TABLE associate_profiles ADD COLUMN passport_issuing_country VARCHAR(10) DEFAULT 'GB'",
                "ALTER TABLE associate_profiles ADD COLUMN passport_issue_date DATE",
                "ALTER TABLE associate_profiles ADD COLUMN passport_expiry_date DATE",
                "ALTER TABLE associate_profiles ADD COLUMN driving_licence_number VARCHAR(20) DEFAULT ''",
                "ALTER TABLE associate_profiles ADD COLUMN driving_licence_issuing_country VARCHAR(10) DEFAULT 'GB'",
                "ALTER TABLE associate_profiles ADD COLUMN driving_licence_issue_date DATE",
                "ALTER TABLE associate_profiles ADD COLUMN country_of_birth VARCHAR(100) DEFAULT ''",
                "ALTER TABLE associate_profiles ADD COLUMN town_of_birth VARCHAR(100) DEFAULT ''",
                "ALTER TABLE associate_profiles ADD COLUMN mother_maiden_name VARCHAR(100) DEFAULT ''",
                "ALTER TABLE associate_profiles ADD COLUMN current_address_in_scotland BOOLEAN",
            ]:
                try:
                    conn.execute(text(col_stmt))
                except Exception:
                    pass
        # Ensure new qualification columns exist for Verifile integration
            for col_stmt in [
                "ALTER TABLE qualification_records ADD COLUMN level VARCHAR(100) DEFAULT ''",
                "ALTER TABLE qualification_records ADD COLUMN institution_street VARCHAR(300) DEFAULT ''",
                "ALTER TABLE qualification_records ADD COLUMN institution_town VARCHAR(200) DEFAULT ''",
                "ALTER TABLE qualification_records ADD COLUMN institution_country VARCHAR(10) DEFAULT 'GB'",
            ]:
                try:
                    conn.execute(text(col_stmt))
                except Exception:
                    pass
            # company_details columns added after the table was first created.
            # Without these, existing deployments cannot persist umbrella/limited details.
            for col_stmt in [
                "ALTER TABLE company_details ADD COLUMN umbrella_company_name VARCHAR(300) DEFAULT ''",
                "ALTER TABLE company_details ADD COLUMN vat_number VARCHAR(50) DEFAULT ''",
                "ALTER TABLE company_details ADD COLUMN bank_account_number VARCHAR(20) DEFAULT ''",
                "ALTER TABLE company_details ADD COLUMN bank_sort_code VARCHAR(10) DEFAULT ''",
                "ALTER TABLE company_details ADD COLUMN contact_email VARCHAR(254) DEFAULT ''",
            ]:
                try:
                    conn.execute(text(col_stmt))
                except Exception:
                    pass
        current_app._associate_tables_created = True
    except Exception as exc:
        current_app.logger.warning("Could not create associate portal tables: %s", exc)
        current_app._associate_tables_created = True  # Don't retry every request


def _pending_offers_for(session_obj, candidate_id):
    """Req-046: return list of Application rows where this candidate has a pending offer.

    A "pending offer" is one where status='Offered' and offer_response IS NULL.
    Used by the context processor below so every portal page can render the
    notification badge / banner without each route having to compute it.
    """
    Application = _model("Application")
    if not Application or not candidate_id:
        return []
    try:
        return session_obj.query(Application).filter(
            Application.candidate_id == candidate_id,
            Application.status == "Offered",
            Application.offer_response.is_(None),
        ).all()
    except Exception:
        return []


@associate_bp.context_processor
def _inject_associate():
    """Inject the associate (Candidate) object and pending-offer list into all portal templates."""
    cand_id = session.get("associate_user_id")
    if not cand_id:
        return {"associate": None, "pending_offers": []}
    try:
        Candidate = _model("Candidate")
        engine = _engine()
        if Candidate and engine:
            with SASession(engine) as s:
                cand = s.get(Candidate, cand_id)
                if cand:
                    pending = _pending_offers_for(s, cand_id)
                    # Return simple namespaces so they're session-safe (the SQLA
                    # session closes after this with-block exits)
                    from types import SimpleNamespace
                    pending_safe = [
                        SimpleNamespace(
                            id=app.id,
                            offer_role_title=app.offer_role_title or "",
                            offer_start_date=app.offer_start_date,
                            offer_day_rate=float(app.offer_day_rate) if app.offer_day_rate is not None else None,
                            offer_rate_type=app.offer_rate_type or "day",
                            offer_location=app.offer_location or "",
                            offer_notes=app.offer_notes or "",
                        )
                        for app in pending
                    ]
                    return {
                        "associate": SimpleNamespace(
                            id=cand.id,
                            name=cand.name or "",
                            email=cand.email or "",
                            phone=cand.phone or "",
                        ),
                        "pending_offers": pending_safe,
                    }
        return {"associate": None, "pending_offers": []}
    except Exception:
        return {"associate": None, "pending_offers": []}


# =========================================================================
# AUTH ROUTES
# =========================================================================

@associate_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login with email/password; redirect to 2FA if enabled.
    Rate limited to 10/minute via blueprint before_request (see _rate_limit_login)."""
    Candidate = _model("Candidate")
    engine = _engine()
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next") or "")

    if request.method == "GET":
        return render_template("associate/auth_login.html", next=next_url)

    email = _sanitise(request.form.get("email", "")).strip().lower()
    password = request.form.get("password", "")

    if not email:
        flash("Please enter your email address.", "danger")
        return redirect(url_for("associate.login", next=next_url))

    with SASession(engine) as s:
        cand = s.query(Candidate).filter(Candidate.email.ilike(email)).first()

        if not cand:
            flash("No account found with that email. Please register first.", "info")
            return redirect(url_for("associate.register", next=next_url))

        if hasattr(cand, "is_locked") and cand.is_locked():
            flash("Account temporarily locked. Please try again later.", "danger")
            return redirect(url_for("associate.login", next=next_url))

        if not cand.password_hash:
            flash("Please complete your account setup first. Check your email for a registration link.", "info")
            return redirect(url_for("associate.login", next=next_url))

        if not cand.check_password(password):
            cand.failed_login_attempts = (cand.failed_login_attempts or 0) + 1
            locked = cand.failed_login_attempts >= 5
            if locked:
                cand.locked_until = datetime.utcnow() + timedelta(minutes=15)
                flash("Too many failed attempts. Account locked for 15 minutes.", "danger")
            else:
                flash("Invalid email or password.", "danger")
            s.commit()
            _audit_portal("login", f"Failed portal login (attempt {cand.failed_login_attempts})",
                          candidate_id=cand.id,
                          details={"locked": locked, "email": email},
                          status="failure")
            return redirect(url_for("associate.login", next=next_url))

        # Password correct
        cand.failed_login_attempts = 0
        cand.locked_until = None

        if cand.totp_enabled and cand.totp_secret:
            session["associate_2fa_pending_id"] = cand.id
            session["associate_2fa_next"] = next_url
            s.commit()
            return redirect(url_for("associate.verify_2fa"))

        # Log in directly
        cand.last_login_at = datetime.utcnow()
        session["associate_user_id"] = cand.id
        session.permanent = True  # Use PERMANENT_SESSION_LIFETIME (60 min) instead of browser-session
        s.commit()
        _audit_portal("login", "Portal login (no 2FA)", candidate_id=cand.id)

    flash("Signed in successfully.", "success")
    return redirect(next_url)


@associate_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register a new associate account; send magic link."""
    Candidate = _model("Candidate")
    engine = _engine()
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next") or "")

    if request.method == "GET":
        return render_template("associate/auth_register.html", next=next_url)

    email = _sanitise(request.form.get("email", "")).strip().lower()
    first_name = _sanitise(request.form.get("first_name", "")).strip()
    surname = _sanitise(request.form.get("surname", "")).strip()
    name = _sanitise(request.form.get("name", "")).strip() or f"{first_name} {surname}".strip()
    phone = _sanitise(request.form.get("phone", "")).strip()

    if not email or not name:
        flash("Please provide your name and email address.", "danger")
        return redirect(url_for("associate.register", next=next_url))

    with SASession(engine) as s:
        existing = s.query(Candidate).filter(Candidate.email.ilike(email)).first()
        if existing:
            if existing.password_hash:
                flash("An account with this email already exists. Please sign in.", "info")
                return redirect(url_for("associate.login", next=next_url))
            # Resend magic link
            try:
                _send_magic_link(email, existing.name or name, is_signup=True, next_url=next_url)
            except Exception as e:
                current_app.logger.exception("Magic link send failed: %s", e)
                flash("Unable to send verification email. Please contact support.", "danger")
                return redirect(url_for("associate.register", next=next_url))
            flash("We have sent you a new verification link.", "info")
            return render_template("associate/auth_check_email.html", email=email)

    # Send email first, then create record
    try:
        _send_magic_link(email, name, is_signup=True, next_url=next_url)
    except Exception as e:
        current_app.logger.exception("Magic link send failed: %s", e)
        flash("Unable to send verification email. Please contact support.", "danger")
        return redirect(url_for("associate.register", next=next_url))

    with SASession(engine) as s:
        existing = s.query(Candidate).filter(Candidate.email.ilike(email)).first()
        if existing:
            flash("Check your email to complete registration.", "success")
            return render_template("associate/auth_check_email.html", email=email)

        # current_employer_contact_ok defaults at the column level (True);
        # the per-employer Yes/No on the Employment History page now drives
        # actual reference-contact behaviour.
        cand = Candidate(
            name=name,
            email=email,
            phone=phone,
            email_verified=False,
            source="associate-portal",
        )
        s.add(cand)
        s.commit()

    flash("Check your email to complete registration.", "success")
    return render_template("associate/auth_check_email.html", email=email)


@associate_bp.route("/verify-email", methods=["GET"])
def verify_email():
    """Verify magic-link token and redirect to password setup."""
    from itsdangerous import SignatureExpired, BadSignature

    Candidate = _model("Candidate")
    engine = _engine()
    token = request.args.get("token", "")

    if not token:
        flash("Invalid verification link.", "danger")
        return redirect(url_for("associate.login"))

    try:
        data = _portal_signer().loads(token, max_age=30 * 60)
    except SignatureExpired:
        flash("This link has expired. Please request a new one.", "warning")
        return redirect(url_for("associate.register"))
    except BadSignature:
        flash("Invalid verification link.", "danger")
        return redirect(url_for("associate.login"))

    email = (data.get("email") or "").strip().lower()
    if not email:
        flash("Invalid verification link.", "danger")
        return redirect(url_for("associate.login"))

    with SASession(engine) as s:
        cand = s.query(Candidate).filter(Candidate.email.ilike(email)).first()
        if not cand:
            flash("Account not found. Please register again.", "warning")
            return redirect(url_for("associate.register"))

        cand.email_verified = True
        if hasattr(cand, "email_verified_at"):
            cand.email_verified_at = datetime.utcnow()
        s.commit()

        session["associate_setup_password_id"] = cand.id
        session["associate_setup_password_ts"] = datetime.utcnow().timestamp()
        # Carry next_url from magic link through the setup flow
        token_next = _safe_next_url(data.get("next") or "")
        if token_next:
            session["associate_setup_next"] = token_next

    return redirect(url_for("associate.set_password"))


@associate_bp.route("/set-password", methods=["GET", "POST"])
def set_password():
    """Set password after email verification."""
    Candidate = _model("Candidate")
    engine = _engine()

    cand_id = session.get("associate_setup_password_id")
    setup_ts = session.get("associate_setup_password_ts")
    # S9: Expire setup session keys after 15 minutes
    if not cand_id or (setup_ts and (datetime.utcnow().timestamp() - setup_ts) > 900):
        session.pop("associate_setup_password_id", None)
        session.pop("associate_setup_password_ts", None)
        flash("Please use the link from your email.", "warning")
        return redirect(url_for("associate.login"))

    if request.method == "GET":
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            if not cand:
                session.pop("associate_setup_password_id", None)
                flash("Account not found.", "danger")
                return redirect(url_for("associate.register"))
            return render_template("associate/auth_set_password.html", name=cand.name, email=cand.email)

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id)
        if not cand:
            session.pop("associate_setup_password_id", None)
            flash("Account not found.", "danger")
            return redirect(url_for("associate.register"))

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("associate/auth_set_password.html", name=cand.name, email=cand.email)

        valid, msg = _validate_password(password)
        if not valid:
            flash(msg, "danger")
            return render_template("associate/auth_set_password.html", name=cand.name, email=cand.email)

        cand.set_password(password)
        s.commit()

        session["associate_setup_2fa_id"] = cand.id
        session["associate_setup_2fa_ts"] = datetime.utcnow().timestamp()
        session.pop("associate_setup_password_id", None)
        session.pop("associate_setup_password_ts", None)

        flash("Password set successfully. Now set up two-factor authentication.", "success")
        return redirect(url_for("associate.setup_2fa"))


@associate_bp.route("/setup-2fa", methods=["GET", "POST"])
def setup_2fa():
    """Set up TOTP 2FA with QR code."""
    import pyotp

    Candidate = _model("Candidate")
    engine = _engine()

    cand_id = session.get("associate_setup_2fa_id")
    setup_ts = session.get("associate_setup_2fa_ts")
    # S9: Expire setup session keys after 15 minutes
    if not cand_id or (setup_ts and (datetime.utcnow().timestamp() - setup_ts) > 900):
        session.pop("associate_setup_2fa_id", None)
        session.pop("associate_setup_2fa_ts", None)
        flash("Session expired. Please sign in again.", "warning")
        return redirect(url_for("associate.login"))

    if request.method == "GET":
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            if not cand:
                session.pop("associate_setup_2fa_id", None)
                flash("Account not found.", "danger")
                return redirect(url_for("associate.register"))

            if not cand.totp_secret:
                cand.totp_secret = cand.generate_totp_secret()
                s.commit()

            secret = cand.totp_secret
            email = cand.email

        totp = pyotp.TOTP(secret)
        prov_uri = totp.provisioning_uri(name=email, issuer_name="Optimus Portal")

        # Generate QR code
        qr_data_uri = ""
        try:
            import qrcode as qrlib
            qr = qrlib.QRCode(version=1, box_size=10, border=4)
            qr.add_data(prov_uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            qr_data_uri = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
        except Exception:
            import urllib.parse
            encoded = urllib.parse.quote(prov_uri)
            qr_data_uri = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={encoded}"

        return render_template("associate/auth_setup_2fa.html", qr_url=qr_data_uri, secret=secret, email=email)

    # POST: verify token (field name matches auth_setup_2fa.html: name="totp_code")
    token_code = (request.form.get("totp_code") or request.form.get("token", "")).strip().replace(" ", "")
    if not token_code or len(token_code) != 6 or not token_code.isdigit():
        flash("Please enter a valid 6-digit code.", "danger")
        return redirect(url_for("associate.setup_2fa"))

    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id)
        if not cand:
            session.pop("associate_setup_2fa_id", None)
            flash("Account not found.", "danger")
            return redirect(url_for("associate.register"))

        if not cand.verify_totp(token_code):
            flash("Invalid code. Please try again.", "danger")
            return redirect(url_for("associate.setup_2fa"))

        cand.totp_enabled = True
        backup_codes = cand.generate_backup_codes(count=10)
        cand.last_login_at = datetime.utcnow()
        s.commit()

        session["associate_user_id"] = cand.id
        session.permanent = True
        session.pop("associate_setup_2fa_id", None)
        session.pop("associate_setup_2fa_next", None)

    setup_next = session.pop("associate_setup_next", "") or ""
    return render_template("associate/auth_backup_codes.html",
                           backup_codes=backup_codes,
                           next_url=setup_next)


@associate_bp.route("/verify-2fa", methods=["GET", "POST"])
def verify_2fa():
    """Verify TOTP during login."""
    Candidate = _model("Candidate")
    engine = _engine()

    cand_id = session.get("associate_2fa_pending_id")
    next_url = _safe_next_url(session.get("associate_2fa_next") or "")

    if not cand_id:
        flash("Please sign in first.", "warning")
        return redirect(url_for("associate.login"))

    if request.method == "GET":
        return render_template("associate/auth_verify_2fa.html")

    # Template sends totp_code or backup_code (auth_verify_2fa.html)
    totp_input = (request.form.get("totp_code") or request.form.get("token", "")).strip().replace(" ", "")
    backup_input = (request.form.get("backup_code", "")).strip().replace(" ", "")
    use_backup = bool(backup_input)
    token_code = backup_input if use_backup else totp_input

    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id)
        if not cand:
            session.pop("associate_2fa_pending_id", None)
            flash("Account not found.", "danger")
            return redirect(url_for("associate.login"))

        verified = False
        if use_backup:
            if cand.verify_backup_code(token_code):
                verified = True
        else:
            if len(token_code) == 6 and token_code.isdigit() and cand.verify_totp(token_code):
                verified = True

        if not verified:
            flash("Invalid code. Please try again.", "danger")
            return redirect(url_for("associate.verify_2fa"))

        cand.last_login_at = datetime.utcnow()
        session["associate_user_id"] = cand.id
        session.permanent = True  # Use PERMANENT_SESSION_LIFETIME (60 min) instead of browser-session
        session.pop("associate_2fa_pending_id", None)
        session.pop("associate_2fa_next", None)
        s.commit()
        _audit_portal("login", "Portal login via 2FA", candidate_id=cand.id)

    flash("Signed in successfully.", "success")
    return redirect(next_url)


@associate_bp.route("/logout")
def logout():
    """Clear session and redirect to login."""
    cand_id = session.get("associate_user_id")
    _audit_portal("logout", "Portal logout", candidate_id=cand_id)
    for key in list(session.keys()):
        if key.startswith("associate_"):
            session.pop(key, None)
    # S7: Regenerate session ID to prevent session fixation
    session.modified = True
    flash("You have been signed out.", "success")
    return redirect(url_for("associate.login"))


# =========================================================================
# PROTECTED ROUTES
# =========================================================================

@associate_bp.route("/dashboard")
@_require_login
def dashboard():
    """Main dashboard with profile completeness progress bars."""
    Candidate = _model("Candidate")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id)
        if not cand:
            session.pop("associate_user_id", None)
            flash("Session expired. Please sign in again.", "warning")
            return redirect(url_for("associate.login"))

        completeness = _calc_completeness(s, cand_id)

        # Build next actions based on completeness gaps
        next_actions = []
        if completeness.get("personal_pct", 0) < 100:
            next_actions.append({
                "icon": "user",
                "title": "Complete Personal Details",
                "description": "Fill in your personal information to complete your profile.",
                "url": "/portal/personal-details"
            })
        if completeness.get("company_pct", 0) < 100:
            next_actions.append({
                "icon": "building",
                "title": "Add Umbrella or Ltd Company Details",
                "description": "Provide your umbrella or limited company details.",
                "url": "/portal/company-details"
            })
        if completeness.get("checks_pct", 0) < 100:
            next_actions.append({
                "icon": "folder-open",
                "title": "Upload Documentation to Support Vetting",
                "description": "Upload documents such as ID, proof of address, and qualifications.",
                "url": "/portal/documents"
            })

        # Recent activity from candidate notes
        CandidateNote = _model("CandidateNote")
        recent_activity = []
        if CandidateNote:
            notes = s.query(CandidateNote).filter_by(candidate_id=cand_id).order_by(
                CandidateNote.created_at.desc()
            ).limit(5).all()
            for note in notes:
                recent_activity.append({
                    "content": note.content,
                    "timestamp": note.created_at.strftime("%d %b %Y %H:%M") if note.created_at else "",
                    "created_at": note.created_at,
                })

        return render_template(
            "associate/dashboard.html",
            associate=cand,
            overall_pct=completeness.get("overall_pct", 0),
            personal_pct=completeness.get("personal_pct", 0),
            company_pct=completeness.get("company_pct", 0),
            vetting_pct=completeness.get("checks_pct", 0),
            completeness=completeness,
            next_actions=next_actions,
            recent_activity=recent_activity,
        )


@associate_bp.route("/personal-details", methods=["GET", "POST"])
@_require_login
def personal_details():
    """Extended personal details form."""
    Candidate = _model("Candidate")
    AssociateProfile = _portal_model("AssociateProfile")
    engine = _engine()
    cand_id = _get_associate_id()

    if request.method == "GET":
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            profile = s.query(AssociateProfile).filter_by(candidate_id=cand_id).first() if AssociateProfile else None
            CompanyDetails = _portal_model("CompanyDetails")
            company = s.query(CompanyDetails).filter_by(candidate_id=cand_id).first() if CompanyDetails else None
            employment_company = ""
            if company:
                if company.contracting_type == "umbrella" and company.umbrella_company_name:
                    employment_company = company.umbrella_company_name
                elif company.contracting_type == "limited" and company.company_name:
                    employment_company = company.company_name
            Document = _model("Document")
            cv_document = None
            if Document:
                cv_document = s.query(Document).filter_by(candidate_id=cand_id, doc_type="cv").first()
            return render_template("associate/personal_details.html", associate=cand, profile=profile, employment_company=employment_company, cv_document=cv_document)

    # POST
    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id)
        if not cand:
            flash("Session expired.", "danger")
            return redirect(url_for("associate.login"))

        profile = s.query(AssociateProfile).filter_by(candidate_id=cand_id).first() if AssociateProfile else None
        if not profile and AssociateProfile:
            profile = AssociateProfile(candidate_id=cand_id)
            s.add(profile)

        # Update candidate base fields — form sends first_name + surname, not "name"
        first_name = _sanitise(request.form.get("first_name", "")).strip()
        surname = _sanitise(request.form.get("surname", "")).strip()
        if first_name or surname:
            cand.name = f"{first_name} {surname}".strip()
        cand.email = _sanitise(request.form.get("email", cand.email or "")).strip().lower()
        # Form sends contact_number, not phone
        contact_number = _sanitise(request.form.get("contact_number", "")).strip()
        if contact_number:
            cand.phone = contact_number
        # Reference-contact preference removed from Personal Details — captured
        # per-employer on the Employment History page instead.

        # Update profile fields
        if profile:
            profile.title = _sanitise(request.form.get("title", ""))
            profile.first_name = _sanitise(request.form.get("first_name", ""))
            profile.surname = _sanitise(request.form.get("surname", ""))
            profile.aliases = _sanitise(request.form.get("aliases", ""))
            profile.previous_names = _sanitise(request.form.get("previous_names", ""))
            profile.previous_first_names = _sanitise(request.form.get("previous_first_names", ""))
            profile.previous_surnames = _sanitise(request.form.get("previous_surnames", ""))
            profile.dob = _parse_date(request.form.get("dob", ""))
            profile.address_line1 = _sanitise(request.form.get("address_line1", ""))
            profile.address_line2 = _sanitise(request.form.get("address_line2", ""))
            profile.city = _sanitise(request.form.get("city", ""))
            profile.postcode = _sanitise(request.form.get("postcode", ""))
            profile.country = _sanitise(request.form.get("country", "United Kingdom"))
            # Req 26 — Scotland answer drives the DBS criminal-record variant.
            _scotland = (request.form.get("current_address_in_scotland", "") or "").strip().lower()
            profile.current_address_in_scotland = (
                True if _scotland == "yes"
                else (False if _scotland == "no" else None)
            )
            profile.contact_number = _sanitise(request.form.get("contact_number", ""))
            profile.emergency_contact_name = _sanitise(request.form.get("emergency_contact_name", ""))
            profile.emergency_contact_phone = _sanitise(request.form.get("emergency_contact_phone", ""))
            # P8: Gender field
            profile.gender = _sanitise(request.form.get("gender", ""))
            profile.emergency_contact_relationship = _sanitise(request.form.get("emergency_contact_relationship", ""))
            profile.current_salary = _sanitise(request.form.get("current_salary", ""))
            profile.expected_salary = _sanitise(request.form.get("expected_salary", ""))
            profile.available_from = _parse_date(request.form.get("available_from", ""))
            profile.national_insurance_number = _sanitise(request.form.get("national_insurance_number", ""))
            profile.passport_number = _sanitise(request.form.get("passport_number", ""))
            profile.passport_issuing_country = _sanitise(request.form.get("passport_issuing_country", "GB"))
            profile.passport_issue_date = _parse_date(request.form.get("passport_issue_date", ""))
            profile.passport_expiry_date = _parse_date(request.form.get("passport_expiry_date", ""))
            profile.driving_licence_number = _sanitise(request.form.get("driving_licence_number", ""))
            profile.driving_licence_issuing_country = _sanitise(request.form.get("driving_licence_issuing_country", "GB"))
            profile.driving_licence_issue_date = _parse_date(request.form.get("driving_licence_issue_date", ""))
            profile.country_of_birth = _sanitise(request.form.get("country_of_birth", ""))
            profile.town_of_birth = _sanitise(request.form.get("town_of_birth", ""))
            profile.mother_maiden_name = _sanitise(request.form.get("mother_maiden_name", ""))
            profile.most_recent_employer = _sanitise(request.form.get("most_recent_employer", ""))
            # contact_current_employer saved on Candidate model (line above), not profile
            profile.unsubscribed = request.form.get("unsubscribed") == "1"

        _add_note(s, cand_id, "Personal details updated via Associate Portal.")
        try:
            s.commit()
            current_app.logger.info(f"Personal details saved for candidate {cand_id}")
        except Exception:
            s.rollback()
            # Might be missing column on the profile — try to self-heal then
            # retry the save.
            try:
                eng = _engine()
                with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    for stmt in [
                        "ALTER TABLE associate_profiles ADD COLUMN most_recent_employer VARCHAR(300) DEFAULT ''",
                    ]:
                        try:
                            conn.execute(text(stmt))
                        except Exception:
                            pass
            except Exception:
                pass
            current_app.logger.error(f"Personal details save failed for candidate {cand_id}: {e}")
            flash(f"Failed to save personal details. Please try again.", "danger")
            return redirect(url_for("associate.personal_details"))

    flash("Personal details saved.", "success")
    return redirect(url_for("associate.personal_details"))


@associate_bp.route("/profile-picture", methods=["POST"])
@_require_login
def upload_profile_picture():
    """Profile picture upload has been removed."""
    flash("Profile picture upload is no longer available.", "info")
    return redirect(url_for("associate.personal_details"))


@associate_bp.route("/profile-pic/<filename>")
@_require_login
def serve_profile_pic(filename):
    """Serve profile picture files."""
    from flask import send_from_directory
    upload_dir = os.path.join(os.getcwd(), "uploads", "profile_pictures")
    return send_from_directory(upload_dir, filename)


@associate_bp.route("/upload-cv", methods=["POST"])
@_require_login
def upload_cv():
    """Upload or replace CV document."""
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    file = request.files.get("cv_file")
    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("associate.personal_details"))

    allowed = {"pdf", "doc", "docx"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        flash("Please upload a PDF, DOC, or DOCX file.", "danger")
        return redirect(url_for("associate.personal_details"))

    # Save to same location as documents page (static/uploads/associate_docs/)
    saved = _save_file(file)
    if not saved:
        flash("Failed to save CV.", "danger")
        return redirect(url_for("associate.personal_details"))

    with SASession(engine) as s:
        if Document:
            # Remove old CV if exists
            old_cv = s.query(Document).filter_by(candidate_id=cand_id, doc_type="cv").first()
            if old_cv:
                s.delete(old_cv)
            doc = Document(
                candidate_id=cand_id,
                doc_type="cv",
                filename=saved["filename"],
                original_name=saved["original_name"],
            )
            s.add(doc)
            s.commit()

            # Auto-generate AI summary + score in background (don't block upload)
            doc_id = doc.id
            def _bg_summarise(app, cand_id, doc_id):
                with app.app_context():
                    try:
                        from app import (
                            extract_cv_text, ai_summarise, ai_score_with_explanation,
                            _log_ai_score_activity, engine as app_engine,
                            Document as AppDoc, Candidate as AppCand,
                            Application as AppApplication, Job as AppJob,
                        )
                        from sqlalchemy.orm import Session as BgSession
                        from sqlalchemy import select as bg_select
                        with BgSession(app_engine) as bg_s:
                            bg_doc = bg_s.get(AppDoc, doc_id)
                            if not bg_doc:
                                return
                            cv_text = extract_cv_text(bg_doc)
                            if not cv_text or len(cv_text.strip()) <= 50:
                                return

                            summary = ai_summarise(cv_text)
                            bg_cand = bg_s.get(AppCand, cand_id)
                            if bg_cand and summary:
                                bg_cand.ai_summary = summary

                            # Score against candidate's most recent application
                            latest_app = bg_s.scalar(
                                bg_select(AppApplication)
                                .where(AppApplication.candidate_id == cand_id)
                                .order_by(AppApplication.created_at.desc())
                            )
                            latest_job = (
                                bg_s.get(AppJob, latest_app.job_id)
                                if latest_app and latest_app.job_id else None
                            )
                            if latest_app and latest_job:
                                try:
                                    result = ai_score_with_explanation(
                                        latest_job.description or "",
                                        cv_text or "",
                                    )
                                    latest_app.ai_score = int(result.get("final", 0) or 0)
                                    latest_app.ai_explanation = (result.get("explanation") or "")[:7999]
                                    _log_ai_score_activity(bg_s, cand_id, latest_app.ai_score, latest_job)
                                except Exception as score_exc:
                                    print(f"[BG] AI score failed for candidate {cand_id}: {score_exc}")

                            bg_s.commit()
                            print(f"[BG] AI summary/score done for candidate {cand_id}")
                    except Exception as e:
                        print(f"[BG] AI summary failed for candidate {cand_id}: {e}")

            import threading
            threading.Thread(
                target=_bg_summarise,
                args=(current_app._get_current_object(), cand_id, doc_id),
                daemon=True
            ).start()

    # Add activity note for CV upload
    try:
        with SASession(engine) as ns:
            _add_note(ns, cand_id, f"CV uploaded via Portal: {saved['original_name']}")
            ns.commit()
    except Exception:
        pass

    flash("CV uploaded successfully.", "success")
    return redirect(url_for("associate.personal_details"))


@associate_bp.route("/company-details", methods=["GET", "POST"])
@_require_login
def company_details():
    """Contracting entity details: Umbrella or Limited Company."""
    CompanyDetails = _portal_model("CompanyDetails")
    engine = _engine()
    cand_id = _get_associate_id()

    # Contradiction Fix 6: Query approved umbrella companies from DB instead of hardcoded list
    ApprovedUmbrella = _model("ApprovedUmbrella")
    umbrella_options = [
        "Trafalgar Workforce Solutions Ltd",
        "Paystream",
    ]  # fallback if model not available
    if ApprovedUmbrella:
        try:
            with SASession(engine) as _s:
                db_umbrellas = _s.query(ApprovedUmbrella).filter_by(is_active=True).order_by(ApprovedUmbrella.name).all()
                if db_umbrellas:
                    umbrella_options = [u.name for u in db_umbrellas]
        except Exception:
            pass  # fallback to hardcoded list

    if request.method == "GET":
        with SASession(engine) as s:
            comp = s.query(CompanyDetails).filter_by(candidate_id=cand_id).first() if CompanyDetails else None
            # Raw-SQL fallback — partial ORM init can leave CompanyDetails
            # unresolvable on some Gunicorn workers.
            if comp is None:
                try:
                    _cd_row = s.execute(text(
                        "SELECT id, candidate_id, contracting_type, company_name, "
                        "       registration_number, vat_registered, vat_number, "
                        "       bank_account_number, bank_sort_code, "
                        "       umbrella_company_name, contact_email, updated_at "
                        "FROM company_details WHERE candidate_id = :cid"
                    ).bindparams(cid=cand_id)).first()
                    if _cd_row:
                        comp = type("CD", (), {
                            col: getattr(_cd_row, col, None)
                            for col in (
                                "id", "candidate_id", "contracting_type",
                                "company_name", "registration_number",
                                "vat_registered", "vat_number",
                                "bank_account_number", "bank_sort_code",
                                "umbrella_company_name", "contact_email",
                                "updated_at",
                            )
                        })()
                except Exception:
                    pass

            # Check if any active engagement is outside IR35
            Application = _model("Application")
            Engagement = _model("Engagement")
            allow_limited = False
            if Application and Engagement:
                apps = s.query(Application).filter_by(candidate_id=cand_id).all()
                for app in apps:
                    if hasattr(app, "job") and app.job and hasattr(app.job, "engagement_id") and app.job.engagement_id:
                        eng = s.query(Engagement).get(app.job.engagement_id)
                        if eng and getattr(eng, "ir35_status", "inside") == "outside":
                            allow_limited = True
                            break

            # Query existing company documents
            Document = _model("Document")
            company_docs = {}
            if Document:
                for dtype in ["cert_incorporation", "pipl_insurance", "vat_certificate"]:
                    doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type=dtype).first()
                    if doc:
                        company_docs[dtype] = doc

            # Conduct Regulations 2003 state — displayed inline so the
            # associate can opt-in/opt-out on the same page. Resilient to
            # partial ORM init AND missing columns on prod by falling
            # back to raw SQL and swallowing any load error.
            conduct_current_choice = None
            conduct_decision_at = None
            conduct_signed_name = ""
            conduct_portal_name = ""
            try:
                row = s.execute(text("""
                    SELECT conduct_regs_opted_in,
                           conduct_regs_decision_at,
                           conduct_regs_signed_name,
                           name
                    FROM candidates
                    WHERE id = :cid
                """).bindparams(cid=cand_id)).first()
                if row:
                    _opted = row[0]
                    if _opted is True:
                        conduct_current_choice = "opt_in"
                    elif _opted is False:
                        conduct_current_choice = "opt_out"
                    conduct_decision_at = row[1]
                    conduct_signed_name = (row[2] or "").strip()
                    conduct_portal_name = (row[3] or "").strip()
            except Exception:
                # Column likely missing on this DB — leave defaults.
                current_app.logger.exception(
                    "conduct regs preload failed for cand=%s", cand_id
                )

            return render_template(
                "associate/company_details.html",
                company=comp,
                umbrella_options=umbrella_options,
                allow_limited=allow_limited,
                company_docs=company_docs,
                conduct_current_choice=conduct_current_choice,
                conduct_decision_at=conduct_decision_at,
                conduct_signed_name=conduct_signed_name,
                conduct_portal_name=conduct_portal_name,
            )

    # Diagnostic: record what the POST actually contained, so if the save
    # silently no-ops we can see exactly why in Railway logs.
    entity_type_raw = request.form.get("entity_type", "")
    umbrella_raw = request.form.get("umbrella_company", "")
    current_app.logger.info(
        "company_details POST | cand_id=%s | CompanyDetails_resolved=%s | entity_type=%r | umbrella_company=%r",
        cand_id, bool(CompanyDetails), entity_type_raw, umbrella_raw,
    )

    # Pre-compute sanitised values so both the ORM and the raw-SQL
    # fallback path use the same inputs.
    _values = {
        "cid": cand_id,
        "contracting_type": _sanitise(entity_type_raw),
        "company_name": _sanitise(request.form.get("company_name", "")),
        "registration_number": _sanitise(request.form.get("company_reg_number", "")),
        "vat_registered": (request.form.get("vat_registered") == "yes"),
        "vat_number": _sanitise(request.form.get("vat_number", "")),
        "bank_account_number": _sanitise(request.form.get("bank_account_number", "")),
        "bank_sort_code": _sanitise(request.form.get("bank_sort_code", "")),
        "umbrella_company_name": _sanitise(umbrella_raw),
    }

    with SASession(engine) as s:
        comp = s.query(CompanyDetails).filter_by(candidate_id=cand_id).first() if CompanyDetails else None
        was_new = comp is None

        # If the ORM class couldn't be resolved on this worker (partial
        # model init), persist via raw SQL so the Save still works.
        if CompanyDetails is None:
            try:
                existing = s.execute(
                    text("SELECT id FROM company_details WHERE candidate_id = :cid"),
                    {"cid": cand_id},
                ).first()
                if existing:
                    s.execute(text("""
                        UPDATE company_details SET
                          contracting_type = :contracting_type,
                          company_name = :company_name,
                          registration_number = :registration_number,
                          vat_registered = :vat_registered,
                          vat_number = :vat_number,
                          bank_account_number = :bank_account_number,
                          bank_sort_code = :bank_sort_code,
                          umbrella_company_name = :umbrella_company_name,
                          updated_at = CURRENT_TIMESTAMP
                        WHERE candidate_id = :cid
                    """), _values)
                else:
                    s.execute(text("""
                        INSERT INTO company_details (
                          candidate_id, contracting_type, company_name,
                          registration_number, vat_registered, vat_number,
                          bank_account_number, bank_sort_code,
                          umbrella_company_name, created_at, updated_at
                        ) VALUES (
                          :cid, :contracting_type, :company_name,
                          :registration_number, :vat_registered, :vat_number,
                          :bank_account_number, :bank_sort_code,
                          :umbrella_company_name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                    """), _values)
            except Exception as raw_exc:
                current_app.logger.exception(
                    "company_details raw-SQL upsert failed: %s", raw_exc,
                )

        if not comp and CompanyDetails:
            comp = CompanyDetails(candidate_id=cand_id)
            s.add(comp)

        if comp:
            comp.contracting_type = _values["contracting_type"]
            comp.company_name = _values["company_name"]
            comp.registration_number = _values["registration_number"]
            comp.vat_registered = _values["vat_registered"]
            comp.vat_number = _values["vat_number"]
            comp.bank_account_number = _values["bank_account_number"]
            comp.bank_sort_code = _values["bank_sort_code"]
            comp.umbrella_company_name = _values["umbrella_company_name"]
            # contact_email is owned by staff on the candidate card in the
            # app; the associate-side form does not collect it. Do not
            # overwrite an existing staff-entered value on save.

        # Handle Ltd Co document uploads
        Document = _model("Document")
        if Document and comp and comp.contracting_type == "limited":
            import uuid
            for doc_type_key in ["cert_incorporation", "pipl_insurance", "vat_certificate"]:
                file = request.files.get(doc_type_key)
                if file and file.filename:
                    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "pdf"
                    if ext not in {"pdf", "doc", "docx", "jpg", "jpeg", "png"}:
                        flash(f"Invalid file type for {doc_type_key}. Allowed: PDF, DOC, DOCX, JPG, PNG.", "danger")
                        continue
                    upload_dir = os.path.join("uploads", "company_docs")
                    os.makedirs(upload_dir, exist_ok=True)
                    safe_name = f"{cand_id}_{doc_type_key}_{uuid.uuid4().hex[:8]}.{ext}"
                    filepath = os.path.join(upload_dir, safe_name)
                    file.save(filepath)
                    # Replace existing doc of this type
                    old_doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type=doc_type_key).first()
                    if old_doc:
                        s.delete(old_doc)
                    new_doc = Document(candidate_id=cand_id, doc_type=doc_type_key, filename=safe_name, original_name=file.filename)
                    s.add(new_doc)

        _add_note(s, cand_id, "Company details updated via Associate Portal.")
        try:
            s.commit()
        except Exception as exc:
            s.rollback()
            current_app.logger.exception("company_details save failed: %s", exc)
            flash("We couldn't save your company details. Please try again or contact support.", "danger")
            return redirect(url_for("associate.company_details"))

        # Diagnostic: re-read the row and log what's actually persisted.
        # Use the ORM class if available, else raw SQL — so the "no row"
        # warning only fires if the DB genuinely doesn't have a matching
        # row, not because the class failed to resolve.
        persisted_entity = persisted_umbrella = persisted_company = None
        row_id = None
        if CompanyDetails is not None:
            verify = s.query(CompanyDetails).filter_by(candidate_id=cand_id).first()
            if verify:
                persisted_entity = getattr(verify, "contracting_type", None)
                persisted_umbrella = getattr(verify, "umbrella_company_name", None)
                persisted_company = getattr(verify, "company_name", None)
                row_id = getattr(verify, "id", None)
        else:
            raw_row = s.execute(
                text("""
                    SELECT id, contracting_type, umbrella_company_name, company_name
                    FROM company_details
                    WHERE candidate_id = :cid
                """),
                {"cid": cand_id},
            ).first()
            if raw_row:
                row_id = raw_row.id
                persisted_entity = raw_row.contracting_type
                persisted_umbrella = raw_row.umbrella_company_name
                persisted_company = raw_row.company_name
        current_app.logger.info(
            "company_details POST done | cand_id=%s | was_new=%s | orm=%s | row_id=%s | entity=%r | umbrella=%r",
            cand_id, was_new, bool(CompanyDetails),
            row_id, persisted_entity, persisted_umbrella,
        )

    # Echo what landed in the DB so the user can spot a silent no-op without
    # needing access to server logs. Entity and umbrella/company name come
    # straight from the row we just verified after commit.
    if row_id is None:
        # Retained diagnostic for the silent no-op case (no row after commit).
        flash(
            "Save attempt reached the server but no company row exists yet. "
            "Please refresh and try again, or contact support.",
            "warning",
        )
    else:
        flash("Company details saved.", "success")
    return redirect(url_for("associate.company_details"))


@associate_bp.route("/consent-form", methods=["GET", "POST"])
@_require_login
def consent_form():
    """Digital consent form with e-signature."""
    _ensure_models()
    ConsentRecord = _portal_model("ConsentRecord")
    cand_id = _get_associate_id()

    if ConsentRecord is None:
        current_app.logger.error("ConsentRecord model not available — cannot render consent form.")
        flash("Consent form is temporarily unavailable. Please try again later.", "danger")
        return redirect(url_for("associate.dashboard"))

    if not cand_id:
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("associate.login"))

    try:
        engine = _engine()
    except Exception:
        current_app.logger.exception("Failed to obtain database engine for consent form.")
        flash("A system error occurred. Please try again later.", "danger")
        return redirect(url_for("associate.dashboard"))

    if request.method == "GET":
        try:
            with SASession(engine) as s:
                consent = s.query(ConsentRecord).filter_by(candidate_id=cand_id).order_by(
                    ConsentRecord.created_at.desc()
                ).first()

                # Self-heal: if no ConsentRecord exists but a Signable webhook
                # has previously logged "Consent Form signed via Signable" for
                # this candidate, create the record retroactively so the
                # completion calc and "already signed" view both reflect it.
                if consent is None:
                    CandidateNote = _model("CandidateNote")
                    Candidate = _model("Candidate")
                    if CandidateNote and Candidate:
                        signed_note = s.query(CandidateNote).filter(
                            CandidateNote.candidate_id == cand_id,
                            CandidateNote.user_email == "Signable",
                            CandidateNote.content.ilike("%Consent Form signed via Signable%"),
                        ).order_by(CandidateNote.created_at.desc()).first()
                        if signed_note:
                            cand = s.get(Candidate, cand_id)
                            consent = ConsentRecord(
                                candidate_id=cand_id,
                                consent_given=True,
                                signed_date=signed_note.created_at or datetime.utcnow(),
                                legal_name=(getattr(cand, "name", None) or ""),
                                ip_address="",
                            )
                            s.add(consent)
                            s.commit()

                reference_consent = getattr(consent, "reference_consent", False) if consent else False
                Candidate = _model("Candidate")
                _cand = s.get(Candidate, cand_id) if Candidate else None
                portal_name = (getattr(_cand, "name", None) or "").strip()
                return render_template(
                    "associate/consent_form.html",
                    consent=consent,
                    already_signed=consent is not None,
                    reference_consent=reference_consent,
                    portal_name=portal_name,
                )
        except Exception:
            current_app.logger.exception("Error loading consent form for candidate %s.", cand_id)
            flash("Could not load consent form. Please try again.", "danger")
            return redirect(url_for("associate.dashboard"))

    # --- POST handling ---
    try:
        with SASession(engine) as s:
            # Contradiction Fix 4: Manual consent PDF upload fallback
            manual_file = request.files.get("manual_consent")
            if manual_file and manual_file.filename:
                import uuid as _uuid
                ext = manual_file.filename.rsplit(".", 1)[-1].lower() if "." in manual_file.filename else "pdf"
                if ext not in {"pdf", "doc", "docx", "jpg", "jpeg", "png"}:
                    flash("Invalid file type for consent form. Allowed: PDF, DOC, DOCX, JPG, PNG.", "danger")
                    return redirect(url_for("associate.consent_form"))

                upload_dir = os.path.join("uploads", "consent_forms")
                os.makedirs(upload_dir, exist_ok=True)
                safe_name = f"{cand_id}_consent_{_uuid.uuid4().hex[:8]}.{ext}"
                filepath = os.path.join(upload_dir, safe_name)
                manual_file.save(filepath)

                # Save as Document record
                Document = _model("Document")
                if Document:
                    # Remove old consent doc if exists
                    old_doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type="consent_signed").first()
                    if old_doc:
                        s.delete(old_doc)
                    new_doc = Document(
                        candidate_id=cand_id,
                        doc_type="consent_signed",
                        filename=safe_name,
                        original_name=manual_file.filename
                    )
                    s.add(new_doc)

                # Create ConsentRecord for the manual upload.
                # The Reference Consent question was removed from the digital
                # form (referencing permission is now captured per-employer on
                # the Employment History page); default to True so downstream
                # checks that read the flag don't regress.
                consent = ConsentRecord(
                    candidate_id=cand_id,
                    consent_given=True,
                    reference_consent=True,
                    secondary_employment=request.form.get("has_secondary_employment") == "yes",
                    secondary_employment_details=_sanitise(request.form.get("secondary_employment_details", "")),
                    legal_name=_sanitise(request.form.get("legal_name", "Manual Upload")).strip() or "Manual Upload",
                    signed_date=datetime.utcnow(),
                    ip_address=request.remote_addr or "",
                )
                s.add(consent)
                _add_note(s, cand_id, f"Consent form uploaded manually (file: {manual_file.filename}).")

                s.commit()

                flash("Signed consent form uploaded ✓ — moving you to the next stage.", "success")
                return redirect(_next_vetting_stage_url(cand_id))

            # Original digital consent flow — legal_name auto-set from candidate
            Candidate = _model("Candidate")
            cand = s.get(Candidate, cand_id) if Candidate else None
            legal_name = ((cand.name if cand and getattr(cand, "name", None) else "") or "").strip()
            if not legal_name:
                legal_name = "Associate"

            consent = ConsentRecord(
                candidate_id=cand_id,
                consent_given=True,  # signing the form implies consent
                # Reference Consent question removed from the digital form
                # (per-employer permissions cover this); default True so
                # downstream gates don't regress.
                reference_consent=True,
                secondary_employment=request.form.get("has_secondary_employment") == "yes",
                secondary_employment_details=_sanitise(request.form.get("secondary_employment_details", "")),
                legal_name=legal_name,
                signed_date=datetime.utcnow(),
                ip_address=request.remote_addr or "",
            )
            s.add(consent)
            _add_note(s, cand_id, f"Consent form signed by '{legal_name}' from IP {request.remote_addr}.")

            s.commit()

        flash("Consent form signed ✓ — moving you to the next stage.", "success")
        return redirect(_next_vetting_stage_url(cand_id))

    except Exception:
        current_app.logger.exception("Error saving consent form for candidate %s.", cand_id)
        flash("An error occurred while saving your consent form. Please try again.", "danger")
        return redirect(url_for("associate.consent_form"))


@associate_bp.route("/consent-form/mark-signed", methods=["POST"])
@_require_login
def consent_mark_signed():
    """Associate-triggered fallback when the Signable webhook hasn't reached
    us (webhook misconfigured, delayed, or the envelope was signed outside
    the embedded widget). Creates or updates a ConsentRecord so the portal
    and completion calc reflect the signing immediately."""
    ConsentRecord = _portal_model("ConsentRecord")
    Candidate = _model("Candidate")
    if ConsentRecord is None:
        flash("Consent record model not available — cannot mark as signed.", "danger")
        return redirect(url_for("associate.consent_form"))
    cand_id = _get_associate_id()
    engine = _engine()
    try:
        with SASession(engine) as s:
            consent = s.query(ConsentRecord).filter_by(candidate_id=cand_id).first()
            if consent is None:
                consent = ConsentRecord(candidate_id=cand_id)
                s.add(consent)
            consent.consent_given = True
            consent.signed_date = datetime.utcnow()
            if not (consent.legal_name or "").strip():
                cand = s.get(Candidate, cand_id) if Candidate else None
                consent.legal_name = (getattr(cand, "name", None) or "").strip()
            if hasattr(consent, "ip_address") and not (consent.ip_address or ""):
                consent.ip_address = request.remote_addr or ""
            _add_note(
                s, cand_id,
                "Consent form marked as signed by the candidate "
                "(Signable widget manual confirmation).",
            )
            s.commit()
        flash("Consent form marked as signed ✓ — moving you to the next stage.", "success")
        return redirect(_next_vetting_stage_url(cand_id))
    except Exception:
        current_app.logger.exception(
            "consent_mark_signed failed for candidate %s", cand_id
        )
        flash("Could not mark consent as signed. Please try again.", "danger")
    return redirect(url_for("associate.consent_form"))


@associate_bp.route("/declaration-form/mark-signed", methods=["POST"])
@_require_login
def declaration_mark_signed():
    """Associate-triggered fallback for the Declaration Form Signable widget.
    Upserts a DeclarationRecord so the portal / completion calc treat it as
    signed, without waiting on the Signable webhook."""
    DeclarationRecord = _portal_model("DeclarationRecord")
    Candidate = _model("Candidate")
    if DeclarationRecord is None:
        flash("Declaration record model not available — cannot mark as signed.", "danger")
        return redirect(url_for("associate.declaration_form"))
    cand_id = _get_associate_id()
    engine = _engine()
    try:
        with SASession(engine) as s:
            decl = s.query(DeclarationRecord).filter_by(candidate_id=cand_id).first()
            if decl is None:
                decl = DeclarationRecord(candidate_id=cand_id)
                s.add(decl)
            if hasattr(decl, "signed_date"):
                decl.signed_date = datetime.utcnow()
            if hasattr(decl, "legal_name") and not (decl.legal_name or "").strip():
                cand = s.get(Candidate, cand_id) if Candidate else None
                decl.legal_name = (getattr(cand, "name", None) or "").strip()
            _add_note(
                s, cand_id,
                "Declaration form marked as signed by the candidate "
                "(Signable widget manual confirmation).",
            )
            s.commit()
        flash("Declaration form marked as signed ✓ — moving you to the next stage.", "success")
        return redirect(_next_vetting_stage_url(cand_id))
    except Exception:
        current_app.logger.exception(
            "declaration_mark_signed failed for candidate %s", cand_id
        )
        flash("Could not mark declaration as signed. Please try again.", "danger")
    return redirect(url_for("associate.declaration_form"))


@associate_bp.route("/secondary-job-declaration/mark-signed", methods=["POST"])
@_require_login
def secondary_job_mark_signed():
    """Associate-triggered fallback for Secondary Job Declaration."""
    Candidate = _model("Candidate")
    if Candidate is None:
        flash("Candidate model not available — cannot mark as signed.", "danger")
        return redirect(url_for("associate.secondary_job_declaration"))
    cand_id = _get_associate_id()
    engine = _engine()
    try:
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            if cand is None:
                flash("Candidate record not found.", "danger")
                return redirect(url_for("associate.secondary_job_declaration"))
            if hasattr(cand, "secondary_job_declaration_signed"):
                cand.secondary_job_declaration_signed = True
            if hasattr(cand, "secondary_job_declaration_signed_at"):
                cand.secondary_job_declaration_signed_at = datetime.utcnow()
            _add_note(
                s, cand_id,
                "Secondary Job Declaration marked as signed by the candidate "
                "(Signable widget manual confirmation).",
            )
            s.commit()
        flash("Secondary Job Declaration marked as signed ✓ — moving you to the next stage.", "success")
        return redirect(_next_vetting_stage_url(cand_id))
    except Exception:
        current_app.logger.exception(
            "secondary_job_mark_signed failed for candidate %s", cand_id
        )
        flash("Could not mark the declaration as signed. Please try again.", "danger")
    return redirect(url_for("associate.secondary_job_declaration"))


@associate_bp.route("/declaration")
@_require_login
def declaration_redirect():
    """Redirect /portal/declaration to /portal/declaration-form."""
    return redirect(url_for("associate.declaration_form"))


# =============================================================================
# Req-005: Signable integration for the associate declaration form.
#
# COST GUARDRAIL — IMPORTANT.
# This integration is GATED by the SIGNABLE_LIVE_CALLS env var (default false).
# While the flag is false (or unset), _signable_send_declaration_envelope() never
# touches the network — it logs what it WOULD have sent and returns a stub envelope
# ID. Real Signable API calls (which incur per-envelope cost on the client's
# Signable account) only fire when an operator explicitly sets
# SIGNABLE_LIVE_CALLS=true in .env after the user has approved going live.
#
# Placeholders below marked "TODO: confirm with user" must be filled in before
# the live flag is flipped on.
# =============================================================================
SIGNABLE_LIVE_CALLS = os.getenv("SIGNABLE_LIVE_CALLS", "false").lower() == "true"


def _signable_send_declaration_envelope(candidate_name, candidate_email, declaration_id, legal_name):
    """
    Send the associate declaration as a Signable envelope.

    Returns dict with keys:
        envelope_id  — Signable envelope fingerprint, or "stub-<uuid>" in stub mode
        status       — "stub_sent" in stub mode, otherwise the Signable status string
        signing_url  — URL for the signer (may be None depending on Signable response/mode)

    Raises on live-call failure (caller catches and handles gracefully).
    """
    # Placeholders — TODO: confirm with user before going live
    template_id = os.getenv("SIGNABLE_DECLARATION_TEMPLATE_ID", "")  # TODO: confirm with user
    signer_role = os.getenv("SIGNABLE_DECLARATION_SIGNER_ROLE", "Associate")  # TODO: confirm with user
    subject = "Optimus Associate Declaration"
    message = (
        f"Hi {legal_name or candidate_name},\n\n"
        "Please countersign your Optimus associate declaration. "
        "Your responses have been recorded — this envelope is the formal e-signature step."
    )

    # ---------- STUB MODE (default) — never touches the network ----------
    if not SIGNABLE_LIVE_CALLS:
        stub_id = "stub-" + uuid4().hex
        try:
            current_app.logger.info(
                "[stub] Signable.envelope.create suppressed (SIGNABLE_LIVE_CALLS=false). "
                f"Would have sent: template_id={template_id!r}, signer_role={signer_role!r}, "
                f"signer_name={(legal_name or candidate_name)!r}, signer_email={candidate_email!r}, "
                f"subject={subject!r}, declaration_id={declaration_id!r}, stub_id={stub_id!r}"
            )
        except Exception:
            pass
        return {
            "envelope_id": stub_id,
            "status": "stub_sent",
            "signing_url": None,
        }

    # ---------- LIVE MODE — only reached when SIGNABLE_LIVE_CALLS=true ----------
    # Lazy import so the requests library is never even loaded in stub mode.
    import requests as _requests  # noqa: E402

    api_key = os.getenv("SIGNABLE_API_KEY", "")
    base_url = os.getenv("SIGNABLE_BASE_URL", "https://api.signable.co.uk/v1").rstrip("/")
    if not api_key:
        raise RuntimeError(
            "SIGNABLE_LIVE_CALLS is enabled but SIGNABLE_API_KEY is not set — refusing to send."
        )

    headers = {
        "Authorization": f"Basic {base64.b64encode((api_key + ':').encode()).decode()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "envelope_title": subject,
        "envelope_signing_message": message,
        "envelope_parties": [
            {
                "party_name": legal_name or candidate_name,
                "party_email": candidate_email,
                "party_role": signer_role,
            }
        ],
    }
    if template_id:
        payload["envelope_documents"] = [{"document_template_fingerprint": template_id}]

    resp = _requests.post(f"{base_url}/envelopes", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json() or {}
    return {
        "envelope_id": data.get("envelope_fingerprint") or data.get("id") or "",
        "status": (data.get("envelope_status") or "sent").lower(),
        "signing_url": data.get("signing_url"),
    }


def _signable_send_consent_envelope(candidate_name, candidate_email, consent_id, legal_name):
    """
    Send the associate consent form as a Signable envelope.

    Returns dict with keys:
        envelope_id  — Signable envelope fingerprint, or "stub-<uuid>" in stub mode
        status       — "stub_sent" in stub mode, otherwise the Signable status string
        signing_url  — URL for the signer (may be None depending on Signable response/mode)

    Raises on live-call failure (caller catches and handles gracefully).
    """
    # Placeholders — TODO: confirm with user before going live
    template_id = os.getenv("SIGNABLE_CONSENT_TEMPLATE_ID", "")  # TODO: confirm with user
    signer_role = os.getenv("SIGNABLE_CONSENT_SIGNER_ROLE", "Associate")  # TODO: confirm with user
    subject = "Optimus Consent Form — E-Signature Required"
    message = (
        f"Hi {legal_name or candidate_name},\n\n"
        "Please countersign your Optimus consent form. "
        "Your consent responses have been recorded — this envelope is the formal e-signature step."
    )

    # ---------- STUB MODE (default) — never touches the network ----------
    if not SIGNABLE_LIVE_CALLS:
        stub_id = "stub-" + uuid4().hex
        try:
            current_app.logger.info(
                "[stub] Signable.envelope.create suppressed (SIGNABLE_LIVE_CALLS=false). "
                f"Would have sent: template_id={template_id!r}, signer_role={signer_role!r}, "
                f"signer_name={(legal_name or candidate_name)!r}, signer_email={candidate_email!r}, "
                f"subject={subject!r}, consent_id={consent_id!r}, stub_id={stub_id!r}"
            )
        except Exception:
            pass
        return {
            "envelope_id": stub_id,
            "status": "stub_sent",
            "signing_url": None,
        }

    # ---------- LIVE MODE — only reached when SIGNABLE_LIVE_CALLS=true ----------
    # Lazy import so the requests library is never even loaded in stub mode.
    import requests as _requests  # noqa: E402

    api_key = os.getenv("SIGNABLE_API_KEY", "")
    base_url = os.getenv("SIGNABLE_BASE_URL", "https://api.signable.co.uk/v1").rstrip("/")
    if not api_key:
        raise RuntimeError(
            "SIGNABLE_LIVE_CALLS is enabled but SIGNABLE_API_KEY is not set — refusing to send."
        )

    headers = {
        "Authorization": f"Basic {base64.b64encode((api_key + ':').encode()).decode()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "envelope_title": subject,
        "envelope_signing_message": message,
        "envelope_parties": [
            {
                "party_name": legal_name or candidate_name,
                "party_email": candidate_email,
                "party_role": signer_role,
            }
        ],
    }
    if template_id:
        payload["envelope_documents"] = [{"document_template_fingerprint": template_id}]

    resp = _requests.post(f"{base_url}/envelopes", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json() or {}
    return {
        "envelope_id": data.get("envelope_fingerprint") or data.get("id") or "",
        "status": (data.get("envelope_status") or "sent").lower(),
        "signing_url": data.get("signing_url"),
    }


@associate_bp.route("/declaration-form", methods=["GET", "POST"])
@_require_login
def declaration_form():
    """Declaration form with 6 yes/no questions and e-signature."""
    DeclarationRecord = _portal_model("DeclarationRecord")
    engine = _engine()
    cand_id = _get_associate_id()

    if request.method == "GET":
        with SASession(engine) as s:
            decl = s.query(DeclarationRecord).filter_by(candidate_id=cand_id).order_by(
                DeclarationRecord.created_at.desc()
            ).first() if DeclarationRecord else None
            Candidate = _model("Candidate")
            _cand = s.get(Candidate, cand_id) if Candidate else None
            portal_name = (getattr(_cand, "name", None) or "").strip()
            return render_template(
                "associate/declaration_form.html",
                declaration=decl,
                already_signed=decl is not None,
                portal_name=portal_name,
            )

    with SASession(engine) as s:
        # Auto-set legal_name from the logged-in candidate
        Candidate = _model("Candidate")
        cand = s.get(Candidate, cand_id) if Candidate else None
        legal_name = ((cand.name if cand and getattr(cand, "name", None) else "") or "").strip()
        if not legal_name:
            legal_name = "Associate"

        work_restrictions = _parse_bool(request.form.get("work_restrictions", False))
        criminal_convictions = _parse_bool(request.form.get("criminal_convictions", False))
        ccj_debt = _parse_bool(request.form.get("ccj_debt", False))
        bankruptcy = _parse_bool(request.form.get("bankruptcy", False))
        dismissed = _parse_bool(request.form.get("dismissed", False))
        referencing_issues = _parse_bool(request.form.get("referencing_issues", False))

        decl = DeclarationRecord(
            candidate_id=cand_id,
            work_restrictions=work_restrictions,
            work_restrictions_detail=_sanitise(request.form.get("work_restrictions_details", "")),
            criminal_convictions=criminal_convictions,
            criminal_convictions_detail=_sanitise(request.form.get("criminal_convictions_details", "")),
            ccj_debt=ccj_debt,
            ccj_debt_detail=_sanitise(request.form.get("ccj_debt_details", "")),
            bankruptcy=bankruptcy,
            bankruptcy_detail=_sanitise(request.form.get("bankruptcy_details", "")),
            dismissed=dismissed,
            dismissed_detail=_sanitise(request.form.get("dismissed_details", "")),
            referencing_issues=referencing_issues,
            referencing_issues_detail=_sanitise(request.form.get("referencing_issues_details", "")),
            disclosure_text=_sanitise(request.form.get("open_disclosure", "")),
            legal_name=legal_name,
            signed_date=datetime.utcnow(),
            ip_address=request.remote_addr or "",
        )
        s.add(decl)

        # If any answer is "Yes", create a flag note for internal review
        flags = []
        if work_restrictions:
            flags.append("Work Restrictions")
        if criminal_convictions:
            flags.append("Criminal Convictions")
        if ccj_debt:
            flags.append("CCJ/Debt")
        if bankruptcy:
            flags.append("Bankruptcy")
        if dismissed:
            flags.append("Previous Dismissal")
        if referencing_issues:
            flags.append("Referencing Issues")

        if flags:
            flag_text = (
                f"DECLARATION FLAG: Candidate declared YES to: {', '.join(flags)}. "
                f"Signed by '{legal_name}' from IP {request.remote_addr}. "
                "Manual review required."
            )
            _add_note(s, cand_id, flag_text, note_type="system")

            # Contradiction Fix 3: If vetting checks exist, set them ALL to ON HOLD (orange)
            # If no vetting checks exist yet, fall back to the existing Flagged behaviour
            VettingCheck = _model("VettingCheck")
            existing_checks = []
            if VettingCheck:
                existing_checks = s.query(VettingCheck).filter_by(candidate_id=cand_id).all()

            if existing_checks:
                for vc in existing_checks:
                    vc.status = "ON HOLD"
                    if hasattr(vc, "colour"):
                        vc.colour = "orange"
                _add_note(s, cand_id,
                          f"All {len(existing_checks)} vetting checks set to ON HOLD due to declaration flags.",
                          note_type="system")
            else:
                # No vetting checks exist yet — keep existing flag behaviour
                Candidate = _model("Candidate")
                cand = s.get(Candidate, cand_id)
                if cand and hasattr(cand, "status"):
                    if cand.status not in ("On Hold", "Flagged"):
                        cand.status = "Flagged"
        else:
            _add_note(s, cand_id, f"Declaration form signed by '{legal_name}'. No flags raised.")

        s.commit()

    flash("Declaration form signed ✓ — moving you to the next stage.", "success")
    return redirect(_next_vetting_stage_url(cand_id))


@associate_bp.route("/intro-to-vetting")
@_require_login
def intro_to_vetting():
    """Redirect to merged vetting progress page."""
    return redirect(url_for("associate.vetting_progress"))


@associate_bp.route("/vetting-progress")
@_require_login
def vetting_progress():
    """Read-only view of all vetting check statuses."""
    VettingCheck = _model("VettingCheck")
    engine = _engine()
    cand_id = _get_associate_id()

    # P10: Check employment completeness — warn (don't hard block vetting view)
    employment_ok = True
    employment_msg = ""
    with SASession(engine) as s_check:
        employment_ok, employment_msg = _check_employment_complete(s_check, cand_id)

    ConsentRecord = _portal_model("ConsentRecord")
    DeclarationRecord = _portal_model("DeclarationRecord")

    with SASession(engine) as s:
        checks = []
        if VettingCheck:
            # Req 26 — Employment History and Address History retired as
            # vetting checks; hide any rows that pre-date the change.
            checks = s.query(VettingCheck).filter_by(candidate_id=cand_id).filter(
                VettingCheck.check_type.notin_(["Employment History", "Address History"])
            ).order_by(
                VettingCheck.check_type.asc()
            ).all()

        consent_signed = False
        if ConsentRecord:
            consent = s.query(ConsentRecord).filter_by(candidate_id=cand_id).first()
            consent_signed = bool(consent and consent.consent_given)

        declaration_signed = False
        if DeclarationRecord:
            decl = s.query(DeclarationRecord).filter_by(candidate_id=cand_id).first()
            declaration_signed = bool(decl and decl.signed_date)

        secondary_job_signed = False
        Candidate = _model("Candidate")
        if Candidate:
            cand = s.get(Candidate, cand_id)
            if cand:
                secondary_job_signed = bool(getattr(cand, "secondary_job_declaration_signed", False))

        EmploymentHistory = _portal_model("EmploymentHistory")
        employment_entered = False
        emp_ref_decl_signed = False
        if Candidate:
            _c = cand if cand else s.get(Candidate, cand_id)
            if _c:
                emp_ref_decl_signed = bool(getattr(_c, "employment_ref_declaration_signed", False))
        if EmploymentHistory:
            emp_count = s.query(EmploymentHistory).filter_by(candidate_id=cand_id).count()
            employment_entered = emp_count > 0 and emp_ref_decl_signed

        Document = _model("Document")
        docs_uploaded = False
        if Document:
            id_doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type="proof_of_identity").first()
            docs_uploaded = id_doc is not None

        return render_template(
            "associate/vetting_progress.html",
            vetting_checks=checks,
            consent_signed=consent_signed,
            declaration_signed=declaration_signed,
            secondary_job_signed=secondary_job_signed,
            employment_entered=employment_entered,
            docs_uploaded=docs_uploaded,
        )


@associate_bp.route("/references", methods=["GET"])
@_require_login
def references():
    """Redirect to employment history page."""
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/conduct-regulations", methods=["GET", "POST"])
@_require_login
def conduct_regulations():
    """Opt-in / opt-out of the Conduct of Employment Agencies and
    Employment Businesses Regulations 2003. Stored on Candidate so both
    the portal and staff profile can render the preference."""
    cand_id = _get_associate_id()
    engine = _engine()
    Candidate = _model("Candidate")
    if not Candidate or not cand_id:
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("associate.login"))

    if request.method == "POST":
        # Let callers route the redirect back to their own page (e.g.
        # the Company Details page embeds the same form inline).
        return_to = (request.form.get("return_to") or "").strip()
        def _redir_back():
            if return_to == "company_details":
                return redirect(url_for("associate.company_details"))
            return redirect(url_for("associate.conduct_regulations"))

        choice = (request.form.get("opt_choice") or "").strip().lower()
        legal_name = _sanitise(request.form.get("legal_name") or "").strip()
        if choice not in ("opt_in", "opt_out"):
            flash("Please select opt in or opt out before submitting.", "warning")
            return _redir_back()
        if not legal_name:
            flash("Please type your full name to confirm your choice.", "warning")
            return _redir_back()
        try:
            with SASession(engine) as s:
                cand = s.get(Candidate, cand_id)
                if cand is None:
                    flash("Candidate record not found.", "danger")
                    return redirect(url_for("associate.dashboard"))
                cand.conduct_regs_opted_in = (choice == "opt_in")
                cand.conduct_regs_decision_at = datetime.utcnow()
                cand.conduct_regs_signed_name = legal_name
                _add_note(
                    s, cand_id,
                    f"Conduct Regulations 2003: {'opted IN' if choice == 'opt_in' else 'opted OUT'} "
                    f"by '{legal_name}'.",
                )
                s.commit()
            flash("Your Conduct Regulations preference has been saved.", "success")
        except Exception:
            current_app.logger.exception(
                "conduct_regulations save failed for candidate %s", cand_id
            )
            flash("Could not save your preference. Please try again.", "danger")
        return _redir_back()

    # GET — read current state
    current_choice = None
    decision_at = None
    signed_name = ""
    portal_name = ""
    try:
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            if cand:
                opted = getattr(cand, "conduct_regs_opted_in", None)
                if opted is True:
                    current_choice = "opt_in"
                elif opted is False:
                    current_choice = "opt_out"
                decision_at = getattr(cand, "conduct_regs_decision_at", None)
                signed_name = (getattr(cand, "conduct_regs_signed_name", "") or "").strip()
                portal_name = (getattr(cand, "name", None) or "").strip()
    except Exception:
        pass

    return render_template(
        "associate/conduct_regulations.html",
        current_choice=current_choice,
        decision_at=decision_at,
        signed_name=signed_name,
        portal_name=portal_name,
    )


@associate_bp.route("/secondary-job-declaration", methods=["GET", "POST"])
@_require_login
def secondary_job_declaration():
    """Secondary Job Declaration — in-app form."""
    cand_id = _get_associate_id()
    engine = _engine()

    if request.method == "POST":
        try:
            Candidate = _model("Candidate")
            with SASession(engine) as s:
                cand = s.get(Candidate, cand_id) if Candidate else None
                if not cand:
                    flash("Session expired. Please log in again.", "warning")
                    return redirect(url_for("associate.login"))

                has_secondary = request.form.get("has_secondary") == "yes"
                job_title = request.form.get("job_title", "").strip()

                cand.secondary_job_declaration_signed = True
                cand.secondary_job_declaration_signed_at = datetime.utcnow()
                cand.secondary_job_has_secondary = has_secondary
                cand.secondary_job_title = job_title if has_secondary else ""
                cand.secondary_job_signed_name = cand.name or ""

                _add_note(s, cand_id, "Secondary Job Declaration confirmed via portal.")
                s.commit()

            flash("Secondary Job Declaration signed ✓ — moving you to the next stage.", "success")
            return redirect(_next_vetting_stage_url(cand_id))
        except Exception:
            current_app.logger.exception("Error saving secondary job declaration for candidate %s.", cand_id)
            flash("An error occurred. Please try again.", "danger")
            return redirect(url_for("associate.secondary_job_declaration"))

    # GET handler
    portal_name = ""
    already_signed = False
    signing_ctx = {}
    try:
        Candidate = _model("Candidate")
        if Candidate and cand_id:
            with SASession(engine) as s:
                _cand = s.get(Candidate, cand_id)
                portal_name = (getattr(_cand, "name", None) or "").strip()
                already_signed = bool(getattr(_cand, "secondary_job_declaration_signed", False))
                if already_signed:
                    signing_ctx = {
                        "signed_at": getattr(_cand, "secondary_job_declaration_signed_at", None),
                        "has_secondary": bool(getattr(_cand, "secondary_job_has_secondary", False)),
                        "title": (getattr(_cand, "secondary_job_title", "") or "").strip(),
                        "signed_name": (getattr(_cand, "secondary_job_signed_name", "") or "").strip(),
                    }
    except Exception:
        portal_name = ""
    return render_template(
        "associate/secondary_job_declaration.html",
        portal_name=portal_name,
        already_signed=already_signed,
        signing=signing_ctx,
    )


@associate_bp.route("/employment-reference-declaration", methods=["GET"])
@_require_login
def employment_reference_declaration():
    """Employment Reference Declaration — embedded Signable widget."""
    cand_id = _get_associate_id()
    base_url = os.getenv("SIGNABLE_EMPLOYMENT_REF_WIDGET_URL", "")
    widget_url = _widget_url_with_meta(base_url, cand_id) if base_url else ""

    # Check if already signed
    already_signed = False
    Candidate = _model("Candidate")
    if Candidate and cand_id:
        engine = _engine()
        with SASession(engine) as s:
            cand = s.get(Candidate, cand_id)
            if cand:
                already_signed = bool(getattr(cand, "employment_ref_declaration_signed", False))

    return render_template(
        "associate/employment_reference_declaration.html",
        widget_url=widget_url,
        already_signed=already_signed,
    )


@associate_bp.route("/references/employment", methods=["GET"])
@_require_login
def references_employment():
    """Employment history sub-page: timeline + add employment + add gap forms."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    entries = []
    gaps = []
    gap_evidence = {}  # entry_id -> {id, original_name, filename}
    with SASession(engine) as s:
        if EmploymentHistory:
            entries = s.query(EmploymentHistory).filter_by(candidate_id=cand_id).order_by(
                EmploymentHistory.start_date.desc().nullslast()
            ).all()
            gaps = _detect_gaps(entries)

            if Document:
                doc_ids = [
                    e.gap_evidence_doc_id for e in entries
                    if getattr(e, "is_gap", False) and getattr(e, "gap_evidence_doc_id", None)
                ]
                if doc_ids:
                    docs = s.query(Document).filter(Document.id.in_(doc_ids)).all()
                    docs_by_id = {d.id: d for d in docs}
                    for e in entries:
                        if getattr(e, "is_gap", False) and getattr(e, "gap_evidence_doc_id", None):
                            d = docs_by_id.get(e.gap_evidence_doc_id)
                            if d:
                                gap_evidence[e.id] = {
                                    "id": d.id,
                                    "original_name": getattr(d, "original_name", "") or getattr(d, "filename", "") or "evidence",
                                    "filename": getattr(d, "filename", ""),
                                }

    return render_template(
        "associate/references_employment.html",
        employment_entries=entries,
        gaps_detected=gaps,
        gap_evidence=gap_evidence,
    )


@associate_bp.route("/references/gap-evidence/replace/<int:entry_id>", methods=["POST"])
@_require_login
def references_gap_evidence_replace(entry_id: int):
    """Replace (or add) the evidence document on a gap entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        flash("Employment history not available.", "danger")
        return redirect(url_for("associate.references_employment"))

    new_file = request.files.get("evidence")
    if not new_file or not new_file.filename:
        flash("Please choose a file to upload.", "warning")
        return redirect(url_for("associate.references_employment"))

    gap_allowed_ext = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg"}
    ext = os.path.splitext(new_file.filename)[1].lower()
    if ext not in gap_allowed_ext:
        flash("Gap evidence must be one of: PDF, Word, Excel, PNG, JPG.", "danger")
        return redirect(url_for("associate.references_employment"))

    saved = _save_file(new_file)
    if not saved:
        flash("Could not save the uploaded file. Please try again.", "danger")
        return redirect(url_for("associate.references_employment"))

    with SASession(engine) as s:
        entry = s.query(EmploymentHistory).filter_by(id=entry_id, candidate_id=cand_id).first()
        if not entry:
            flash("Gap entry not found.", "danger")
            return redirect(url_for("associate.references_employment"))
        if not getattr(entry, "is_gap", False):
            flash("Evidence can only be attached to gap entries.", "warning")
            return redirect(url_for("associate.references_employment"))

        # Delete the previous file + Document row when replacing.
        if entry.gap_evidence_doc_id and Document:
            old = s.query(Document).filter_by(id=entry.gap_evidence_doc_id, candidate_id=cand_id).first()
            if old:
                old_path = _resolve_document_path(getattr(old, "filename", "") or "")
                if old_path and os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        current_app.logger.exception(
                            "gap evidence replace: failed to delete prior file %s", old_path,
                        )
                s.delete(old)

        new_doc_id = None
        if Document:
            new_doc = Document(
                candidate_id=cand_id,
                doc_type="gap_evidence",
                filename=saved["filename"],
                original_name=saved["original_name"],
                uploaded_at=datetime.utcnow(),
            )
            s.add(new_doc)
            s.flush()
            new_doc_id = new_doc.id

        entry.gap_evidence_doc_id = new_doc_id
        _add_note(s, cand_id, f"Gap evidence uploaded: {saved['original_name']}.")
        s.commit()

    flash("Gap evidence updated.", "success")
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/references/gap-evidence/remove/<int:entry_id>", methods=["POST"])
@_require_login
def references_gap_evidence_remove(entry_id: int):
    """Fully delete the evidence document on a gap entry (file + DB row)."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        flash("Employment history not available.", "danger")
        return redirect(url_for("associate.references_employment"))

    with SASession(engine) as s:
        entry = s.query(EmploymentHistory).filter_by(id=entry_id, candidate_id=cand_id).first()
        if not entry:
            flash("Gap entry not found.", "danger")
            return redirect(url_for("associate.references_employment"))
        if not getattr(entry, "is_gap", False):
            flash("Evidence can only be attached to gap entries.", "warning")
            return redirect(url_for("associate.references_employment"))

        doc_id = entry.gap_evidence_doc_id
        deleted_name = ""
        if doc_id and Document:
            doc = s.query(Document).filter_by(id=doc_id, candidate_id=cand_id).first()
            if doc:
                deleted_name = getattr(doc, "original_name", "") or getattr(doc, "filename", "")
                filepath = _resolve_document_path(getattr(doc, "filename", "") or "")
                if filepath and os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                    except OSError:
                        current_app.logger.exception(
                            "gap evidence remove: failed to delete file %s", filepath,
                        )
                s.delete(doc)

        entry.gap_evidence_doc_id = None
        _add_note(
            s, cand_id,
            f"Gap evidence deleted{(': ' + deleted_name) if deleted_name else ''}.",
        )
        s.commit()

    flash("Gap evidence deleted.", "success")
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/references/vetting-checks", methods=["GET"])
@_require_login
def references_vetting_checks():
    """Vetting checks sub-page: qualifications, professional registrations, and other vetting items."""
    QualificationRecord = _portal_model("QualificationRecord")
    ProfessionalRegistration = _portal_model("ProfessionalRegistration")
    engine = _engine()
    cand_id = _get_associate_id()

    qualifications = []
    professional_registrations = []
    with SASession(engine) as s:
        if QualificationRecord:
            qualifications = s.query(QualificationRecord).filter_by(candidate_id=cand_id).order_by(
                QualificationRecord.start_date.desc().nullslast()
            ).all()
        if ProfessionalRegistration:
            professional_registrations = s.query(ProfessionalRegistration).filter_by(candidate_id=cand_id).order_by(
                ProfessionalRegistration.created_at.desc()
            ).all()

    return render_template(
        "associate/references_vetting_checks.html",
        qualifications=qualifications,
        professional_registrations=professional_registrations,
    )


def _detect_overlaps(session, candidate_id, new_start, new_end, new_is_gap):
    """
    Req-009: detect overlaps between a new employment/gap entry and existing ones.

    Returns (blocking, soft):
      - blocking: gap+gap, gap+employment, employment+gap → must reject (user must update dates)
      - soft: employment+employment → require explicit user confirmation (held two roles concurrently)
    """
    EmploymentHistory = _portal_model("EmploymentHistory")
    if not EmploymentHistory or not new_start:
        return [], []
    effective_end = new_end or date.today()
    blocking = []
    soft = []
    rows = session.query(EmploymentHistory).filter_by(candidate_id=candidate_id).all()

    def _fmt(d):
        return d.strftime("%b %Y") if d else "present"

    for row in rows:
        if not row.start_date:
            continue
        row_end = row.end_date or date.today()
        # No overlap if new range is entirely before or after the row range
        if new_start > row_end or effective_end < row.start_date:
            continue
        rng = f"{_fmt(row.start_date)} to {_fmt(row.end_date)}"
        row_is_gap = bool(getattr(row, "is_gap", False))
        if new_is_gap and row_is_gap:
            blocking.append({
                "message": f"This gap overlaps with another gap ({rng}). Two gaps cannot overlap — please update your dates."
            })
        elif new_is_gap and not row_is_gap:
            blocking.append({
                "message": f"This gap overlaps with your employment at {row.company_name or 'an employer'} ({rng}). You cannot be both employed and in a gap at the same time — please update your dates."
            })
        elif not new_is_gap and row_is_gap:
            blocking.append({
                "message": f"This employment overlaps with a recorded gap ({rng}). You cannot be both employed and in a gap at the same time — please update your dates."
            })
        else:
            soft.append({
                "message": f"This overlaps with your employment at {row.company_name or 'another employer'} ({rng})."
            })
    return blocking, soft


@associate_bp.route("/references/add-employment", methods=["POST"])
@_require_login
def references_add_employment():
    """Form handler: add an employment history entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        flash("Employment history not available.", "danger")
        return redirect(url_for("associate.references_employment"))

    company_name = _sanitise(request.form.get("company_name", "")).strip()
    if not company_name:
        flash("Company name is required.", "danger")
        return redirect(url_for("associate.references_employment"))

    new_start = _parse_date(request.form.get("start_date", ""))
    new_end = _parse_date(request.form.get("end_date", ""))
    confirm_overlap = request.form.get("confirm_overlap") == "yes"

    with SASession(engine) as s:
        # Req-009: server-side overlap validation
        blocking, soft = _detect_overlaps(s, cand_id, new_start, new_end, new_is_gap=False)
        if blocking:
            for b in blocking:
                flash(b["message"], "danger")
            return redirect(url_for("associate.references_employment"))
        if soft and not confirm_overlap:
            for so in soft:
                flash(
                    so["message"] + " If you held two roles at the same time, please confirm the overlap on the form and re-submit.",
                    "warning",
                )
            return redirect(url_for("associate.references_employment"))

        can_contact = "1" in request.form.getlist("can_contact")
        no_contact_reason = _sanitise(request.form.get("no_contact_reason", "")).strip()
        earliest_contact = _parse_date(request.form.get("earliest_contact_date", ""))

        # If can't contact, reason is required
        if not can_contact and not no_contact_reason:
            flash("Please provide a reason why we cannot contact this employer.", "danger")
            return redirect(url_for("associate.references_employment"))

        # Req 22 — company_email / agency_email are the new canonical fields.
        # Legacy referee_email is kept and mirrored from company_email so any
        # older read paths (and the staff-side default email selection) still
        # work without a one-off data migration.
        company_email = _sanitise(request.form.get("company_email", "")).strip()
        agency_email = _sanitise(request.form.get("agency_email", "")).strip()
        legacy_referee = _sanitise(request.form.get("referee_email", "")).strip()
        entry = EmploymentHistory(
            candidate_id=cand_id,
            company_name=company_name,
            agency_name=_sanitise(request.form.get("agency_name", "")),
            referee_email=legacy_referee or company_email,
            company_email=company_email,
            agency_email=agency_email,
            company_address=_sanitise(request.form.get("company_address", "")),
            start_date=new_start,
            end_date=new_end,
            job_title=_sanitise(request.form.get("job_title", "")),
            reason_for_leaving=_sanitise(request.form.get("reason_for_leaving", "")),
            is_gap=False,
            permission_to_request=can_contact,
            permission_delay_reason=no_contact_reason if not can_contact else "",
            permission_future_date=earliest_contact if not can_contact else None,
            reference_status="not_sent",
        )
        s.add(entry)
        _add_note(s, cand_id, f"Employment history added: {company_name}.")
        s.commit()

    flash("Employment entry added successfully.", "success")
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/references/add-gap", methods=["POST"])
@_require_login
def references_add_gap():
    """Form handler: add a gap entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        flash("Employment history not available.", "danger")
        return redirect(url_for("associate.references_employment"))

    reason = _sanitise(request.form.get("reason", "")).strip()
    other_reason = _sanitise(request.form.get("other_reason", "")).strip()
    gap_reason = other_reason if reason == "Other" and other_reason else reason

    new_start = _parse_date(request.form.get("start_date", ""))
    new_end = _parse_date(request.form.get("end_date", ""))

    # Req-009: server-side overlap validation. For gaps, all overlaps are blocking.
    with SASession(engine) as s_check:
        blocking, _soft = _detect_overlaps(s_check, cand_id, new_start, new_end, new_is_gap=True)
        if blocking:
            for b in blocking:
                flash(b["message"], "danger")
            return redirect(url_for("associate.references_employment"))

    evidence_doc_id = None
    evidence_file = request.files.get("evidence")
    if evidence_file and evidence_file.filename:
        gap_allowed_ext = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg"}
        ext = os.path.splitext(evidence_file.filename)[1].lower()
        if ext not in gap_allowed_ext:
            flash(
                "Gap evidence must be one of: PDF, Word, Excel, PNG, JPG.",
                "danger",
            )
            return redirect(url_for("associate.references_employment"))
        saved = _save_file(evidence_file)
        if saved:
            Document = _model("Document")
            if Document:
                with SASession(engine) as s_doc:
                    doc = Document(
                        candidate_id=cand_id,
                        doc_type="gap_evidence",
                        filename=saved["filename"],
                        original_name=saved["original_name"],
                        uploaded_at=datetime.utcnow(),
                    )
                    s_doc.add(doc)
                    s_doc.commit()
                    evidence_doc_id = doc.id

    with SASession(engine) as s:
        entry = EmploymentHistory(
            candidate_id=cand_id,
            company_name="",
            start_date=new_start,
            end_date=new_end,
            is_gap=True,
            gap_reason=gap_reason,
            gap_evidence_doc_id=evidence_doc_id,
            reference_status="not_sent",
        )
        s.add(entry)
        _add_note(s, cand_id, f"Gap entry added: {gap_reason}.")
        s.commit()

    flash("Gap entry added successfully.", "success")
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/references/extract-from-cv", methods=["POST"])
@_require_login
def references_extract_from_cv():
    """Use AI to extract employment history from candidate's CV."""
    from app import extract_cv_text, ai_extract_employment_history, Document, engine

    cand_id = _get_associate_id()
    if not cand_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    with SASession(engine) as s:
        # Find latest CV
        doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type="cv").order_by(Document.id.desc()).first()
        if not doc:
            return jsonify({"ok": False, "error": "No CV uploaded. Please upload your CV first on the My Profile page."}), 400

        # Try to extract text — log details on failure
        try:
            from app import _doc_file_path
            file_path = _doc_file_path(doc)
            file_exists = os.path.exists(file_path)
            current_app.logger.info(
                f"CV extraction: doc.id={doc.id}, filename={doc.filename}, "
                f"original_name={doc.original_name}, resolved_path={file_path}, "
                f"exists={file_exists}"
            )
        except Exception as path_err:
            current_app.logger.warning(f"CV path resolution failed: {path_err}")
            file_path = "unknown"
            file_exists = False

        if not file_exists:
            return jsonify({"ok": False, "error": f"CV file not found on server. Please re-upload your CV. (path: {doc.filename})"}), 400

        cv_text = extract_cv_text(doc)
        if not cv_text or len(cv_text.strip()) < 50:
            return jsonify({"ok": False, "error": "Could not extract text from your CV. The file may be scanned/image-based. Please upload a text-based PDF or DOCX file."}), 400

    entries = ai_extract_employment_history(cv_text)
    if not entries:
        return jsonify({"ok": False, "error": f"AI could not extract employment history from your CV ({len(cv_text)} chars extracted). This may happen with scanned PDFs or CVs with unusual formatting. Please enter your history manually."}), 400

    return jsonify({"ok": True, "entries": entries})


@associate_bp.route("/references/save-extracted", methods=["POST"])
@_require_login
def references_save_extracted():
    """Save AI-extracted employment history entries."""
    cand_id = _get_associate_id()
    if not cand_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json()
    if not data or not data.get("entries"):
        return jsonify({"ok": False, "error": "No entries to save"}), 400

    _ensure_models()
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()

    saved = 0
    with SASession(engine) as s:
        for entry in data["entries"]:
            start = _parse_date(entry.get("start_date", ""))
            end = _parse_date(entry.get("end_date", "")) if entry.get("end_date") != "Present" else None

            emp = EmploymentHistory(
                candidate_id=cand_id,
                company_name=_sanitise(entry.get("company_name", "")),
                job_title=_sanitise(entry.get("job_title", "")),
                start_date=start,
                end_date=end,
                is_gap=bool(entry.get("is_gap", False)),
                gap_reason=_sanitise(entry.get("gap_reason", "")),
                reference_status="not_sent",
            )
            s.add(emp)
            saved += 1
        s.commit()

    return jsonify({"ok": True, "saved": saved})


@associate_bp.route("/references/add-qualification", methods=["POST"])
@_require_login
def references_add_qualification():
    """Form handler: add a qualification record."""
    QualificationRecord = _portal_model("QualificationRecord")
    engine = _engine()
    cand_id = _get_associate_id()

    if not QualificationRecord:
        flash("Qualifications not available.", "danger")
        return redirect(url_for("associate.references_vetting_checks"))

    qual_name = _sanitise(request.form.get("name", "")).strip()
    if not qual_name:
        flash("Qualification name is required.", "danger")
        return redirect(url_for("associate.references_vetting_checks"))

    perm = request.form.get("qual_permission", "yes") == "yes"

    with SASession(engine) as s:
        qual = QualificationRecord(
            candidate_id=cand_id,
            qualification_name=qual_name,
            qualification_type=_sanitise(request.form.get("qual_type", "")),
            grade=_sanitise(request.form.get("grade", "")),
            institution=_sanitise(request.form.get("institution", "")),
            level=_sanitise(request.form.get("qual_level", "")),
            institution_street=_sanitise(request.form.get("institution_street", "")),
            institution_town=_sanitise(request.form.get("institution_town", "")),
            institution_country=_sanitise(request.form.get("institution_country", "GB")),
            start_date=_parse_date(request.form.get("start_date", "")),
            end_date=_parse_date(request.form.get("end_date", "")),
            permission_to_request=perm,
            permission_delay_reason=_sanitise(request.form.get("qual_delay_reason", "")),
        )
        s.add(qual)
        _add_note(s, cand_id, f"Qualification added: {qual_name}.")
        s.commit()

    flash("Qualification added successfully.", "success")
    return redirect(url_for("associate.references_vetting_checks"))


@associate_bp.route("/references/delete-entry/<int:entry_id>", methods=["DELETE"])
@_require_login
def references_delete_entry(entry_id):
    """AJAX: delete an employment/gap entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        return jsonify({"success": False, "error": "Not available"}), 500

    with SASession(engine) as s:
        entry = s.query(EmploymentHistory).filter_by(id=entry_id, candidate_id=cand_id).first()
        if not entry:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        desc = entry.company_name or "Gap"
        s.delete(entry)
        _add_note(s, cand_id, f"Employment entry deleted: {desc}.")
        s.commit()

    return jsonify({"success": True})


@associate_bp.route("/references/delete-qualification/<int:qual_id>", methods=["DELETE"])
@_require_login
def references_delete_qualification(qual_id):
    """AJAX: delete a qualification record."""
    QualificationRecord = _portal_model("QualificationRecord")
    engine = _engine()
    cand_id = _get_associate_id()

    if not QualificationRecord:
        return jsonify({"success": False, "error": "Not available"}), 500

    with SASession(engine) as s:
        qual = s.query(QualificationRecord).filter_by(id=qual_id, candidate_id=cand_id).first()
        if not qual:
            return jsonify({"success": False, "error": "Qualification not found"}), 404
        name = qual.qualification_name
        s.delete(qual)
        _add_note(s, cand_id, f"Qualification deleted: {name}.")
        s.commit()

    return jsonify({"success": True})


@associate_bp.route("/references/add-professional-registration", methods=["POST"])
@_require_login
def references_add_professional_registration():
    """Form handler: add a professional registration record."""
    ProfessionalRegistration = _portal_model("ProfessionalRegistration")
    engine = _engine()
    cand_id = _get_associate_id()

    if not ProfessionalRegistration:
        flash("Professional registrations not available.", "danger")
        return redirect(url_for("associate.references_vetting_checks"))

    assoc_name = _sanitise(request.form.get("association_name", "")).strip()
    if not assoc_name:
        flash("Association name is required.", "danger")
        return redirect(url_for("associate.references_vetting_checks"))

    with SASession(engine) as s:
        reg = ProfessionalRegistration(
            candidate_id=cand_id,
            association_name=assoc_name,
            membership_number=_sanitise(request.form.get("membership_number", "")),
            membership_level=_sanitise(request.form.get("membership_level", "")),
            membership_status=_sanitise(request.form.get("membership_status", "Current")),
        )
        s.add(reg)
        _add_note(s, cand_id, f"Professional registration added: {assoc_name}.")
        s.commit()

    flash("Professional registration added successfully.", "success")
    return redirect(url_for("associate.references_vetting_checks"))


@associate_bp.route("/references/delete-professional-registration/<int:reg_id>", methods=["DELETE"])
@_require_login
def references_delete_professional_registration(reg_id):
    """AJAX: delete a professional registration record."""
    ProfessionalRegistration = _portal_model("ProfessionalRegistration")
    engine = _engine()
    cand_id = _get_associate_id()

    if not ProfessionalRegistration:
        return jsonify({"success": False, "error": "Not available"}), 500

    with SASession(engine) as s:
        reg = s.query(ProfessionalRegistration).filter_by(id=reg_id, candidate_id=cand_id).first()
        if not reg:
            return jsonify({"success": False, "error": "Registration not found"}), 404
        name = reg.association_name
        s.delete(reg)
        _add_note(s, cand_id, f"Professional registration deleted: {name}.")
        s.commit()

    return jsonify({"success": True})


@associate_bp.route("/documents", methods=["GET", "POST"])
@_require_login
def documents():
    """Upload and view documents (ID, proof of address, RTW, CV, qualifications)."""
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    if request.method == "GET":
        with SASession(engine) as s:
            docs_list = []
            if Document:
                docs_list = s.query(Document).filter_by(candidate_id=cand_id).order_by(
                    Document.uploaded_at.desc()
                ).all()
            # Organize documents by category (doc_type) for the template
            docs_by_category = {}
            for doc in docs_list:
                cat = getattr(doc, "doc_type", "other") or "other"
                docs_by_category.setdefault(cat, []).append(doc)
            return render_template("associate/documents.html", documents=docs_by_category)

    # POST: handle file upload
    doc_type = _sanitise(request.form.get("doc_type", "other"))
    file = request.files.get("file")

    # P11: POA type-specific expiry validation
    expiry_date_str = request.form.get("expiry_date", "").strip()
    poa_sub_type = _sanitise(request.form.get("poa_sub_type", "")).strip()
    if doc_type in ("proof_of_address", "poa") and poa_sub_type:
        # Validate POA expiry based on document sub-type
        max_days = {"bank_statement": 90, "credit_card_statement": 90,
                    "utility_bill": 90, "council_tax": 365, "mortgage_statement": 365}
        limit = max_days.get(poa_sub_type, 90)
        if not expiry_date_str:
            flash(f"Please enter the document date for your proof of address. "
                  f"{'Bank/utility/credit statements must be within 90 days.' if limit == 90 else 'Council tax/mortgage statements must be within 12 months.'}",
                  "danger")
            return redirect(url_for("associate.documents"))

    if not file or not file.filename:
        flash("Please select a file to upload.", "danger")
        return redirect(url_for("associate.documents"))

    # Validate extension
    allowed = {"pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"}
    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    if ext not in allowed:
        flash(f"File type .{ext} not allowed. Accepted: {', '.join(sorted(allowed))}.", "danger")
        return redirect(url_for("associate.documents"))

    saved = _save_file(file)
    if not saved:
        flash("Failed to save file.", "danger")
        return redirect(url_for("associate.documents"))

    with SASession(engine) as s:
        if Document:
            doc = Document(
                candidate_id=cand_id,
                doc_type=doc_type,
                filename=saved["filename"],
                original_name=saved["original_name"],
                uploaded_at=datetime.utcnow(),
            )
            s.add(doc)
            _add_note(s, cand_id, f"Document uploaded: {saved['original_name']} (type: {doc_type}).")
            s.commit()

    flash("Document uploaded successfully.", "success")
    return redirect(url_for("associate.documents"))


@associate_bp.route("/my-applications")
@_require_login
def my_applications():
    """View all submitted applications, grouped by status (Pending Offer / Applied / Accepted / Declined / Unsuccessful)."""
    Application = _model("Application")
    Job = _model("Job")
    Engagement = _model("Engagement")
    engine = _engine()
    cand_id = _get_associate_id()

    UNSUCCESSFUL_STATUSES = {
        "rejected", "withdrawn", "rejected/withdrawn", "offer declined",
        "declined", "unsuccessful", "closed", "cancelled", "canceled",
    }

    ESigRequest = _model("ESigRequest")

    applications = []
    with SASession(engine) as s:
        if Application:
            from sqlalchemy.orm import joinedload
            query = s.query(Application).filter_by(candidate_id=cand_id)
            if hasattr(Application, "job"):
                query = query.options(joinedload(Application.job))
            rows = query.order_by(Application.created_at.desc()).all()

            for app in rows:
                job = app.job if hasattr(app, "job") and app.job else None
                raw_status = (app.status or "").strip().lower()

                # Determine client name from engagement
                client_name = ""
                if job and hasattr(job, "engagement_id") and job.engagement_id and Engagement:
                    eng = s.get(Engagement, job.engagement_id)
                    if eng:
                        client_name = getattr(eng, "client", "") or getattr(eng, "name", "") or ""

                # Req-046: classify by offer response state
                # Order matters — check offer states FIRST so "Offered" doesn't fall through to UNSUCCESSFUL_STATUSES check
                if raw_status == "offered" and app.offer_response is None and app.offer_made_at:
                    display_status = "Offer Received"
                elif app.offer_response == "accepted":
                    display_status = "Offer Accepted"
                elif app.offer_response == "declined":
                    display_status = "Offer Declined"
                elif raw_status in UNSUCCESSFUL_STATUSES:
                    display_status = "Unsuccessful"
                elif raw_status in ("contract issued", "contract sent"):
                    display_status = "Contract Issued"
                elif raw_status in ("contract signed", "placed"):
                    display_status = "Placed"
                else:
                    display_status = "Applied"

                # Req 44 — contract status MUST be specific to this
                # Application. The previous fallback to a candidate-wide
                # ESigRequest lookup was the root cause of "Contract Sent"
                # appearing against every role when only one was sent.
                # If no ESigRequest is tied to this application id we
                # surface contract_status = None so the template renders
                # an empty contract column for that row.
                esig = None
                if ESigRequest:
                    esig = s.scalar(
                        select(ESigRequest)
                        .where(ESigRequest.application_id == app.id)
                        .order_by(ESigRequest.id.desc())
                    )

                applications.append({
                    "id": app.id,
                    "job_title": job.title if job else "Untitled Role",
                    "client": client_name,
                    "applied_date": app.created_at.strftime("%d %b %Y") if app.created_at else "N/A",
                    "status": display_status,
                    # Offer detail (only populated for offer-state rows; templates check display_status)
                    "offer_role_title": app.offer_role_title or "",
                    "offer_start_date": app.offer_start_date.strftime("%d %b %Y") if app.offer_start_date else "",
                    "offer_day_rate": float(app.offer_day_rate) if app.offer_day_rate is not None else None,
                    "offer_rate_type": app.offer_rate_type or "day",
                    "offer_location": app.offer_location or "",
                    "offer_notes": app.offer_notes or "",
                    "offer_responded_at": app.offer_responded_at.strftime("%d %b %Y") if app.offer_responded_at else "",
                    # Contract / e-signature info
                    "contract_status": esig.status if esig else None,
                    "signing_url": esig.signing_url if esig else None,
                })

    return render_template("associate/my_applications.html",
                           applications=applications,
                           active_page="my_applications")


# =============================================================================
# Req-046: Candidate-side offer accept / decline routes (the feedback loop)
# =============================================================================
@associate_bp.route("/offer/<int:application_id>/accept", methods=["POST"])
@_require_login
def offer_accept(application_id):
    """Candidate accepts an offer via the portal — updates the application directly.

    No Signable involvement at this stage. The accept action immediately
    transitions Application.status to 'Accepted' and runs the same
    Candidate.status update that the kanban drag-drop would (Candidate.status =
    'In Vetting'). The staff side sees the change on next page load.
    """
    Application = _model("Application")
    Candidate = _model("Candidate")
    CandidateNote = _model("CandidateNote")
    engine = _engine()
    cand_id = _get_associate_id()

    if not Application or not Candidate:
        flash("Sorry, the portal can't process your response right now. Please try again later.", "danger")
        return redirect(url_for("associate.my_applications"))

    with SASession(engine) as s:
        appn = s.get(Application, application_id)
        if not appn:
            flash("Offer not found.", "danger")
            return redirect(url_for("associate.my_applications"))

        # Authorisation: candidate can only act on their own application
        if appn.candidate_id != cand_id:
            abort(403)

        # State check: only allow accept if the offer is genuinely pending
        if (appn.status or "").strip() != "Offered" or appn.offer_response is not None:
            flash("This offer is no longer pending — it may have already been responded to or withdrawn.", "warning")
            return redirect(url_for("associate.my_applications"))

        cand = s.get(Candidate, cand_id)
        ip = (request.remote_addr or "")[:50]
        now = datetime.utcnow()

        # Record the response
        appn.offer_response = "accepted"
        appn.offer_responded_at = now
        appn.offer_responded_ip = ip

        # Transition the application + run the same downstream automation
        # the kanban does (cand.status = 'In Vetting' for the Accepted stage)
        old_status = appn.status
        appn.status = "Accepted"

        # Activity log entry on the candidate's feed (matches existing pattern)
        if CandidateNote:
            note_content = (
                f"Offer accepted by candidate via portal from IP {ip}\n"
                f"Workflow stage changed: {old_status} \u2192 Accepted"
            )
            s.add(CandidateNote(
                candidate_id=cand_id,
                user_email=cand.email if cand else "candidate",
                note_type="activity",
                content=note_content,
                created_at=now,
            ))

        s.commit()

        # Audit log (best-effort; failure should not block the candidate response)
        try:
            from app import log_audit_event
            log_audit_event(
                'update', 'workflow',
                f'Offer accepted by candidate (application {application_id})',
                'application', application_id,
                {
                    'candidate_id': cand_id,
                    'candidate_name': cand.name if cand else None,
                    'old_workflow_status': old_status,
                    'new_workflow_status': 'Accepted',
                    'actor': cand.email if cand else 'candidate',
                    'source': 'portal_offer_accept',
                    'ip': ip,
                }
            )
        except Exception:
            pass

        # Req 21 — auto-fire Intro to Vetting email so the associate
        # can immediately complete portal forms and consent. Idempotent
        # via cand.intro_to_vetting_sent_at; admin Resend can override.
        try:
            from app import _send_intro_to_vetting_email
            if cand and not getattr(cand, "intro_to_vetting_sent_at", None):
                _send_intro_to_vetting_email(s, cand)
                s.commit()
        except Exception:
            current_app.logger.exception("intro-to-vetting auto-send failed for cand %s", cand_id)

    flash("Thanks — your offer has been accepted. Our vetting team will be in touch shortly.", "success")
    return redirect(url_for("associate.my_applications"))


@associate_bp.route("/offer/<int:application_id>/decline", methods=["POST"])
@_require_login
def offer_decline(application_id):
    """Candidate declines an offer via the portal."""
    Application = _model("Application")
    Candidate = _model("Candidate")
    CandidateNote = _model("CandidateNote")
    engine = _engine()
    cand_id = _get_associate_id()

    if not Application or not Candidate:
        flash("Sorry, the portal can't process your response right now. Please try again later.", "danger")
        return redirect(url_for("associate.my_applications"))

    decline_reason = _sanitise(request.form.get("decline_reason", "")).strip() or None

    with SASession(engine) as s:
        appn = s.get(Application, application_id)
        if not appn:
            flash("Offer not found.", "danger")
            return redirect(url_for("associate.my_applications"))

        if appn.candidate_id != cand_id:
            abort(403)

        if (appn.status or "").strip() != "Offered" or appn.offer_response is not None:
            flash("This offer is no longer pending — it may have already been responded to or withdrawn.", "warning")
            return redirect(url_for("associate.my_applications"))

        cand = s.get(Candidate, cand_id)
        ip = (request.remote_addr or "")[:50]
        now = datetime.utcnow()

        appn.offer_response = "declined"
        appn.offer_responded_at = now
        appn.offer_responded_ip = ip
        appn.offer_decline_reason = decline_reason

        old_status = appn.status
        appn.status = "Withdrawn"

        if CandidateNote:
            reason_text = f": {decline_reason}" if decline_reason else ""
            note_content = (
                f"Offer declined by candidate via portal from IP {ip}{reason_text}\n"
                f"Workflow stage changed: {old_status} \u2192 Withdrawn"
            )
            s.add(CandidateNote(
                candidate_id=cand_id,
                user_email=cand.email if cand else "candidate",
                note_type="activity",
                content=note_content,
                created_at=now,
            ))

        s.commit()

        try:
            from app import log_audit_event
            log_audit_event(
                'update', 'workflow',
                f'Offer declined by candidate (application {application_id})',
                'application', application_id,
                {
                    'candidate_id': cand_id,
                    'candidate_name': cand.name if cand else None,
                    'old_workflow_status': old_status,
                    'new_workflow_status': 'Withdrawn',
                    'decline_reason': decline_reason,
                    'actor': cand.email if cand else 'candidate',
                    'source': 'portal_offer_decline',
                    'ip': ip,
                }
            )
        except Exception:
            pass

    flash("Your offer has been declined. We'll be in touch about other opportunities.", "info")
    return redirect(url_for("associate.my_applications"))


@associate_bp.route("/assignments")
@_require_login
def assignments():
    """Removed — redirects to my-applications."""
    return redirect(url_for("associate.my_applications"))


@associate_bp.route("/timesheets", methods=["GET"])
@_require_login
def timesheets():
    """View and manage timesheets with weekly grid."""
    import json as _json
    from collections import defaultdict
    Timesheet = _model("Timesheet")
    Application = _model("Application")
    Engagement = _model("Engagement")
    TimesheetConfig = _portal_model("TimesheetConfig")
    TimesheetEntry = _portal_model("TimesheetEntry")
    TimesheetExpense = _portal_model("TimesheetExpense")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        # Get candidate's contracted assignments for dropdown —
        # show applications where the candidate has a signed contract
        # (either by application status OR by having a signed ESigRequest).
        assignments = []
        PLACED_STATUSES = {
            "placed", "contract signed", "contract issued",
            "contract sent", "active", "hired", "on contract",
        }
        # Also check if the candidate has a signed contract at the
        # candidate level (umbrella_assignment_signed = True).
        Candidate = _model("Candidate")
        cand_has_signed_contract = False
        signed_app_ids = set()
        try:
            cand_row = s.execute(text(
                "SELECT umbrella_assignment_signed FROM candidates WHERE id = :cid"
            ).bindparams(cid=cand_id)).first()
            if cand_row and cand_row[0]:
                cand_has_signed_contract = True
        except Exception:
            pass
        try:
            signed_esigs = s.execute(text(
                "SELECT application_id FROM esign_requests "
                "WHERE candidate_id = :cid AND LOWER(status) IN ('signed', 'completed')"
            ).bindparams(cid=cand_id)).all()
            signed_app_ids = {r[0] for r in signed_esigs if r[0]}
        except Exception:
            pass
        if Application:
            apps = s.query(Application).filter_by(candidate_id=cand_id).all()
            for app in apps:
                status_match = (app.status or "").strip().lower() in PLACED_STATUSES
                esig_match = app.id in signed_app_ids
                # If candidate has a signed contract, include the most
                # recent application regardless of its status.
                cand_match = cand_has_signed_contract and app == apps[0]
                if not status_match and not esig_match and not cand_match:
                    continue
                job = app.job if hasattr(app, "job") and app.job else None
                eng = None
                if job and hasattr(job, "engagement_id") and job.engagement_id and Engagement:
                    eng = s.query(Engagement).get(job.engagement_id)
                assignments.append({
                    "id": app.id,
                    "engagement_id": job.engagement_id if job and hasattr(job, "engagement_id") else None,
                    "job_title": job.title if job else "Assignment",
                    "client": eng.client if eng else "",
                    "engagement_name": eng.name if eng else "",
                })

        # Get all timesheets for this candidate
        all_sheets = []
        if Timesheet:
            all_sheets = s.query(Timesheet).filter_by(user_id=cand_id).order_by(
                Timesheet.period_start.desc()
            ).all()

        # Current draft timesheet
        current_ts = None
        previous_sheets = []
        for ts in all_sheets:
            if not current_ts and getattr(ts, "status", "") in ("Draft", "Unsubmitted", None, ""):
                current_ts = ts
            else:
                previous_sheets.append(ts)

        # Load config, entries, and expenses for current timesheet
        config = None
        time_types = ["Standard Time", "Overtime", "Holiday", "Sickness"]
        entries = {}  # {(date_str, time_type): value}
        expenses = []

        if current_ts:
            # Get config for this timesheet's engagement
            eng_id = getattr(current_ts, "engagement_id", None)
            if eng_id and TimesheetConfig:
                config = s.query(TimesheetConfig).filter_by(engagement_id=eng_id).first()
            if config:
                try:
                    time_types = _json.loads(config.time_types)
                except Exception:
                    pass

            # Load entries
            if TimesheetEntry:
                entry_rows = s.query(TimesheetEntry).filter_by(timesheet_id=current_ts.id).all()
                for e in entry_rows:
                    date_str = e.entry_date.strftime("%Y-%m-%d") if e.entry_date else ""
                    entries[(date_str, e.time_type)] = {"value": e.value, "unit": e.value_unit}

            # Load expenses
            if TimesheetExpense:
                expenses = s.query(TimesheetExpense).filter_by(timesheet_id=current_ts.id).all()

        # Build week days for grid
        week_days = []
        if current_ts and getattr(current_ts, "week_start", None):
            ws = current_ts.week_start
            for i in range(7):
                d = ws + timedelta(days=i)
                week_days.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "label": d.strftime("%a %d %b"),
                    "short": d.strftime("%a"),
                })

        # Group previous timesheets by month
        monthly_bundles = defaultdict(lambda: {"sheets": [], "total_days": 0, "total_hours": 0, "total_expenses": 0, "total_amount": 0})
        for ts in previous_sheets:
            ws = getattr(ts, "week_start", None) or getattr(ts, "period_start", None)
            if ws:
                month_key = ws.strftime("%Y-%m")
                month_label = ws.strftime("%B %Y")
            else:
                month_key = "unknown"
                month_label = "Unknown"
            bundle = monthly_bundles[month_key]
            bundle["label"] = month_label
            bundle["sheets"].append(ts)
            bundle["total_days"] += getattr(ts, "billable_days", 0) or 0
            bundle["total_hours"] += getattr(ts, "billable_hours", 0) or 0
            bundle["total_expenses"] += getattr(ts, "expense_total", 0) or 0
            bundle["total_amount"] += getattr(ts, "grand_total", 0) or getattr(ts, "total_amount", 0) or 0

        # Sort monthly bundles by key (most recent first)
        sorted_bundles = sorted(monthly_bundles.items(), key=lambda x: x[0], reverse=True)

        expense_enabled = config.expense_enabled if config else False
        # TS 2 — Overtime is hidden by default; an OS1 admin turns it on per
        # engagement via the timesheet config. When off, drop the overtime
        # time-types so the OT row never renders on the Associate's grid.
        overtime_enabled = config.overtime_enabled if config else False
        if not overtime_enabled:
            time_types = [t for t in time_types if "overtime" not in (t or "").lower()]
        expense_types_list = []
        if config:
            try:
                expense_types_list = _json.loads(config.expense_types)
            except Exception:
                expense_types_list = []

        # Phase 2 / TS 12, 24 — surface ExpenseCategory rows for the new
        # dropdown UI. Each entry has id, name, vat_treatment, is_mileage
        # so the template + JS can swap between £-amount and miles-distance
        # inputs and show a VAT preview.
        expense_categories = []
        try:
            cat_rows = s.execute(text(
                "SELECT id, name, vat_treatment, is_mileage FROM expense_categories ORDER BY sort_order, name"
            )).all()
            for r in cat_rows:
                expense_categories.append({
                    "id": r[0],
                    "name": r[1],
                    "vat_treatment": r[2],
                    "is_mileage": bool(r[3]),
                })
        except Exception:
            pass

        # Phase 1 / TS 7 visual — UK bank-holiday dates (England-and-Wales)
        # passed as ISO date strings for the template's CSS shading logic.
        bank_holidays = []
        try:
            bh_rows = s.execute(text(
                "SELECT date FROM bank_holiday_dates WHERE region = :r"
            ).bindparams(r='england-and-wales')).all()
            bank_holidays = [r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]) for r in bh_rows]
        except Exception:
            pass

        # Phase 1 / TS 2 — whether the current viewer is allowed to see
        # the Overtime + Expenses sections. Default Associates are
        # restricted; OS1 staff users at 'employee' level or above (which
        # in practice means any role other than 'associate') can see them.
        can_view_advanced = True   # default — we don't yet have a
        # session-scoped staff role on the associate portal. If a portal
        # role flag is added later this becomes the right gate.

        # Phase 2 / TS 25 — central HMRC mileage rate so the template can
        # display the live derivation "miles × £rate" preview.
        try:
            from app import _company_settings
            hmrc_mileage_rate = float(_company_settings().get("hmrc_mileage_rate", 0.45))
        except Exception:
            hmrc_mileage_rate = 0.45

        # Phase 1 / TS 3 — WC picker window. Match the server-side guard
        # in _is_wc_in_window so the date input only offers Mondays
        # within {two prior WCs, current WC, next WC}.
        _today = date.today()
        _today_monday = _today - timedelta(days=_today.weekday())
        wc_window_min = (_today_monday - timedelta(weeks=2)).isoformat()
        wc_window_max = (_today_monday + timedelta(weeks=1)).isoformat()
        wc_window_default = _today_monday.isoformat()

        return render_template(
            "associate/timesheets.html",
            assignments=assignments,
            current_ts=current_ts,
            config=config,
            time_types=time_types,
            entries=entries,
            expenses=expenses,
            week_days=week_days,
            monthly_bundles=sorted_bundles,
            expense_enabled=expense_enabled,
            overtime_enabled=overtime_enabled,
            expense_types=expense_types_list,
            expense_categories=expense_categories,
            bank_holidays=bank_holidays,
            can_view_advanced=can_view_advanced,
            hmrc_mileage_rate=hmrc_mileage_rate,
            wc_window_min=wc_window_min,
            wc_window_max=wc_window_max,
            wc_window_default=wc_window_default,
        )


@associate_bp.route("/vacancies")
@_require_login
def vacancies():
    """Browse open vacancies with optional search/filter."""
    Job = _model("Job")
    engine = _engine()

    search_query = request.args.get("q", "").strip()
    location_filter = request.args.get("location", "").strip()

    with SASession(engine) as s:
        jobs = []
        locations = []
        if Job:
            query = s.query(Job).filter(
                (Job.status == "Open") | (Job.status == None)  # noqa: E711
            )
            if search_query:
                like_q = f"%{search_query}%"
                query = query.filter(
                    Job.title.ilike(like_q) | Job.description.ilike(like_q)
                )
            if location_filter and hasattr(Job, "location"):
                query = query.filter(Job.location.ilike(f"%{location_filter}%"))

            jobs = query.order_by(Job.created_at.desc()).all()

            # Build distinct location list for the filter dropdown
            if hasattr(Job, "location"):
                loc_rows = s.query(Job.location).filter(
                    ((Job.status == "Open") | (Job.status == None)),  # noqa: E711
                    Job.location != None, Job.location != ""  # noqa: E711
                ).distinct().all()
                locations = sorted(set(r[0] for r in loc_rows if r[0]))

        return render_template(
            "associate/vacancies.html",
            jobs=jobs,
            search_query=search_query,
            location_filter=location_filter,
            locations=locations,
        )


# =========================================================================
# API ROUTES (AJAX)
# =========================================================================

@associate_bp.route("/api/upload-document", methods=["POST"])
@_require_login
def api_upload_document():
    """AJAX endpoint for uploading a document."""
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    file = request.files.get("file")
    doc_type = _sanitise(request.form.get("doc_type", "other"))

    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    allowed = {"pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"}
    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    if ext not in allowed:
        return jsonify({"error": f"File type .{ext} not allowed"}), 400

    saved = _save_file(file)
    if not saved:
        return jsonify({"error": "Failed to save file"}), 500

    doc_id = None
    with SASession(engine) as s:
        if Document:
            doc = Document(
                candidate_id=cand_id,
                doc_type=doc_type,
                filename=saved["filename"],
                original_name=saved["original_name"],
                uploaded_at=datetime.utcnow(),
            )
            s.add(doc)
            _add_note(s, cand_id, f"Document uploaded via API: {saved['original_name']} (type: {doc_type}).")
            s.commit()
            doc_id = doc.id

    return jsonify({
        "success": True,
        "document": {
            "id": doc_id,
            "filename": saved["filename"],
            "original_name": saved["original_name"],
        },
    })


@associate_bp.route("/api/reference-contacts")
@_require_login
def api_reference_contacts():
    """Search known reference contacts by company name prefix."""
    ReferenceContact = _portal_model("ReferenceContact")
    engine = _engine()
    q = _sanitise(request.args.get("q", "")).strip()

    if not q or len(q) < 2:
        return jsonify({"results": []})

    results = []
    if ReferenceContact:
        with SASession(engine) as s:
            contacts = s.query(ReferenceContact).filter(
                ReferenceContact.company_name.ilike(f"{q}%")
            ).limit(20).all()
            results = [
                {
                    "company_name": c.company_name,
                    "referee_email": c.referee_email,
                    "last_amended": c.last_amended.isoformat() if c.last_amended else None,
                }
                for c in contacts
            ]

    return jsonify({"results": results})


@associate_bp.route("/api/check-reference-house")
@_require_login
def api_check_reference_house():
    """Check if a reference house name is flagged as suspicious."""
    FlaggedReferenceHouse = _portal_model("FlaggedReferenceHouse")
    engine = _engine()
    name = _sanitise(request.args.get("name", "")).strip()

    if not name:
        return jsonify({"flagged": False})

    flagged = False
    details = None
    if FlaggedReferenceHouse:
        with SASession(engine) as s:
            match = s.query(FlaggedReferenceHouse).filter(
                FlaggedReferenceHouse.name.ilike(name)
            ).first()
            if match:
                flagged = True
                details = {
                    "name": match.name,
                    "candidate_count": match.candidate_count,
                    "end_clients": match.end_clients,
                    "website": match.website,
                    "notes": match.notes,
                }

    return jsonify({"flagged": flagged, "details": details})


@associate_bp.route("/api/company-lookup")
@_require_login
def api_company_lookup():
    """Combined company name lookup: reference contacts + flagged reference houses."""
    ReferenceContact = _portal_model("ReferenceContact")
    FlaggedReferenceHouse = _portal_model("FlaggedReferenceHouse")
    engine = _engine()
    q = _sanitise(request.args.get("q", "")).strip()

    if not q or len(q) < 2:
        return jsonify({"results": []})

    exact = request.args.get("exact", "") == "1"

    results = []
    with SASession(engine) as s:
        # Find matching reference contacts
        contacts = []
        if ReferenceContact:
            if exact:
                contacts = s.query(ReferenceContact).filter(
                    func.lower(ReferenceContact.company_name) == q.lower()
                ).limit(1).all()
            else:
                contacts = s.query(ReferenceContact).filter(
                    ReferenceContact.company_name.ilike(f"{q}%")
                ).limit(10).all()

        # Build set of flagged house names for cross-check
        flagged_names = set()
        if FlaggedReferenceHouse:
            flagged = s.query(FlaggedReferenceHouse.name).all()
            flagged_names = {f.name.lower() for f in flagged}

        seen = set()
        for c in contacts:
            key = c.company_name.lower()
            if key not in seen:
                seen.add(key)
                results.append({
                    "name": c.company_name,
                    "referee_email": c.referee_email,
                    "is_reference_house": key in flagged_names,
                })

    return jsonify({"results": results})


@associate_bp.route("/api/add-reference", methods=["POST"])
@_require_login
def api_add_reference():
    """Add an employment history entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        return jsonify({"error": "Employment history not available"}), 500

    data = request.get_json(silent=True) or {}

    company_name = _sanitise(data.get("company_name", ""))
    if not company_name:
        return jsonify({"error": "Company name is required"}), 400

    with SASession(engine) as s:
        # NOTE: permission_to_request no longer set per-entry.
        # Global reference consent is on ConsentRecord.reference_consent.
        entry = EmploymentHistory(
            candidate_id=cand_id,
            company_name=company_name,
            agency_name=_sanitise(data.get("agency_name", "")),
            referee_email=_sanitise(data.get("referee_email", "")),
            company_address=_sanitise(data.get("company_address", "")),
            start_date=_parse_date(data.get("start_date", "")),
            end_date=_parse_date(data.get("end_date", "")),
            job_title=_sanitise(data.get("job_title", "")),
            reason_for_leaving=_sanitise(data.get("reason_for_leaving", "")),
            is_gap=_parse_bool(data.get("is_gap", False)),
            gap_reason=_sanitise(data.get("gap_reason", "")),
            reference_status="not_sent",
        )
        s.add(entry)
        _add_note(s, cand_id, f"Employment history added: {company_name}.")
        s.commit()
        entry_id = entry.id

    return jsonify({"success": True, "id": entry_id})


@associate_bp.route("/api/add-qualification", methods=["POST"])
@_require_login
def api_add_qualification():
    """Add a qualification entry."""
    QualificationRecord = _portal_model("QualificationRecord")
    engine = _engine()
    cand_id = _get_associate_id()

    if not QualificationRecord:
        return jsonify({"error": "Qualifications not available"}), 500

    data = request.get_json(silent=True) or {}

    qual_name = _sanitise(data.get("qualification_name", ""))
    if not qual_name:
        return jsonify({"error": "Qualification name is required"}), 400

    with SASession(engine) as s:
        qual = QualificationRecord(
            candidate_id=cand_id,
            qualification_name=qual_name,
            qualification_type=_sanitise(data.get("qualification_type", "")),
            grade=_sanitise(data.get("grade", "")),
            institution=_sanitise(data.get("institution", "")),
            start_date=_parse_date(data.get("start_date", "")),
            end_date=_parse_date(data.get("end_date", "")),
            permission_to_request=_parse_bool(data.get("permission_to_request", True)),
            permission_delay_reason=_sanitise(data.get("permission_delay_reason", "")),
        )
        s.add(qual)
        _add_note(s, cand_id, f"Qualification added: {qual_name}.")
        s.commit()
        qual_id = qual.id

    return jsonify({"success": True, "id": qual_id})


def _resolve_document_path(filename: str) -> Optional[str]:
    """Locate a stored associate document on disk, in priority order:
    Railway persistent volume (/data), legacy static folder, UPLOAD_FOLDER.
    Mirrors app._doc_file_path so the associate portal stays in sync.
    """
    if not filename:
        return None

    rel = filename
    for prefix in ("uploads/associate_docs/", "uploads/cvs/", "uploads/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break

    candidates = []
    if os.path.isdir("/data"):
        candidates.append(os.path.join("/data", filename))
        candidates.append(os.path.join("/data/uploads/associate_docs", rel))
        candidates.append(os.path.join("/data/uploads/cvs", rel))

    app_root = current_app.root_path
    candidates.append(os.path.join(app_root, "static", filename))
    candidates.append(os.path.join(app_root, "static", "uploads", "associate_docs", rel))
    candidates.append(os.path.join(app_root, "static", "uploads", "cvs", rel))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


@associate_bp.route("/api/download-document/<int:doc_id>")
@_require_login
def api_download_document(doc_id):
    """Download a document file belonging to the current associate."""
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    if not Document:
        abort(404)

    with SASession(engine) as s:
        doc = s.query(Document).filter_by(id=doc_id, candidate_id=cand_id).first()
        if not doc:
            abort(404)
        filepath = _resolve_document_path(doc.filename or "")
        if not filepath:
            current_app.logger.warning(
                "api_download_document: file not found on disk for doc_id=%s filename=%s",
                doc_id, doc.filename,
            )
            abort(404)
        download_name = getattr(doc, "original_name", None) or os.path.basename(doc.filename)
        return send_file(filepath, as_attachment=True, download_name=download_name)


@associate_bp.route("/api/delete-document/<int:doc_id>", methods=["DELETE"])
@_require_login
def api_delete_document(doc_id):
    """Delete a document belonging to the current associate."""
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    if not Document:
        return jsonify({"success": False, "error": "Not available"}), 500

    with SASession(engine) as s:
        doc = s.query(Document).filter_by(id=doc_id, candidate_id=cand_id).first()
        if not doc:
            return jsonify({"success": False, "error": "Document not found"}), 404
        fname = getattr(doc, "original_name", doc.filename)
        # Remove file from disk
        filepath = os.path.join(current_app.root_path, "static", doc.filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
        s.delete(doc)
        _add_note(s, cand_id, f"Document deleted: {fname}.")
        s.commit()

    return jsonify({"success": True})


# =========================================================================
# TIMESHEET CRUD ROUTES
# =========================================================================

def _split_week_at_month_end(week_start):
    """Phase 1 / TS 1 — when a Mon-Sun week crosses a calendar month
    boundary, split it into two (week_start, week_end) tuples — one ending
    on the last day of the prior month, one starting on the first day of
    the new month. Each returned tuple is independently submittable /
    invoiceable. Returns a list of 1 or 2 tuples (always non-empty)."""
    natural_end = week_start + timedelta(days=6)
    # If the natural end is in the same month, no split needed.
    if natural_end.month == week_start.month and natural_end.year == week_start.year:
        return [(week_start, natural_end)]
    # Split: first half ends on the last day of week_start.month.
    # Find first day of next month, then -1 day = last day of current month.
    if week_start.month == 12:
        next_month_first = date(week_start.year + 1, 1, 1)
    else:
        next_month_first = date(week_start.year, week_start.month + 1, 1)
    first_half_end = next_month_first - timedelta(days=1)
    second_half_start = next_month_first
    return [(week_start, first_half_end), (second_half_start, natural_end)]


def _clamp_time_value(value, is_overtime: bool = False) -> float:
    """Phase 1 / TS 7 — restrict standard / holiday / sickness time
    entries to {0, 0.5, 1}. Rounds to nearest 0.5 then clamps. Overtime
    keeps free-form hours (its unit is hours, not days, so the {0, 0.5,
    1} constraint doesn't apply)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if is_overtime:
        # Overtime: just keep non-negative.
        return max(0.0, v)
    # Round to nearest 0.5, clamp to [0, 1].
    snapped = round(v * 2) / 2
    if snapped < 0:
        return 0.0
    if snapped > 1:
        return 1.0
    return snapped


def _is_wc_in_window(week_start) -> bool:
    """Phase 1 / TS 3 — restrict associate-portal timesheet creation to
    the current WC, the next WC, and the two previous WCs. Admins on the
    staff platform create out-of-window timesheets via a separate path."""
    if not week_start:
        return False
    today = date.today() if not hasattr(week_start, "date") else date.today()
    today_monday = today - timedelta(days=today.weekday())
    earliest = today_monday - timedelta(weeks=2)
    latest = today_monday + timedelta(weeks=1)
    return earliest <= week_start <= latest


@associate_bp.route("/timesheets/new", methods=["POST"])
@_require_login
def timesheets_new():
    """Create a new weekly timesheet."""
    Timesheet = _model("Timesheet")
    TimesheetConfig = _portal_model("TimesheetConfig")
    engine = _engine()
    cand_id = _get_associate_id()

    assignment_id = request.form.get("assignment_id")
    week_start_str = request.form.get("week_start", "")

    if not assignment_id or not week_start_str:
        flash("Please select an assignment and week.", "danger")
        return redirect(url_for("associate.timesheets"))

    week_start = _parse_date(week_start_str)
    if not week_start:
        flash("Invalid week start date.", "danger")
        return redirect(url_for("associate.timesheets"))

    # Ensure it's a Monday
    if hasattr(week_start, 'weekday') and week_start.weekday() != 0:
        flash("Week must start on a Monday.", "danger")
        return redirect(url_for("associate.timesheets"))

    # Phase 1 / TS 3 — WC window guard. Two weeks back through to the
    # next week commencing. Out-of-window timesheets are created only by
    # an OS1 staff user via /admin/timesheets/new-for-associate (Phase 4).
    if not _is_wc_in_window(week_start):
        flash(
            "Timesheets can only be created for the current week, the next "
            "week, or the two previous weeks. Contact the Optimus team if "
            "you need a timesheet outside this window.",
            "danger"
        )
        return redirect(url_for("associate.timesheets"))

    # Phase 1 / TS 1 — split the week at month-end if it crosses one.
    week_splits = _split_week_at_month_end(week_start)

    # Get engagement_id from Application
    Application = _model("Application")
    engagement_id = None
    with SASession(engine) as s:
        if Application:
            app = s.get(Application, int(assignment_id))
            if app and hasattr(app, "job") and app.job and hasattr(app.job, "engagement_id"):
                engagement_id = app.job.engagement_id

        # Load rates: TimesheetConfig first, then fall back to the
        # assignment fee (from Issue Contract capture sheet), then
        # offer_day_rate (from the offer).
        day_rate = 0
        overtime_rate = 0
        if engagement_id and TimesheetConfig:
            config = s.query(TimesheetConfig).filter_by(engagement_id=engagement_id).first()
            if config:
                day_rate = config.day_rate or 0
                overtime_rate = config.overtime_rate or 0
        if not day_rate and Application:
            app_for_rate = s.get(Application, int(assignment_id))
            if app_for_rate:
                # assignment_fee is a string like "£500.00 / day" — extract the number
                fee_str = getattr(app_for_rate, "assignment_fee", "") or ""
                if fee_str:
                    import re
                    nums = re.findall(r"[\d,]+\.?\d*", fee_str.replace(",", ""))
                    if nums:
                        try:
                            day_rate = float(nums[0])
                        except ValueError:
                            pass
                if not day_rate and app_for_rate.offer_day_rate:
                    day_rate = float(app_for_rate.offer_day_rate)

        # Phase 1 / TS 4 — duplicate check on (user_id, week_start, engagement_id).
        # We use the first split's week_start (which always equals the
        # submitted week_start). If a row exists in ANY status, refuse and
        # redirect to the existing one rather than creating a duplicate.
        existing = s.query(Timesheet).filter_by(
            user_id=cand_id, engagement_id=(engagement_id or 0), period_start=week_splits[0][0]
        ).first()
        if existing:
            flash(
                f"A timesheet for that week already exists (status: {existing.status}). "
                f"Opening the existing one instead of creating a duplicate.",
                "info"
            )
            return redirect(url_for("associate.timesheets"))

        created_ids = []
        for ws, we in week_splits:
            ts = Timesheet(
                user_id=cand_id,
                engagement_id=engagement_id or 0,
                period_start=ws,
                period_end=we,
                status="Draft",
            )
            if hasattr(ts, "week_start"):
                ts.week_start = ws
                ts.week_end = we
                ts.day_rate = day_rate
                ts.overtime_rate = overtime_rate
            s.add(ts)
            s.flush()
            created_ids.append(ts.id)
        s.commit()

    if len(week_splits) == 2:
        flash(
            f"That week crossed a month-end, so two timesheets were created — "
            f"one ending {week_splits[0][1].strftime('%d %b')} and one starting "
            f"{week_splits[1][0].strftime('%d %b')}. Fill both in and submit "
            f"each separately.",
            "info",
        )
    else:
        flash("New timesheet created.", "success")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/save", methods=["POST"])
@_require_login
def timesheets_save():
    """Save timesheet grid entries and expenses as Draft."""
    import json as _json
    Timesheet = _model("Timesheet")
    TimesheetEntry = _portal_model("TimesheetEntry")
    TimesheetExpense = _portal_model("TimesheetExpense")
    engine = _engine()
    cand_id = _get_associate_id()
    ts_id = request.form.get("timesheet_id")

    if not ts_id:
        flash("No timesheet specified.", "danger")
        return redirect(url_for("associate.timesheets"))

    with SASession(engine) as s:
        ts = s.get(Timesheet, int(ts_id))
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        # Phase 1 / TS 5 — Rejected timesheets re-enable editing on the
        # associate portal so the associate amends the SAME row and
        # re-submits, instead of creating a duplicate. Approved /
        # Submitted timesheets are read-only unless an admin re-opens.
        if (ts.status or "").lower() not in ("draft", "rejected"):
            flash(
                f"This timesheet is {ts.status} and can't be edited. "
                f"Ask an admin to re-open it if you need to make changes.",
                "warning"
            )
            return redirect(url_for("associate.timesheets"))

        # Delete existing entries and re-create from form
        if TimesheetEntry:
            s.query(TimesheetEntry).filter_by(timesheet_id=ts.id).delete()

            # Parse grid entries: entry_<date>_<time_type> = value
            total_days = 0
            total_hours = 0
            for key, val in request.form.items():
                if key.startswith("entry_") and val and val != "0" and val != "-":
                    parts = key.split("_", 2)  # entry_2026-03-23_Standard__Time
                    if len(parts) >= 3:
                        entry_date_str = parts[1]
                        time_type = parts[2].replace("__", " ")
                        try:
                            value = float(val)
                        except ValueError:
                            # Handle HH:MM format for overtime
                            if ":" in val:
                                hh, mm = val.split(":")
                                value = float(hh) + float(mm) / 60
                            else:
                                continue

                        unit = "hours" if "overtime" in time_type.lower() else "days"
                        # Phase 1 / TS 7 — non-overtime entries clamp to {0, 0.5, 1}.
                        value = _clamp_time_value(value, is_overtime=(unit == "hours"))
                        entry = TimesheetEntry(
                            timesheet_id=ts.id,
                            entry_date=_parse_date(entry_date_str),
                            time_type=time_type,
                            value=value,
                            value_unit=unit,
                        )
                        s.add(entry)

                        if time_type.lower() == "standard time":
                            total_days += value
                        elif "overtime" in time_type.lower():
                            total_hours += value

            ts.billable_days = total_days
            ts.billable_hours = total_hours

        # Expenses are owned solely by the add/edit/delete-expense routes —
        # never delete+recreate them here (that would drop the category, VAT,
        # date, mileage and receipt captured by those routes). Just recompute
        # the timesheet's expense total from the live rows.
        expense_total = 0
        if TimesheetExpense:
            exp_rows = s.query(TimesheetExpense).filter_by(timesheet_id=ts.id).all()
            expense_total = sum(float(r.amount or 0) for r in exp_rows)

        # Calculate totals
        day_rate = getattr(ts, "day_rate", 0) or 0
        ot_rate = getattr(ts, "overtime_rate", 0) or 0
        if not ot_rate and day_rate:
            ot_rate = round(day_rate / 7.5, 2)
        ts.total_amount = (ts.billable_days * day_rate) + (ts.billable_hours * ot_rate)
        ts.expense_total = expense_total
        ts.grand_total = ts.total_amount + ts.expense_total

        ts.status = "Draft"
        s.commit()

    flash("Timesheet saved as draft.", "success")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/submit", methods=["POST"])
@_require_login
def timesheets_submit():
    """Submit a timesheet (saves entries first, then changes status to Submitted)."""
    import json as _json
    Timesheet = _model("Timesheet")
    TimesheetEntry = _portal_model("TimesheetEntry")
    engine = _engine()
    cand_id = _get_associate_id()
    ts_id = request.form.get("timesheet_id")

    if not ts_id:
        flash("No timesheet specified.", "danger")
        return redirect(url_for("associate.timesheets"))

    with SASession(engine) as s:
        ts = s.get(Timesheet, int(ts_id))
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        # Phase 1 / TS 5 — submit allowed only from Draft or Rejected.
        # Already-Submitted or Approved timesheets are read-only until an
        # admin re-opens.
        if (ts.status or "").lower() not in ("draft", "rejected"):
            flash(
                f"This timesheet is {ts.status} and cannot be re-submitted. "
                f"Ask an admin to re-open it if you need to make changes.",
                "warning"
            )
            return redirect(url_for("associate.timesheets"))

        # Save entries from form before submitting
        if TimesheetEntry:
            s.query(TimesheetEntry).filter_by(timesheet_id=ts.id).delete()

            total_days = 0
            total_hours = 0
            for key, val in request.form.items():
                if key.startswith("entry_") and val and val != "0" and val != "-":
                    parts = key.split("_", 2)
                    if len(parts) >= 3:
                        entry_date_str = parts[1]
                        time_type = parts[2].replace("__", " ")
                        try:
                            value = float(val)
                        except ValueError:
                            if ":" in val:
                                hh, mm = val.split(":")
                                value = float(hh) + float(mm) / 60
                            else:
                                continue

                        unit = "hours" if "overtime" in time_type.lower() else "days"
                        # Phase 1 / TS 7 — non-overtime entries clamp to {0, 0.5, 1}.
                        value = _clamp_time_value(value, is_overtime=(unit == "hours"))
                        entry = TimesheetEntry(
                            timesheet_id=ts.id,
                            entry_date=_parse_date(entry_date_str),
                            time_type=time_type,
                            value=value,
                            value_unit=unit,
                        )
                        s.add(entry)

                        if time_type.lower() == "standard time":
                            total_days += value
                        elif "overtime" in time_type.lower():
                            total_hours += value

            ts.billable_days = total_days
            ts.billable_hours = total_hours

        # Expenses are owned solely by the add/edit/delete-expense routes —
        # never delete+recreate them here. Recompute the total from live rows.
        TimesheetExpense = _portal_model("TimesheetExpense")
        expense_total = 0
        if TimesheetExpense:
            exp_rows = s.query(TimesheetExpense).filter_by(timesheet_id=ts.id).all()
            expense_total = sum(float(r.amount or 0) for r in exp_rows)

        # Calculate totals
        day_rate = getattr(ts, "day_rate", 0) or 0
        ot_rate = getattr(ts, "overtime_rate", 0) or 0
        if not ot_rate and day_rate:
            ot_rate = round(day_rate / 7.5, 2)
        ts.total_amount = (ts.billable_days * day_rate) + (ts.billable_hours * ot_rate)
        ts.expense_total = expense_total
        ts.grand_total = ts.total_amount + ts.expense_total

        ts.status = "Submitted"
        ts.submitted_at = datetime.utcnow()
        s.commit()

    flash("Timesheet submitted successfully.", "success")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/cancel", methods=["POST"])
@_require_login
def timesheets_cancel():
    """Cancel/delete a draft timesheet."""
    Timesheet = _model("Timesheet")
    TimesheetEntry = _portal_model("TimesheetEntry")
    TimesheetExpense = _portal_model("TimesheetExpense")
    engine = _engine()
    cand_id = _get_associate_id()
    ts_id = request.form.get("timesheet_id")

    if not ts_id:
        flash("No timesheet specified.", "danger")
        return redirect(url_for("associate.timesheets"))

    with SASession(engine) as s:
        ts = s.get(Timesheet, int(ts_id))
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        if ts.status not in ("Draft", "Unsubmitted", None, ""):
            flash("Only draft timesheets can be cancelled.", "warning")
            return redirect(url_for("associate.timesheets"))

        # Delete entries and expenses
        if TimesheetEntry:
            s.query(TimesheetEntry).filter_by(timesheet_id=ts.id).delete()
        if TimesheetExpense:
            s.query(TimesheetExpense).filter_by(timesheet_id=ts.id).delete()
        s.delete(ts)
        s.commit()

    flash("Timesheet cancelled.", "info")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/generate-invoice", methods=["POST"])
@_require_login
def timesheets_generate_invoice():
    """Generate a PDF invoice from selected timesheets."""
    Timesheet = _model("Timesheet")
    Candidate = _model("Candidate")
    engine = _engine()
    cand_id = _get_associate_id()
    ts_ids = request.form.getlist("ts_ids")

    if not ts_ids:
        flash("Select at least one timesheet to generate an invoice.", "warning")
        return redirect(url_for("associate.timesheets"))

    try:
        ts_ids_int = [int(t) for t in ts_ids]
    except ValueError:
        flash("Invalid timesheet selection.", "danger")
        return redirect(url_for("associate.timesheets"))

    with SASession(engine) as s:
        cand = s.get(Candidate, cand_id) if Candidate else None
        cand_name = (cand.name if cand else "") or "Associate"

        sheets = []
        if Timesheet:
            for tid in ts_ids_int:
                ts = s.get(Timesheet, tid)
                if ts and ts.user_id == cand_id and ts.status == "Approved":
                    sheets.append(ts)

        if not sheets:
            flash("No valid timesheets found.", "warning")
            return redirect(url_for("associate.timesheets"))

        sheets.sort(key=lambda t: t.period_start or datetime.min)

        from fpdf import FPDF
        import io

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()

        # Header
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "INVOICE", ln=True, align="C")
        pdf.ln(4)

        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Associate: {cand_name}", ln=True)
        pdf.cell(0, 6, f"Date: {datetime.utcnow().strftime('%d %B %Y')}", ln=True)
        pdf.cell(0, 6, f"Timesheets included: {len(sheets)}", ln=True)

        first_start = min((t.period_start for t in sheets if t.period_start), default=None)
        last_end = max((t.period_end for t in sheets if t.period_end), default=None)
        if first_start and last_end:
            pdf.cell(0, 6, f"Period: {first_start.strftime('%d/%m/%Y')} - {last_end.strftime('%d/%m/%Y')}", ln=True)
        pdf.ln(6)

        # Table header
        pdf.set_font("Helvetica", "B", 9)
        col_w = [45, 20, 20, 25, 25, 25, 30]
        headers = ["Period", "Days", "OT Hrs", "Day Amt", "OT Amt", "Expenses", "Total"]
        for i, h in enumerate(headers):
            pdf.cell(col_w[i], 8, h, border=1, align="C")
        pdf.ln()

        # Table rows
        pdf.set_font("Helvetica", "", 9)
        grand_total = 0
        total_days = 0
        total_hours = 0
        total_expenses = 0
        for ts in sheets:
            period = ""
            if ts.period_start and ts.period_end:
                period = f"{ts.period_start.strftime('%d/%m')} - {ts.period_end.strftime('%d/%m/%Y')}"
            elif ts.period_start:
                period = ts.period_start.strftime('%d/%m/%Y')

            days = ts.billable_days or 0
            hours = ts.billable_hours or 0
            rate = ts.day_rate or 0
            ot_rate_inv = getattr(ts, "overtime_rate", 0) or 0
            if not ot_rate_inv and rate:
                ot_rate_inv = round(rate / 7.5, 2)
            day_amount = days * rate
            ot_amount = (ts.total_amount or 0) - day_amount if (ts.total_amount or 0) > day_amount else hours * ot_rate_inv
            expenses = getattr(ts, "expense_total", 0) or 0
            line_total = day_amount + ot_amount + expenses
            total_days += days
            total_hours += hours
            total_expenses += expenses
            grand_total += line_total

            pdf.cell(col_w[0], 7, period, border=1)
            pdf.cell(col_w[1], 7, f"{days:.1f}", border=1, align="C")
            pdf.cell(col_w[2], 7, f"{hours:.1f}", border=1, align="C")
            pdf.cell(col_w[3], 7, f"£{day_amount:.2f}", border=1, align="R")
            pdf.cell(col_w[4], 7, f"£{ot_amount:.2f}", border=1, align="R")
            pdf.cell(col_w[5], 7, f"£{expenses:.2f}", border=1, align="R")
            pdf.cell(col_w[6], 7, f"£{line_total:.2f}", border=1, align="R")
            pdf.ln()

        # Totals row
        pdf.set_font("Helvetica", "B", 9)
        total_day_amt = total_days * (sheets[0].day_rate or 0) if sheets else 0
        total_ot_amt = grand_total - total_day_amt - total_expenses
        pdf.cell(col_w[0], 8, "TOTAL", border=1, align="R")
        pdf.cell(col_w[1], 8, f"{total_days:.1f}", border=1, align="C")
        pdf.cell(col_w[2], 8, f"{total_hours:.1f}", border=1, align="C")
        pdf.cell(col_w[3], 8, f"£{total_day_amt:.2f}", border=1, align="R")
        pdf.cell(col_w[4], 8, f"£{total_ot_amt:.2f}", border=1, align="R")
        pdf.cell(col_w[5], 8, f"£{total_expenses:.2f}", border=1, align="R")
        pdf.cell(col_w[6], 8, f"£{grand_total:.2f}", border=1, align="R")
        pdf.ln(12)

        # Footer
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "71-75 Shelton Street | London | WC2H 9JQ", ln=True, align="C")
        pdf.cell(0, 6, "associates@optimussolutions.co.uk", ln=True, align="C")
        pdf.cell(0, 6, "Optimus - Financial Services Resourcing Specialists", ln=True, align="C")

        buf = io.BytesIO()
        pdf.output(buf)
        buf.seek(0)

        safe_name = cand_name.replace(" ", "_")
        download_name = f"Invoice_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"

        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=download_name,
                         mimetype="application/pdf")


# =========================================================================
# P1: VACANCY DETAIL ROUTE
# =========================================================================

@associate_bp.route("/vacancies/<int:job_id>")
@_require_login
def vacancy_detail(job_id):
    """P1: View details of a single vacancy."""
    Job = _model("Job")
    Application = _model("Application")
    Engagement = _model("Engagement")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        job = s.get(Job, job_id) if Job else None
        if not job:
            flash("Vacancy not found.", "danger")
            return redirect(url_for("associate.vacancies"))

        engagement = s.get(Engagement, job.engagement_id) if Engagement and job.engagement_id else None

        already_applied = False
        if Application:
            existing = s.query(Application).filter_by(
                candidate_id=cand_id, job_id=job_id
            ).first()
            already_applied = existing is not None

        # Check if applications are blocked (on contract)
        _Candidate = _model("Candidate")
        cand_check = s.get(_Candidate, cand_id) if _Candidate else None
        applications_blocked = bool(cand_check and getattr(cand_check, "applications_blocked", False))

        # Check if associate has a CV on file
        Document = _model("Document")
        has_cv = False
        if Document:
            cv_doc = s.query(Document).filter_by(
                candidate_id=cand_id, doc_type="cv"
            ).first()
            has_cv = cv_doc is not None

        return render_template(
            "associate/vacancy_detail.html",
            job=job,
            engagement=engagement,
            already_applied=already_applied,
            has_cv=has_cv,
            applications_blocked=applications_blocked,
        )


# =========================================================================
# P2: VACANCY APPLY ROUTE
# =========================================================================

@associate_bp.route("/vacancies/<int:job_id>/apply", methods=["POST"])
@_require_login
def vacancy_apply(job_id):
    """P2: Apply for a vacancy from the portal."""
    import secrets as _secrets
    Job = _model("Job")
    Application = _model("Application")
    Candidate = _model("Candidate")
    Document = _model("Document")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        job = s.get(Job, job_id) if Job else None
        if not job:
            flash("Vacancy not found.", "danger")
            return redirect(url_for("associate.vacancies"))

        # Block applications for associates currently on contract
        cand_check = s.get(Candidate, cand_id) if Candidate else None
        if cand_check and getattr(cand_check, "applications_blocked", False):
            flash("Please contact an Optimus representative to discuss your application.", "warning")
            return redirect(url_for("associate.vacancy_detail", job_id=job_id))

        if Application:
            existing = s.query(Application).filter_by(
                candidate_id=cand_id, job_id=job_id
            ).first()
            if existing:
                flash("You have already applied for this role.", "info")
                return redirect(url_for("associate.vacancy_detail", job_id=job_id))

        # Check if CV is on file
        cand = s.get(Candidate, cand_id) if Candidate else None
        has_cv = False
        if Document:
            cv_doc = s.query(Document).filter_by(candidate_id=cand_id, doc_type="cv").first()
            has_cv = cv_doc is not None

        # Handle CV upload if no CV on file
        cv_file = request.files.get("cv_file")
        if not has_cv and not cv_file:
            flash("Please upload your CV to apply.", "danger")
            return redirect(url_for("associate.vacancy_detail", job_id=job_id))

        if cv_file and cv_file.filename:
            # Validate file type
            allowed_ext = {'.pdf', '.doc', '.docx'}
            from werkzeug.utils import secure_filename as _secure
            original_name = cv_file.filename
            ext = os.path.splitext(original_name)[1].lower()
            if ext not in allowed_ext:
                flash("CV must be a PDF, DOC, or DOCX file.", "danger")
                return redirect(url_for("associate.vacancy_detail", job_id=job_id))

            # Save the CV to the main uploads/cvs directory
            safe_name = _secure(original_name)
            unique_name = f"{_secrets.token_hex(8)}_{safe_name}"
            upload_dir = os.path.join(current_app.root_path, "uploads", "cvs")
            os.makedirs(upload_dir, exist_ok=True)
            cv_file.save(os.path.join(upload_dir, unique_name))

            # Create Document record
            if Document:
                doc = Document(
                    candidate_id=cand_id,
                    filename=unique_name,
                    original_name=original_name,
                    doc_type="cv",
                )
                s.add(doc)

        if Application:
            app = Application(
                candidate_id=cand_id,
                job_id=job_id,
                status="Pipeline",
            )
            s.add(app)
            s.flush()
            app_id = app.id
            print(f"[PORTAL] Application flushed: candidate_id={cand_id}, job_id={job_id}, app_id={app_id}, status=Pipeline, job_engagement_id={job.engagement_id}")
            try:
                _add_note(s, cand_id, f"Applied for {job.title} via Associate Portal.")
            except Exception as e:
                print(f"[PORTAL] Note failed (non-fatal): {e}")
            s.commit()
            print(f"[PORTAL] Application committed successfully: app_id={app_id}")

            # Trigger AI summary + score in background
            try:
                from app import ai_summarise, _collect_text_for_candidate
                with SASession(engine) as s2:
                    _cand = s2.get(Candidate, cand_id)
                    _job = s2.get(Job, job_id)
                    if _cand and _job:
                        cv_text = _collect_text_for_candidate(s2, _cand)
                        if cv_text and _job.description:
                            summary = ai_summarise(cv_text, job_description=_job.description or "")
                            _app = s2.get(Application, app_id)
                            if _app and summary:
                                _app.ai_summary = summary[:6000]
                            # Score
                            from app import ai_score_with_explanation
                            score_result = ai_score_with_explanation(_job.description or "", cv_text)
                            if score_result and _app:
                                _app.ai_score = int(score_result.get("final", 0) or 0)
                                _app.ai_explanation = (score_result.get("explanation") or "")[:7999]
                            s2.commit()
                            print(f"[PORTAL] AI score={_app.ai_score if _app else '?'} for app_id={app_id}")
            except Exception as ai_exc:
                print(f"[PORTAL] AI scoring failed (non-fatal): {ai_exc}")

        flash(f"Application submitted for {job.title}.", "success")
        return redirect(url_for("associate.vacancy_detail", job_id=job_id))


# =========================================================================
# P2b: VACANCY WITHDRAW ROUTE
# =========================================================================

@associate_bp.route("/vacancies/<int:job_id>/withdraw", methods=["POST"])
@_require_login
def vacancy_withdraw(job_id):
    """Withdraw an application from the portal.

    Soft-withdraw (status = "Withdrawn") rather than DELETE — TrustIDCheck and
    ESigRequest both reference applications.id without CASCADE, so a hard delete
    raised IntegrityError and surfaced as a 500 to the candidate. Matches the
    Req 44 staff-side pattern which also uses status="Withdrawn".
    """
    Application = _model("Application")
    engine = _engine()
    cand_id = _get_associate_id()

    TERMINAL = {"withdrawn", "rejected", "hired", "placed", "contract signed", "closed"}

    with SASession(engine) as s:
        if Application:
            app = s.query(Application).filter_by(
                candidate_id=cand_id, job_id=job_id
            ).first()
            if not app:
                flash("No application found to withdraw.", "warning")
            elif (app.status or "").strip().lower() in TERMINAL:
                flash(f"Application is already {app.status}.", "info")
            else:
                try:
                    app.status = "Withdrawn"
                    _add_note(s, cand_id, f"Withdrew application for job #{job_id} via Associate Portal.")
                    s.commit()
                    flash("Application withdrawn. You may re-apply.", "success")
                except Exception:
                    s.rollback()
                    current_app.logger.exception(
                        "vacancy_withdraw failed for cand=%s job=%s", cand_id, job_id
                    )
                    flash(
                        "We couldn't withdraw the application — please try again or contact the Optimus team.",
                        "warning",
                    )

    return redirect(url_for("associate.vacancy_detail", job_id=job_id))


# =========================================================================
# P3: FORGOT PASSWORD ROUTE
# =========================================================================

@associate_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """P3: Send a password reset magic link."""
    Candidate = _model("Candidate")
    engine = _engine()

    if request.method == "GET":
        return render_template("associate/auth_forgot_password.html")

    email = _sanitise(request.form.get("email", "")).strip().lower()
    if not email:
        flash("Please enter your email address.", "danger")
        return redirect(url_for("associate.forgot_password"))

    with SASession(engine) as s:
        cand = s.query(Candidate).filter(Candidate.email.ilike(email)).first()
        if cand:
            try:
                _send_magic_link(email, cand.name or "Associate", is_signup=False)
            except Exception:
                pass  # Don't reveal whether account exists

    # Always show success to prevent email enumeration
    flash("If an account exists with that email, a password reset link has been sent.", "success")
    return render_template("associate/auth_check_email.html", email=email)


# =========================================================================
# P4: RESEND VERIFICATION ROUTE
# =========================================================================

@associate_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    """P4: Resend the email verification magic link."""
    Candidate = _model("Candidate")
    engine = _engine()

    email = _sanitise(request.form.get("email", "")).strip().lower()
    if not email:
        flash("Email address is required.", "danger")
        return redirect(url_for("associate.login"))

    with SASession(engine) as s:
        cand = s.query(Candidate).filter(Candidate.email.ilike(email)).first()
        if cand and not cand.password_hash:
            try:
                _send_magic_link(email, cand.name or "Associate", is_signup=True)
            except Exception:
                pass

    flash("If an account exists, a new verification link has been sent.", "success")
    return render_template("associate/auth_check_email.html", email=email)


# =========================================================================
# P5: EMPLOYMENT/GAP ENTRY EDIT
# =========================================================================

@associate_bp.route("/references/edit-entry/<int:entry_id>", methods=["POST"])
@_require_login
def references_edit_entry(entry_id):
    """P5: Edit an existing employment or gap entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        flash("Employment history not available.", "danger")
        return redirect(url_for("associate.references"))

    with SASession(engine) as s:
        entry = s.query(EmploymentHistory).filter_by(id=entry_id, candidate_id=cand_id).first()
        if not entry:
            flash("Entry not found.", "danger")
            return redirect(url_for("associate.references"))

        if entry.is_gap:
            entry.start_date = _parse_date(request.form.get("start_date", ""))
            entry.end_date = _parse_date(request.form.get("end_date", ""))
            entry.gap_reason = _sanitise(request.form.get("gap_reason", ""))
        else:
            entry.company_name = _sanitise(request.form.get("company_name", ""))
            entry.agency_name = _sanitise(request.form.get("agency_name", ""))
            entry.referee_email = _sanitise(request.form.get("referee_email", ""))
            entry.company_address = _sanitise(request.form.get("company_address", ""))
            entry.start_date = _parse_date(request.form.get("start_date", ""))
            entry.end_date = _parse_date(request.form.get("end_date", ""))
            entry.job_title = _sanitise(request.form.get("job_title", ""))
            entry.reason_for_leaving = _sanitise(request.form.get("reason_for_leaving", ""))
            can_contact = "1" in request.form.getlist("can_contact")
            entry.permission_to_request = can_contact
            if not can_contact:
                entry.permission_delay_reason = _sanitise(request.form.get("no_contact_reason", ""))
                entry.permission_future_date = _parse_date(request.form.get("earliest_contact_date", ""))
            else:
                entry.permission_delay_reason = ""
                entry.permission_future_date = None

        _add_note(s, cand_id, f"Employment entry updated: {entry.company_name or 'Gap'}.")
        s.commit()

    flash("Entry updated successfully.", "success")
    return redirect(url_for("associate.references_employment"))


@associate_bp.route("/api/edit-entry/<int:entry_id>", methods=["PUT", "POST"])
@_require_login
def api_edit_entry(entry_id):
    """P5: AJAX edit for employment/gap entry."""
    EmploymentHistory = _portal_model("EmploymentHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not EmploymentHistory:
        return jsonify({"error": "Not available"}), 500

    data = request.get_json(silent=True) or {}

    with SASession(engine) as s:
        entry = s.query(EmploymentHistory).filter_by(id=entry_id, candidate_id=cand_id).first()
        if not entry:
            return jsonify({"error": "Not found"}), 404

        for field in ["company_name", "agency_name", "referee_email", "company_address",
                       "job_title", "reason_for_leaving", "gap_reason"]:
            if field in data:
                setattr(entry, field, _sanitise(data[field]))
        for date_field in ["start_date", "end_date"]:
            if date_field in data:
                setattr(entry, date_field, _parse_date(data[date_field]))
        # NOTE: permission_to_request no longer set per-entry.
        # Global reference consent is on ConsentRecord.reference_consent.

        _add_note(s, cand_id, f"Employment entry edited: {entry.company_name or 'Gap'}.")
        s.commit()

    return jsonify({"success": True})


# =========================================================================
# P6: EXPENSE INPUT ON TIMESHEETS
# =========================================================================

@associate_bp.route("/timesheets/add-expense", methods=["POST"])
@_require_login
def timesheets_add_expense():
    """P6: Add an expense line item to a timesheet."""
    TimesheetExpense = _portal_model("TimesheetExpense")
    Timesheet = _model("Timesheet")
    engine = _engine()
    cand_id = _get_associate_id()

    ts_id = request.form.get("timesheet_id", type=int)
    if not ts_id or not TimesheetExpense:
        flash("Cannot add expense.", "danger")
        return redirect(url_for("associate.timesheets"))

    # Phase 2 / TS 14 — receipt size + type limits before we touch the DB.
    MAX_RECEIPT_BYTES = 5 * 1024 * 1024  # 5 MB

    with SASession(engine) as s:
        ts = s.get(Timesheet, ts_id)
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        # Phase 2 / TS 13 — Rejected unlocks editing same as Draft.
        if (ts.status or "").lower() not in ("draft", "unsubmitted", "rejected", "", None):
            flash("Expenses can only be added to draft or rejected timesheets.", "warning")
            return redirect(url_for("associate.timesheets"))

        # Phase 2 / TS 12 — category-driven VAT. The form may submit:
        #   expense_category_id (new path) — preferred when the portal UI
        #     surfaces the configured ExpenseCategory dropdown.
        #   expense_type (legacy path) — free-text fallback. Looked up by
        #     name against expense_categories; falls back to Standard 20%
        #     if no match (auto-create on Admin's side is via /admin/expense-categories).
        category = None
        category_id_raw = request.form.get("expense_category_id")
        try:
            cat_id = int(category_id_raw) if category_id_raw else None
        except ValueError:
            cat_id = None
        if cat_id is not None:
            try:
                category = s.execute(text(
                    "SELECT id, name, vat_treatment, is_mileage FROM expense_categories WHERE id = :id"
                ).bindparams(id=cat_id)).first()
            except Exception:
                category = None
        expense_type = _sanitise(request.form.get("expense_type", "Other"))
        if not category:
            try:
                category = s.execute(text(
                    "SELECT id, name, vat_treatment, is_mileage FROM expense_categories WHERE LOWER(name) = LOWER(:n)"
                ).bindparams(n=expense_type)).first()
            except Exception:
                category = None
        # vat_treatment → vat_rate_pct
        vat_pct = 0.0
        is_mileage_cat = False
        if category:
            treatment = (category[2] or "standard")
            vat_pct = {"standard": 20.0, "reduced": 5.0, "zero": 0.0, "non_vatable": 0.0}.get(treatment, 20.0)
            is_mileage_cat = bool(category[3])
            expense_type = category[1]  # use the canonical name from the category
        else:
            # No category match — default Standard 20% so legacy free-text
            # entries still capture some VAT data.
            vat_pct = 20.0

        description = _sanitise(request.form.get("expense_description", ""))
        date_of_expense_raw = (request.form.get("expense_date") or "").strip()
        date_of_expense = _parse_date(date_of_expense_raw) if date_of_expense_raw else None
        distance_miles_raw = (request.form.get("expense_distance") or "").strip()
        distance_miles = None

        # Phase 2 / TS 25 — Mileage: amount = miles × HMRC rate. Distance
        # input drives the £, which is read-only on the form.
        try:
            from app import _company_settings  # late import to avoid circular at module load
            company_cfg = _company_settings()
        except Exception:
            company_cfg = {"hmrc_mileage_rate": 0.45}
        hmrc_rate = float(company_cfg.get("hmrc_mileage_rate", 0.45))

        if is_mileage_cat:
            try:
                distance_miles = float(distance_miles_raw) if distance_miles_raw else 0
            except ValueError:
                distance_miles = 0
            if distance_miles <= 0:
                flash("Please enter a positive distance in miles for the mileage expense.", "danger")
                return redirect(url_for("associate.timesheets"))
            amount = round(distance_miles * hmrc_rate, 2)
        else:
            try:
                amount = float(request.form.get("expense_amount", 0))
            except (ValueError, TypeError):
                amount = 0
            if amount <= 0:
                flash("Expense amount must be greater than zero.", "danger")
                return redirect(url_for("associate.timesheets"))

        vat_amount = round(amount * (vat_pct / 100.0), 2)

        # Handle receipt upload — Phase 2 / TS 14 size + MIME checks.
        receipt_doc_id = None
        receipt_file = request.files.get("expense_receipt")
        if receipt_file and receipt_file.filename:
            ext = os.path.splitext(receipt_file.filename)[1].lower().lstrip(".")
            if ext not in {"pdf", "jpg", "jpeg", "png"}:
                flash("Receipt must be a PDF, JPG, JPEG or PNG file.", "danger")
                return redirect(url_for("associate.timesheets"))
            # Size check — read once for size, then seek back.
            try:
                receipt_file.stream.seek(0, 2)  # to end
                size = receipt_file.stream.tell()
                receipt_file.stream.seek(0)
            except Exception:
                size = 0
            if size and size > MAX_RECEIPT_BYTES:
                flash(f"Receipt is too large ({size // 1024} KB). Maximum is 5 MB.", "danger")
                return redirect(url_for("associate.timesheets"))
            saved = _save_file(receipt_file)
            if saved:
                Document = _model("Document")
                if Document:
                    doc = Document(
                        candidate_id=cand_id,
                        doc_type="expense_receipt",
                        filename=saved["filename"],
                        original_name=saved["original_name"],
                    )
                    s.add(doc)
                    s.flush()
                    receipt_doc_id = doc.id

        expense = TimesheetExpense(
            timesheet_id=ts_id,
            expense_type=expense_type,
            description=description,
            amount=amount,
            receipt_doc_id=receipt_doc_id,
            expense_category_id=(category[0] if category else None),
            vat_rate_pct=vat_pct,
            vat_amount=vat_amount,
            date_of_expense=date_of_expense,
            distance_miles=distance_miles,
        )
        s.add(expense)

        # Update timesheet totals
        ts.expense_total = (ts.expense_total or 0) + amount
        ts.grand_total = (ts.total_amount or 0) + ts.expense_total

        _add_note(s, cand_id, f"Expense added: {expense_type} - GBP{amount:.2f}.")
        s.commit()

    flash(f"Expense added: {expense_type} - GBP{amount:.2f}.", "success")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/edit-expense/<int:expense_id>", methods=["POST"])
@_require_login
def timesheets_edit_expense(expense_id):
    """Phase 2 / TS 13 — in-place edit of an expense line on a Draft or
    Rejected timesheet. Recomputes VAT from the category and (for
    mileage) the £-amount from the new distance × HMRC rate."""
    TimesheetExpense = _portal_model("TimesheetExpense")
    Timesheet = _model("Timesheet")
    engine = _engine()
    cand_id = _get_associate_id()
    with SASession(engine) as s:
        expense = s.get(TimesheetExpense, expense_id) if TimesheetExpense else None
        if not expense:
            flash("Expense not found.", "danger")
            return redirect(url_for("associate.timesheets"))
        ts = s.get(Timesheet, expense.timesheet_id)
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))
        if (ts.status or "").lower() not in ("draft", "unsubmitted", "rejected", "", None):
            flash("Expenses can only be edited on draft or rejected timesheets.", "warning")
            return redirect(url_for("associate.timesheets"))

        # Pull current HMRC rate.
        try:
            from app import _company_settings
            cfg = _company_settings()
        except Exception:
            cfg = {"hmrc_mileage_rate": 0.45}
        hmrc_rate = float(cfg.get("hmrc_mileage_rate", 0.45))

        # Allow category swap.
        category_id_raw = request.form.get("expense_category_id")
        cat_id = None
        try:
            cat_id = int(category_id_raw) if category_id_raw else None
        except ValueError:
            cat_id = None
        category = None
        if cat_id is not None:
            try:
                category = s.execute(text(
                    "SELECT id, name, vat_treatment, is_mileage FROM expense_categories WHERE id = :id"
                ).bindparams(id=cat_id)).first()
            except Exception:
                category = None
        if category:
            expense.expense_category_id = category[0]
            expense.expense_type = category[1]
            treatment = category[2] or "standard"
            expense.vat_rate_pct = {"standard": 20.0, "reduced": 5.0, "zero": 0.0, "non_vatable": 0.0}.get(treatment, 20.0)
            is_mileage = bool(category[3])
        else:
            is_mileage = bool(expense.distance_miles)

        # Date + description
        date_raw = (request.form.get("expense_date") or "").strip()
        if date_raw:
            expense.date_of_expense = _parse_date(date_raw)
        desc = request.form.get("expense_description")
        if desc is not None:
            expense.description = _sanitise(desc)

        if is_mileage:
            dist_raw = (request.form.get("expense_distance") or "").strip()
            try:
                miles = float(dist_raw) if dist_raw else 0
            except ValueError:
                miles = 0
            if miles > 0:
                expense.distance_miles = miles
                expense.amount = round(miles * hmrc_rate, 2)
        else:
            amt_raw = (request.form.get("expense_amount") or "").strip()
            try:
                amt = float(amt_raw) if amt_raw else expense.amount
            except ValueError:
                amt = expense.amount
            if amt > 0:
                expense.amount = amt

        expense.vat_amount = round((expense.amount or 0) * ((expense.vat_rate_pct or 0) / 100.0), 2)
        # Refresh totals on the parent timesheet.
        all_rows = s.query(TimesheetExpense).filter_by(timesheet_id=ts.id).all()
        ts.expense_total = sum(float(r.amount or 0) for r in all_rows)
        ts.grand_total = (ts.total_amount or 0) + ts.expense_total
        s.commit()
    flash("Expense updated.", "success")
    return redirect(url_for("associate.timesheets"))


@associate_bp.route("/timesheets/delete-expense/<int:expense_id>", methods=["POST"])
@_require_login
def timesheets_delete_expense(expense_id):
    """P6: Delete an expense from a timesheet."""
    TimesheetExpense = _portal_model("TimesheetExpense")
    Timesheet = _model("Timesheet")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        expense = s.get(TimesheetExpense, expense_id) if TimesheetExpense else None
        if not expense:
            flash("Expense not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        ts = s.get(Timesheet, expense.timesheet_id)
        if not ts or ts.user_id != cand_id:
            flash("Timesheet not found.", "danger")
            return redirect(url_for("associate.timesheets"))

        # TS 5/27 — Rejected timesheets unlock for editing, same as Draft.
        if (ts.status or "").lower() not in ("draft", "unsubmitted", "rejected", ""):
            flash("Cannot delete expenses from submitted timesheets.", "warning")
            return redirect(url_for("associate.timesheets"))

        ts.expense_total = max(0, (ts.expense_total or 0) - expense.amount)
        ts.grand_total = (ts.total_amount or 0) + ts.expense_total
        s.delete(expense)
        s.commit()

    flash("Expense removed.", "success")
    return redirect(url_for("associate.timesheets"))


# =========================================================================
# P7: 5-YEAR ADDRESS HISTORY
# =========================================================================

@associate_bp.route("/address-history", methods=["GET", "POST"])
@_require_login
def address_history():
    """P7: 5-year address history for DBS/Credit checks."""
    AddressHistory = _portal_model("AddressHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    if not AddressHistory:
        flash("Address history not available.", "warning")
        return redirect(url_for("associate.dashboard"))

    if request.method == "GET":
        with SASession(engine) as s:
            addresses = s.query(AddressHistory).filter_by(
                candidate_id=cand_id
            ).order_by(AddressHistory.to_date.desc().nullslast()).all()

            # Calculate coverage
            total_months = 0
            for addr in addresses:
                if addr.from_date and addr.to_date:
                    delta = (addr.to_date.year - addr.from_date.year) * 12 + (addr.to_date.month - addr.from_date.month)
                    total_months += max(0, delta)
                elif addr.from_date and addr.is_current:
                    delta = (date.today().year - addr.from_date.year) * 12 + (date.today().month - addr.from_date.month)
                    total_months += max(0, delta)

            required_months = 60  # 5 years
            coverage_pct = min(100, int(total_months / required_months * 100)) if required_months > 0 else 0

            return render_template(
                "associate/address_history.html",
                addresses=addresses,
                coverage_pct=coverage_pct,
                total_months=total_months,
                required_months=required_months,
            )

    # POST: Add a new address
    with SASession(engine) as s:
        is_current = request.form.get("is_current") == "yes"
        addr = AddressHistory(
            candidate_id=cand_id,
            address_line1=_sanitise(request.form.get("address_line1", "")),
            address_line2=_sanitise(request.form.get("address_line2", "")),
            city=_sanitise(request.form.get("city", "")),
            postcode=_sanitise(request.form.get("postcode", "")),
            country=_sanitise(request.form.get("country", "United Kingdom")),
            from_date=_parse_date(request.form.get("from_date", "")),
            to_date=date.today() if is_current else _parse_date(request.form.get("to_date", "")),
            is_current=is_current,
        )
        s.add(addr)

        # If marking as current, unset other current addresses
        if is_current:
            others = s.query(AddressHistory).filter(
                AddressHistory.candidate_id == cand_id,
                AddressHistory.id != addr.id,
                AddressHistory.is_current == True  # noqa: E712
            ).all()
            for o in others:
                o.is_current = False

        _add_note(s, cand_id, f"Address added: {addr.address_line1}, {addr.city} {addr.postcode}.")
        s.commit()

    flash("Address added successfully.", "success")
    return redirect(url_for("associate.address_history"))


@associate_bp.route("/address-history/<int:addr_id>/delete", methods=["POST"])
@_require_login
def address_history_delete(addr_id):
    """P7: Delete an address history entry."""
    AddressHistory = _portal_model("AddressHistory")
    engine = _engine()
    cand_id = _get_associate_id()

    with SASession(engine) as s:
        addr = s.query(AddressHistory).filter_by(id=addr_id, candidate_id=cand_id).first() if AddressHistory else None
        if addr:
            s.delete(addr)
            s.commit()

    flash("Address removed.", "success")
    return redirect(url_for("associate.address_history"))


# =========================================================================
# P10: EMPLOYMENT COMPLETENESS ENFORCEMENT
# =========================================================================

def _check_employment_complete(session_obj, cand_id):
    """
    P10: Check if employment history covers the required period with no unexplained gaps.
    Returns (complete: bool, message: str).
    """
    EmploymentHistory = _portal_model("EmploymentHistory")
    if not EmploymentHistory:
        return True, ""

    entries = session_obj.query(EmploymentHistory).filter_by(candidate_id=cand_id).all()
    if not entries:
        return False, "No employment history entries. Please add your employment history before proceeding."

    # Check for unexplained gaps > 90 days
    employment_entries = [e for e in entries if not e.is_gap]
    gap_entries = [e for e in entries if e.is_gap]

    if not employment_entries:
        return False, "Please add at least one employment entry."

    # Sort by start_date
    sorted_entries = sorted(
        [e for e in entries if e.start_date],
        key=lambda e: e.start_date
    )

    for i in range(len(sorted_entries) - 1):
        current_end = sorted_entries[i].end_date
        next_start = sorted_entries[i + 1].start_date
        if current_end and next_start:
            gap_days = (next_start - current_end).days
            if gap_days > 90:
                # Check if gap is explained
                gap_covered = any(
                    g.is_gap and g.start_date and g.end_date
                    and g.start_date <= current_end + timedelta(days=7)
                    and g.end_date >= next_start - timedelta(days=7)
                    for g in gap_entries
                )
                if not gap_covered:
                    return False, f"Unexplained gap of {gap_days} days between entries. Please add a gap explanation."

    # Coverage requirement — the timeline must reach back a full 5 years.
    # Soft warning only (caller does not hard-block on this).
    if sorted_entries:
        earliest_start = sorted_entries[0].start_date
        five_years_ago = (datetime.utcnow() - timedelta(days=5*365)).date()
        if earliest_start and earliest_start > five_years_ago:
            years_covered = (datetime.utcnow().date() - earliest_start).days / 365.0
            return False, (
                f"Your employment history currently covers about {years_covered:.1f} years. "
                "A full 5 years is required — please add an earlier employment or gap entry."
            )

    return True, ""
