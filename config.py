"""Environment-driven configuration. Mirrors the capital/expense apps:
cloud (Firestore + GCS) when the env vars are set, SQLite + ./storage locally.
"""
import os
from pathlib import Path

import shared_settings

BASE_DIR = Path(__file__).resolve().parent

# Load .env if present (same process as the capital/expense apps).
# python-dotenv is optional; the app still runs without a .env.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# --- Auth / app ---------------------------------------------------------------
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# K_SERVICE is set by Cloud Run and by nothing else — a reliable "am I in
# production" signal.
ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))


def _require_flask_secret():
    """Session signing key. Never fall back to a literal.

    A hardcoded default is a publicly known key: anyone reading this file can
    forge a `reviewer` session cookie, and every signed review / revise /
    addendum link derives from the same secret. In production we refuse to
    start without one; locally we mint a random key per process, so there is
    never a known value to forge (the cost is that dev sessions and dev links
    don't survive a restart).
    """
    secret = os.environ.get("FLASK_SECRET", "").strip()
    if secret:
        return secret
    if ON_CLOUD_RUN:
        raise RuntimeError(
            "FLASK_SECRET is not set. Refusing to start: without it the session "
            "cookie and every signed review link would be signed with a "
            "guessable key. Set it on the Cloud Run service "
            "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
        )
    import secrets as _secrets
    print("[config] FLASK_SECRET unset — using a random per-process key "
          "(local dev only; sessions and signed links reset on restart).")
    return _secrets.token_urlsafe(48)


FLASK_SECRET = _require_flask_secret()
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")

if ON_CLOUD_RUN and not ADMIN_TOKEN:
    raise RuntimeError(
        "ADMIN_TOKEN is not set. Refusing to start: reviewer routes would be "
        "unreachable and the service would be running without its only "
        "authentication control."
    )

# --- AI extraction (provider-switchable) ----------------------------------------
# AI_PROVIDER: "anthropic" (Claude Sonnet) or "gemini" (Google Gemini).
# AI_MODEL overrides the per-provider default below.
AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic")
AI_MODEL = os.environ.get("AI_MODEL", "")
DEFAULT_MODELS = {"anthropic": "claude-sonnet-4-5", "gemini": "gemini-2.5-flash"}
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))

# --- Mail (same process as the capital/expense apps) -----------------------------
def _bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

APPROVER_EMAIL = os.environ.get("APPROVER_EMAIL", "")
APPROVER_NAME = os.environ.get("APPROVER_NAME", "Approver")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME", "")   # defaults to EMAIL_SENDER
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.office365.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USE_SSL = _bool("SMTP_USE_SSL", SMTP_PORT == 465)
SMTP_STARTTLS = _bool("SMTP_STARTTLS", not SMTP_USE_SSL)
MAIL_CC = [a.strip() for a in os.environ.get("MAIL_CC", "").split(",") if a.strip()]

# --- Carrier destination (auto-send OFF by default; reviewer sends manually) ----
AUTO_SEND_TO_CARRIER = os.environ.get("AUTO_SEND_TO_CARRIER", "false").lower() == "true"
CARRIER_EMAIL = os.environ.get("CARRIER_EMAIL", "nol@choosebroadspire.com")

# --- Live values: the portal's Settings page overrides the env defaults above ----
def approver_email():
    return shared_settings.get("incident", "approver_email", APPROVER_EMAIL)


def approver_name():
    return shared_settings.get("incident", "approver_name", APPROVER_NAME)


def mail_cc():
    return shared_settings.get_list("incident", "cc", MAIL_CC)


def carrier_email():
    return shared_settings.get("incident", "carrier_email", CARRIER_EMAIL)


# --- Storage: cloud when configured, local otherwise ----------------------------
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")            # e.g. incidents-capital-processor
FIRESTORE_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "")  # e.g. incident_reports
USE_CLOUD = bool(GCS_BUCKET and FIRESTORE_COLLECTION)

LOCAL_STORAGE = Path(os.environ.get("LOCAL_STORAGE", BASE_DIR / "storage"))
SQLITE_PATH = Path(os.environ.get("SQLITE_PATH", LOCAL_STORAGE / "incidents.db"))

# --- Form / rules assets ---------------------------------------------------------
BLANK_FORM_XLSX = Path(os.environ.get(
    "BLANK_FORM_XLSX", BASE_DIR / "assets" / "general_liability_reporting_form_Starr.xlsx"))
LANGUAGE_RULES_PATH = Path(os.environ.get(
    "LANGUAGE_RULES_PATH", BASE_DIR / "language_rules.json"))
HOTELS_PATH = Path(os.environ.get("HOTELS_PATH", BASE_DIR / "hotels.json"))

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
ALLOWED_UPLOAD_EXT = {".pdf", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
# Supporting attachments (photos of the incident, CCTV clips, receipts, docs)
ALLOWED_ATTACHMENT_EXT = ALLOWED_UPLOAD_EXT | {".docx", ".doc", ".txt", ".mp4", ".mov", ".csv"}
