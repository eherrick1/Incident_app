"""Incident Reporting Portal — Flask app.

Public (no login): choose intake path, online GL form, Excel/PDF/photo upload,
confirm parsed record, revise after "request changes".
Reviewer (ADMIN_TOKEN + signed links): dashboard, review screen with
completeness + language panels, approve / request changes / reject, downloads.
"""
import functools
import hmac
import io
import re
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config
import shared_settings
import fields as F
import intake
import language_guard as lg
import mailer
import outputs
import storage

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
# SameSite=Lax still sends the cookie on the top-level GET from an e-mailed
# review link, but not on a cross-site POST — which blocks the drive-by
# approve/reject case until real CSRF tokens land.
app.config.update(SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=config.ON_CLOUD_RUN,
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12))


# --------------------------------------------------------------------------- #
# CSRF: same-origin check on unsafe methods
#
# Reviewer actions are authorised by a session cookie, so a logged-in reviewer
# who lands on a hostile page could otherwise be made to approve, reject, or
# file a claim silently. A token scheme would be stronger, but the public
# submit paths are posted by people with no session at all, and half the
# surface here is e-mailed links — so the check that fits is origin
# verification, which needs no per-form state and no new dependency.
#
# Browsers send Origin on every cross-site POST and (since ~2020) on same-site
# POSTs too. A request whose Origin/Referer names another site is refused. A
# request with neither header is allowed: that is a non-browser client (curl,
# a health check), which is not a CSRF vector — CSRF requires a browser
# carrying the victim's cookie.
# --------------------------------------------------------------------------- #
@app.after_request
def _security_headers(resp):
    """Baseline response headers.

    The CSP is deliberately strict — this app serves no third-party scripts and
    no inline JS except the form's own block, which is why 'unsafe-inline' is
    still needed for script-src. frame-ancestors 'none' blocks clickjacking of
    the approve/reject buttons; nosniff stops a text attachment being reinterpreted
    as HTML in the app's own origin.
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    # SAMEORIGIN / frame-ancestors 'self', not DENY / 'none': the review screen
    # shows the reporter's original upload in an iframe from this same origin.
    # DENY blocks framing by anything at all, own origin included, which blanks
    # that preview. SAMEORIGIN still stops another site framing the review
    # screen and its approve / reject buttons.
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "frame-ancestors 'self'; form-action 'self'; base-uri 'none'; "
        "object-src 'none'")
    if config.ON_CLOUD_RUN:
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
    return resp


# --------------------------------------------------------------------------- #
# Login throttle (F12)
#
# In-process and per-IP: good enough for a single-instance service and it adds
# no dependency. If max-instances ever goes above 1 this becomes per-instance
# rather than global — at that point move the counter to Firestore or Redis.
# --------------------------------------------------------------------------- #
_LOGIN_FAILURES = {}          # ip -> [count, first_failure_ts]
_LOGIN_MAX_TRIES = 8
_LOGIN_WINDOW = 900           # 15 minutes


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


def _login_blocked():
    entry = _LOGIN_FAILURES.get(_client_ip())
    if not entry:
        return False
    count, first = entry
    if time.time() - first > _LOGIN_WINDOW:
        _LOGIN_FAILURES.pop(_client_ip(), None)
        return False
    return count >= _LOGIN_MAX_TRIES


def _note_login_failure():
    ip = _client_ip()
    count, first = _LOGIN_FAILURES.get(ip, (0, time.time()))
    if time.time() - first > _LOGIN_WINDOW:
        count, first = 0, time.time()
    _LOGIN_FAILURES[ip] = (count + 1, first)


def _note_login_success():
    _LOGIN_FAILURES.pop(_client_ip(), None)


def _safe_next(target):
    """Only same-site relative paths. '//evil.test' is protocol-relative and
    would leave the site, so a leading '//' is rejected along with absolutes."""
    return bool(target) and target.startswith("/") and not target.startswith("//")


@app.before_request
def _consume_token_query_param():
    """Accept ?token= once, then bounce to a clean URL.

    The portal still hands out ?token= links (see the portal's POST hand-off
    for the real fix). Trading the token for a session cookie and immediately
    redirecting keeps it out of the address bar, browser history, bookmarks,
    and the Referer header of every subsequent request. It is still visible in
    the access log for this one request — which is why the portal should POST.
    """
    if request.method != "GET" or "token" not in request.args or not request.endpoint:
        return None
    if not _token_login():
        return None
    clean = {k: v for k, v in request.args.items() if k != "token"}
    return redirect(url_for(request.endpoint, **(request.view_args or {}), **clean))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Reviewer sign-in. The token travels in a POST body, not a URL."""
    nxt = request.values.get("next", "")
    if request.method == "POST":
        if _login_blocked():
            return render_template(
                "login.html", next=nxt,
                error=("Too many failed attempts from this address. "
                       f"Try again in {_LOGIN_WINDOW // 60} minutes.")), 429
        if _token_login():
            return redirect(nxt if _safe_next(nxt) else url_for("admin"))
        time.sleep(1)                     # slow down brute force
        return render_template("login.html", error="That token was not accepted.",
                               next=nxt), 401
    if _full_reviewer():
        return redirect(nxt if _safe_next(nxt) else url_for("admin"))
    return render_template("login.html", error=None, next=nxt)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.errorhandler(403)
def _forbidden(_e):
    return render_template("forbidden.html", next=request.path), 403


# Sign-in legitimately arrives cross-origin: the portal POSTs the token here so
# it never has to appear in a URL. There is nothing to forge — the POST carries
# the credential itself, and a request without it just fails.
_CSRF_EXEMPT_ENDPOINTS = {"login"}


@app.before_request
def _reject_cross_origin_writes():
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None
    if request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
        return None
    origin = request.headers.get("Origin")
    if origin is None:
        referer = request.headers.get("Referer")
        if not referer:
            return None                      # no browser context at all
        origin = referer
    if urlparse(origin).netloc != urlparse(request.base_url).netloc:
        app.logger.warning("[csrf] refused %s %s from origin %r",
                           request.method, request.path, origin)
        abort(403, "Cross-site form submission refused.")
    return None
app.jinja_env.globals.update(SECTION_NOTES=F.SECTION_NOTES, HOTEL_SECTION=F.HOTEL_SECTION,
                             OPTIONAL_SECTIONS=F.OPTIONAL_SECTIONS)


@app.context_processor
def _live_settings():
    # Per request, so a change on the portal's Settings page shows up here.
    return {"approver_name": config.approver_name()}
app.jinja_env.filters["iso_date"] = F.to_iso_date
app.jinja_env.filters["iso_time"] = F.to_iso_time

# Timed signers: an e-mailed link is a credential, and one that never expires
# is a standing credential sitting in a mailbox forever. Each purpose gets the
# shortest life its workflow actually needs.
_signer = URLSafeTimedSerializer(config.FLASK_SECRET, salt="review-link")
_revise_signer = URLSafeTimedSerializer(config.FLASK_SECRET, salt="revise-link")
_addendum_signer = URLSafeTimedSerializer(config.FLASK_SECRET, salt="addendum-link")

_DAY = 86400
LINK_MAX_AGE = {
    "review": 30 * _DAY,     # reviewer works the queue within a month
    "revise": 30 * _DAY,     # reporter has a month to answer a change request
    "addendum": 365 * _DAY,  # doctor's paperwork can take many months to arrive
}


def _load_signed(signer, sig, incident_id, purpose):
    """Verify a signed link. Returns 'ok' | 'expired' | 'bad'."""
    try:
        if signer.loads(sig, max_age=LINK_MAX_AGE[purpose]) == incident_id:
            return "ok"
        return "bad"
    except SignatureExpired:
        return "expired"
    except BadSignature:
        return "bad"


def _link_expired(purpose):
    """A dead end that explains itself, rather than a bare 403."""
    return render_template("link_expired.html", purpose=purpose,
                           days=LINK_MAX_AGE[purpose] // _DAY,
                           approver=config.approver_name(),
                           approver_email=config.approver_email()), 410


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _full_reviewer():
    """A reviewer who authenticated with ADMIN_TOKEN — trusted across all
    incidents. Only a token login sets this."""
    return bool(session.get("reviewer"))


def _token_login():
    """Accept ADMIN_TOKEN from the query string or a form post."""
    token = request.args.get("token") or request.form.get("token")
    if not token:
        return False
    if _login_blocked():
        return False
    if config.ADMIN_TOKEN and hmac.compare_digest(token, config.ADMIN_TOKEN):
        _note_login_success()
        session["reviewer"] = True
        return True
    _note_login_failure()
    return False


def _reviewer_ok(incident_id=None):
    """Full reviewer, or a signed-link reviewer for THIS incident only.

    A review link is signed for one incident, so it grants access to that one
    incident — not a blanket reviewer session. Before this, one forwarded
    review e-mail handed the recipient every report in the system plus the
    dashboard, the register, and the approve / send-to-carrier actions.
    """
    if _full_reviewer():
        return True
    if _token_login():
        return True
    sig = request.args.get("sig")
    if sig and incident_id:
        if _load_signed(_signer, sig, incident_id, "review") == "ok":
            scope = set(session.get("review_scope", []))
            scope.add(incident_id)
            session["review_scope"] = sorted(scope)
            return True
    # Already opened this incident from its signed link earlier this session.
    return bool(incident_id) and incident_id in session.get("review_scope", [])


def reviewer_required(view):
    """Per-incident reviewer access. Routes using this MUST take an
    `incident_id` — a link-scoped session is only ever valid for its own
    incident."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not _reviewer_ok(kwargs.get("incident_id")):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def full_reviewer_required(view):
    """Cross-incident surfaces: the dashboard and the register. These list or
    export every report, so a link scoped to one incident must not reach them."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not (_full_reviewer() or _token_login()):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def review_link(incident_id):
    return f"{config.BASE_URL}/review/{incident_id}?sig={_signer.dumps(incident_id)}"


def revise_link(incident_id):
    return f"{config.BASE_URL}/revise/{incident_id}?sig={_revise_signer.dumps(incident_id)}"


def addendum_link(incident_id):
    return f"{config.BASE_URL}/addendum/{incident_id}?sig={_addendum_signer.dumps(incident_id)}"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _fields_from_form(form):
    vals = {}
    for f in F.FIELDS:
        v = (form.get(f["key"]) or "").strip()
        if v:
            vals[f["key"]] = v
    return vals


def _drop_inapplicable(doc):
    """Drop answers the chosen loss type rules out — Section H owner details on
    an injury-only report, injured-person details on a fender-bender, and so on.

    The browser hides those fields, but a stale value can still be posted (the
    reporter filled them in, then changed the loss type). Clearing them here is
    what stops the reviewer having to blank them by hand. Audited, never silent."""
    # Answers the form fills in for itself: witness yes/no from a typed name,
    # policy number and hotel name from the hotel list.
    doc["fields"] = F.derive(doc["fields"], _hotels())
    cleaned, dropped = F.strip_inapplicable(doc["fields"])
    if dropped:
        doc["fields"] = cleaned
        labels = ", ".join(F.FIELDS_BY_KEY[k]["label"] for k in dropped)
        storage.audit(doc, "inapplicable_cleared", actor="system",
                      detail=f"{len(dropped)} answer(s) do not apply to a "
                             f"'{cleaned.get('type_of_loss', '?')}' loss: {labels}",
                      extra=dict(keys=dropped))
    # Native date/time pickers submit ISO; the carrier form wants MM/DD/YYYY
    # and a 12-hour clock. Convert before anything is stored or exported.
    cleaned, redated = F.normalize_dates(doc["fields"])
    if redated:
        doc["fields"] = cleaned
    cleaned, placeholders = F.clear_placeholders(doc["fields"])
    if placeholders:
        doc["fields"] = cleaned
        labels = ", ".join(F.FIELDS_BY_KEY[k]["label"] for k in placeholders)
        storage.audit(doc, "placeholders_cleared", actor="system",
                      detail=f"placeholder text blanked in {len(placeholders)} "
                             f"optional field(s): {labels}",
                      extra=dict(keys=placeholders))
    return dropped + placeholders


def _save_attachments(doc, actor="reporter"):
    """Store optional supporting files (photos of the incident, documents)
    posted in the 'attachments' input. Kept verbatim with the record — never
    AI-read, never merged into the form PDF. Returns count saved."""
    fs = storage.get_files()
    saved = 0
    for f in request.files.getlist("attachments"):
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in config.ALLOWED_ATTACHMENT_EXT:
            flash(f"Attachment '{f.filename}' skipped — file type not accepted.")
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(f.filename).name)
        n = len([k for k in doc["files"] if k.startswith("attachment_")])
        name = f"attachment_{n}_{safe}"
        doc["files"][name] = fs.put(doc["id"], name, f.read())
        saved += 1
    if saved:
        storage.audit(doc, "attachments_added", actor=actor, detail=f"{saved} file(s)")
    return saved


def _attachment_names(doc):
    return sorted(k for k in doc["files"] if k.startswith("attachment_"))


def _finalize_submission(doc, actor="reporter"):
    """Language scan + persist + notify reviewer. Called on every (re)submit."""
    _drop_inapplicable(doc)
    doc["original_narratives"] = {k: doc["fields"].get(k, "") for k in F.NARRATIVE_FIELDS}
    doc["language_findings"] = lg.scan_record(doc["fields"])
    doc["status"] = "pending_review"
    storage.audit(doc, "submitted_for_review", actor=actor,
                  detail=f"{len(doc['language_findings'])} language finding(s)")
    storage.get_store().save(doc)
    mailer.send_approval_request(doc, review_link(doc["id"]))
    if not doc.get("gm_notified"):
        hotel = _hotel_for(doc)
        if hotel and hotel.get("gm_email"):
            if mailer.send_gm_notice(doc, hotel.get("gm_name"), hotel["gm_email"],
                                     addendum_link(doc["id"])):
                doc["gm_notified"] = hotel["gm_email"]
                storage.audit(doc, "gm_notified", detail=hotel["gm_email"])
                storage.get_store().save(doc)


def _get_or_404(incident_id):
    doc = storage.get_store().get(incident_id)
    if doc is None:
        abort(404)
    return doc


# --------------------------------------------------------------------------- #
# Public: intake
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- Portal Settings page: server-to-server, authenticated by ADMIN_TOKEN ----
def _settings_caller_ok():
    supplied = request.headers.get("X-Admin-Token", "")
    return bool(config.ADMIN_TOKEN and supplied
                and hmac.compare_digest(supplied, config.ADMIN_TOKEN))


@app.get("/settings/effective")
def settings_effective():
    """What this tool is using right now, so the Settings page can show it."""
    if not _settings_caller_ok():
        return {"error": "not authorized"}, 403
    shared_settings.load(force=True)
    return {"approver_name": config.approver_name(),
            "approver_email": config.approver_email(),
            "cc": config.mail_cc(),
            "carrier_email": config.carrier_email(),
            "sending": shared_settings.sender_label()}


@app.post("/settings/test-email")
def settings_test_email():
    if not _settings_caller_ok():
        return {"error": "not authorized"}, 403
    to = (request.get_json(silent=True) or {}).get("to", "")
    if not mailer.valid_address(to):
        return {"ok": False, "error": "invalid address"}, 400
    shared_settings.load(force=True)
    ok, via, err = mailer.send_test(to)
    return {"ok": ok, "via": via, "error": err}, (200 if ok else 502)


@app.get("/blank-form.xlsx")
def blank_form():
    return send_file(config.BLANK_FORM_XLSX, as_attachment=True,
                     download_name="general_liability_reporting_form_Starr.xlsx")


@app.route("/form", methods=["GET", "POST"])
def online_form():
    hints = lg.load_rules().get("form_hints", [])
    if request.method == "POST":
        vals = _fields_from_form(request.form)
        doc = storage.new_record("online_form", fields=vals,
                                 submitter={"name": vals.get("reported_by_name"),
                                            "email": vals.get("reported_by_email")})
        _save_attachments(doc)
        _finalize_submission(doc)
        return redirect(url_for("submitted", incident_id=doc["id"]))
    return render_template("form.html", mode="new", fields=F.FIELDS, sections=F.SECTIONS,
                           values={}, hints=hints, action=url_for("online_form"),
                           conditions_json=_conditions_for_js(), hotels=_hotels())


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        uploads = [(f.filename, f.read()) for f in request.files.getlist("files") if f and f.filename]
        try:
            method, files_bytes, extracted = intake.process_upload(uploads)
        except intake.IntakeError as e:
            flash(str(e))
            return redirect(url_for("upload"))
        doc = storage.new_record(method, fields=extracted)
        doc["status"] = "draft"  # reporter must confirm the parsed record
        # Remember which values a model produced. Text inside an uploaded
        # document can steer the extractor (N10), and a silently altered
        # narrative should be visible as machine-read rather than typed.
        doc["ai_extracted_keys"] = sorted(extracted)
        fs = storage.get_files()
        for name, data in files_bytes.items():
            doc["files"][name] = fs.put(doc["id"], name, data)
        storage.audit(doc, "extracted", detail=f"{len(extracted)} field(s) auto-read")
        _save_attachments(doc)
        storage.get_store().save(doc)
        return redirect(url_for("confirm", incident_id=doc["id"]))
    return render_template("upload.html")


@app.route("/confirm/<incident_id>", methods=["GET", "POST"])
def confirm(incident_id):
    doc = _get_or_404(incident_id)
    if doc["status"] != "draft":
        return redirect(url_for("submitted", incident_id=incident_id))
    hints = lg.load_rules().get("form_hints", [])
    if request.method == "POST":
        submitted = _fields_from_form(request.form)
        # Anything the reporter changed on the confirm screen is their own
        # words again, so it stops counting as machine-read.
        doc["ai_extracted_keys"] = sorted(
            k for k in doc.get("ai_extracted_keys", [])
            if submitted.get(k) == doc["fields"].get(k))
        doc["fields"] = submitted
        storage.audit(doc, "confirmed", actor="reporter",
                      detail="reporter confirmed/corrected the parsed record")
        _save_attachments(doc)
        _finalize_submission(doc)
        return redirect(url_for("submitted", incident_id=doc["id"]))
    return render_template("form.html", mode="confirm", fields=F.FIELDS, sections=F.SECTIONS,
                           values=F.derive(doc["fields"], _hotels()), hints=hints,
                           attachments=_attachment_names(doc),
                           action=url_for("confirm", incident_id=incident_id),
                           conditions_json=_conditions_for_js(), hotels=_hotels())


@app.get("/submitted/<incident_id>")
def submitted(incident_id):
    """Public confirmation page. Deliberately shows the ID and status only.

    It used to render the signed add-documents link, which made the incident ID
    a working write credential — and the ID is exactly what staff are told to
    give the guest as a reference number. The link now reaches the GM by e-mail
    only (see _finalize_submission)."""
    doc = _get_or_404(incident_id)
    return render_template("submitted.html", doc=doc)


@app.route("/revise/<incident_id>", methods=["GET", "POST"])
def revise(incident_id):
    doc = _get_or_404(incident_id)
    sig = request.args.get("sig", "")
    state = _load_signed(_revise_signer, sig, incident_id, "revise")
    if state == "expired":
        return _link_expired("revise")
    if state != "ok":
        abort(403)
    if doc["status"] != "changes_requested":
        return redirect(url_for("submitted", incident_id=incident_id))
    hints = lg.load_rules().get("form_hints", [])
    if request.method == "POST":
        doc["fields"] = _fields_from_form(request.form)
        storage.audit(doc, "revised", actor="reporter", detail="reporter resubmitted after changes")
        _save_attachments(doc)
        _finalize_submission(doc)
        return redirect(url_for("submitted", incident_id=doc["id"]))
    return render_template("form.html", mode="revise", fields=F.FIELDS, sections=F.SECTIONS,
                           values=F.derive(doc["fields"], _hotels()), hints=hints, notes=doc.get("change_notes", ""),
                           attachments=_attachment_names(doc),
                           action=url_for("revise", incident_id=incident_id) + f"?sig={sig}",
                           conditions_json=_conditions_for_js(), hotels=_hotels())


_hotels_cache = None


def _hotels():
    """Hotel directory for the Section A/B autofill picker (hotels.json)."""
    global _hotels_cache
    if _hotels_cache is None:
        try:
            import json
            with open(config.HOTELS_PATH, encoding="utf-8") as fh:
                _hotels_cache = json.load(fh).get("hotels", [])
        except Exception:
            _hotels_cache = []
    return _hotels_cache


def _carrier_ok(doc):
    """Should this report be filed with Broadspire?

    Marsh confirmed (2026-07-30) that Wright provides general liability at only
    some managed properties; at the rest the owner carries it, and incidents
    there must go to the owner's carrier instead. Filing an owner-covered loss
    with Broadspire sends claimant details to a carrier with no policy for that
    property and starts the clock in the wrong place, so the send button is
    closed off rather than left to memory.

    Returns (allowed, reason). An unrecognised property is treated as unknown
    and blocked — better a manual check than a misrouted claim.
    """
    hotel = _hotel_for(doc)
    if hotel is None:
        return False, ("This report isn't matched to a property in the hotel "
                       "directory, so we can't tell whose policy covers it. "
                       "Check the location code before filing.")
    coverage = (hotel.get("gl_coverage") or "unknown").lower()
    if coverage == "wright":
        return True, ""
    if coverage == "owner":
        return False, (f"{hotel['name']} is owner-covered — general liability "
                       f"there is carried by the property owner, not Wright. "
                       f"This incident goes to the owner's carrier, not "
                       f"Broadspire. Download the PDF and send it on.")
    return False, (f"Coverage for {hotel['name']} isn't recorded in the hotel "
                   f"directory. Confirm with Marsh before filing.")


def _hotel_for(doc):
    """Match the record to a hotels.json entry (location code, then name)."""
    f = doc.get("fields", {})
    code = (f.get("location_code") or "").strip().upper()
    name = (f.get("loss_location_name") or "").strip().lower()
    for h in _hotels():
        if code and h.get("location_code", "").upper() == code:
            return h
    for h in _hotels():
        if name and h.get("name", "").lower() == name:
            return h
    return None


def _conditions_for_js():
    """Serializable rule set so the form shows only the sections and fields this
    loss type actually needs (fields.visibility_payload)."""
    return F.visibility_payload()


@app.route("/addendum/<incident_id>", methods=["GET", "POST"])
def addendum(incident_id):
    """Add documents/photos/notes to an existing report, in any status.
    Signature-gated; shows only minimal report info, never the record itself.
    Additions only - nothing can be viewed, removed, or altered. All audited."""
    doc = _get_or_404(incident_id)
    state = _load_signed(_addendum_signer, request.args.get("sig", ""),
                         incident_id, "addendum")
    if state == "expired":
        return _link_expired("addendum")
    if state != "ok":
        abort(403)
    if request.method == "POST":
        # The name typed here is a claim, not an identity — anyone holding the
        # link can type anything. Record it as self-reported data so the audit
        # trail never attributes an entry to a person who did not make it.
        # The actor stays "addendum-link", which is what we actually know.
        added_by = (request.form.get("added_by") or "").strip()[:120]
        claimed = f"self-reported as {added_by!r}" if added_by else "name not given"
        note = (request.form.get("note") or "").strip()
        saved = _save_attachments(doc, actor="addendum-link")
        if saved or note:
            if note:
                doc.setdefault("supplements", []).append(
                    dict(ts=storage.now_iso(), by=added_by or "unknown",
                         via="addendum-link", note=note))
            storage.audit(doc, "supplement_added", actor="addendum-link",
                          detail=f"{saved} file(s) via add-documents link, {claimed}"
                                 + (f"; note: {note[:120]}" if note else ""),
                          extra=dict(self_reported_name=added_by))
            storage.get_store().save(doc)
            mailer.send_supplement_notice(doc, review_link(doc["id"]), added_by, note, saved)
            flash(f"Added {saved} file(s){' and a note' if note else ''} to {doc['id']}. "
                  f"{config.approver_name()} has been notified.")
        else:
            flash("Nothing was attached - choose files or write a note, then submit.")
    f = doc["fields"]
    return render_template("addendum.html", doc=doc,
                           location=f.get("loss_location_name", ""),
                           date=f.get("date_of_accident", ""))


# --------------------------------------------------------------------------- #
# Reviewer
# --------------------------------------------------------------------------- #
@app.get("/admin")
@full_reviewer_required
def admin():
    docs = storage.get_store().list()
    return render_template("dashboard.html", docs=docs)


@app.get("/review/<incident_id>")
@reviewer_required
def review(incident_id):
    doc = _get_or_404(incident_id)
    completeness = F.check_completeness(doc["fields"])
    pending = lg.pending_findings(doc)
    original_pdf = doc["files"].get("converted.pdf")
    return render_template("review.html", doc=doc, fields=F.FIELDS, sections=F.SECTIONS,
                           completeness=completeness, pending_count=len(pending),
                           original_pdf=original_pdf, attachments=_attachment_names(doc),
                           revise_url=(revise_link(doc["id"])
                                       if doc["status"] == "changes_requested" else None),
                           addendum_url=addendum_link(doc["id"]),
                           carrier_email=config.carrier_email(),
                           carrier_ok=_carrier_ok(doc)[0],
                           carrier_block_reason=_carrier_ok(doc)[1],
                           full_reviewer=_full_reviewer(),
                           approvable=completeness["complete"] and not pending)


@app.get("/file/<incident_id>/<name>")
@reviewer_required
def file_view(incident_id, name):
    doc = _get_or_404(incident_id)
    ref = doc["files"].get(name)
    if not ref:
        abort(404)
    data = storage.get_files().get(ref)
    import mimetypes
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # PDFs are previewed inline on the review screen (the converted original
    # sits in an iframe) and are rendered by the browser's sandboxed viewer.
    # Everything else is sent as a download: reporter-uploaded files must never
    # be interpreted as documents inside this app's origin. The extension
    # allow-list already excludes .svg/.html; this is the second lock.
    inline = mime == "application/pdf"
    resp = send_file(io.BytesIO(data), mimetype=mime,
                     as_attachment=not inline, download_name=name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if inline:
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return resp


@app.post("/review/<incident_id>/save")
@reviewer_required
def review_save(incident_id):
    doc = _get_or_404(incident_id)
    changed = []
    for f in F.FIELDS:
        if f["key"] not in request.form:
            continue
        new = request.form.get(f["key"], "").strip()
        old = doc["fields"].get(f["key"], "") or ""
        if new != old:
            doc["fields"][f["key"]] = new
            changed.append(f["key"])
            storage.audit(doc, "field_edited", actor="reviewer",
                          detail=f["label"], extra=dict(field=f["key"], old=old, new=new))
    # Re-scan narratives the reviewer hand-edited; keep decided findings.
    for key in F.NARRATIVE_FIELDS:
        if key in changed:
            decided = [x for x in doc["language_findings"]
                       if not (x["field"] == key and x["decision"] == "pending")]
            fresh = lg.scan_text(key, doc["fields"].get(key, ""))
            decided_keys = {(x["field"], x["sentence"], x["rule"]) for x in decided}
            fresh = [x for x in fresh if (x["field"], x["sentence"], x["rule"]) not in decided_keys]
            doc["language_findings"] = decided + fresh
    storage.get_store().save(doc)
    flash(f"Saved {len(changed)} field change(s)." if changed else "No changes.")
    return redirect(url_for("review", incident_id=incident_id))


@app.post("/review/<incident_id>/language/<path:finding_id>")
@reviewer_required
def review_language(incident_id, finding_id):
    doc = _get_or_404(incident_id)
    decision = request.form.get("decision")
    edited = request.form.get("edited_text")
    try:
        lg.apply_decision(doc, finding_id, decision, edited_text=edited, actor="reviewer")
    except (KeyError, ValueError):
        abort(400)
    storage.get_store().save(doc)
    return redirect(url_for("review", incident_id=incident_id))


@app.post("/review/<incident_id>/action")
@reviewer_required
def review_action(incident_id):
    doc = _get_or_404(incident_id)
    action = request.form.get("action")

    if action == "approve":
        completeness = F.check_completeness(doc["fields"])
        pending = lg.pending_findings(doc)
        if not completeness["complete"] or pending:
            flash("Cannot approve: "
                  + (f"{len(completeness['missing'])} mandatory field(s) missing/"
                     f"{len(completeness['malformed'])} malformed. " if not completeness["complete"] else "")
                  + (f"{len(pending)} language finding(s) undecided." if pending else ""))
            return redirect(url_for("review", incident_id=incident_id))
        pdf_only = bool(request.form.get("pdf_only"))
        artifacts, pdf_bytes = outputs.generate_approval_outputs(
            doc, approver=config.approver_email(), pdf_only=pdf_only)
        doc["status"] = "approved"
        storage.audit(doc, "approved", actor=config.approver_email(),
                      detail=", ".join(artifacts))
        allowed, reason = _carrier_ok(doc)
        if config.AUTO_SEND_TO_CARRIER and pdf_bytes and allowed:
            sent = mailer.send_to_carrier(doc, pdf_bytes)
            storage.audit(doc, "sent_to_carrier" if sent else "carrier_send_failed",
                          detail=config.carrier_email())
        elif config.AUTO_SEND_TO_CARRIER and pdf_bytes and not allowed:
            storage.audit(doc, "carrier_send_blocked", actor="system", detail=reason)
        storage.get_store().save(doc)
        mailer.send_approved_notice(doc)
        flash("Approved. Outputs generated: " + ", ".join(artifacts))

    elif action == "request_changes":
        notes = request.form.get("notes", "").strip()
        doc["status"] = "changes_requested"
        doc["change_notes"] = notes
        storage.audit(doc, "changes_requested", actor=config.approver_email(), detail=notes)
        storage.get_store().save(doc)
        hotel = _hotel_for(doc)
        emailed = mailer.send_changes_request(doc, notes, revise_link(doc["id"]),
                                              gm_email=(hotel or {}).get("gm_email"))
        flash("Changes requested — reporter emailed a revise link."
              if emailed else
              "Changes requested. No reporter email on file — copy the revise "
              "link shown on this page and send it to the team yourself.")

    elif action == "reject":
        reason = request.form.get("notes", "").strip()
        doc["status"] = "rejected"
        storage.audit(doc, "rejected", actor=config.approver_email(), detail=reason)
        storage.get_store().save(doc)
        flash("Report rejected and archived.")
    else:
        abort(400)
    return redirect(url_for("review", incident_id=incident_id))


@app.post("/review/<incident_id>/send-carrier")
@reviewer_required
def review_send_carrier(incident_id):
    """Explicit reviewer action: email the approved Broadspire PDF (and
    optionally the supporting attachments) to the carrier claim address."""
    doc = _get_or_404(incident_id)
    if doc["status"] != "approved":
        abort(400)
    allowed, reason = _carrier_ok(doc)
    if not allowed:
        # Guarded server-side as well as in the template: the button is hidden,
        # but a stale tab or a bookmarked POST must not get through either.
        storage.audit(doc, "carrier_send_blocked", actor=config.approver_email(),
                      detail=reason)
        storage.get_store().save(doc)
        flash(f"Not sent. {reason}")
        return redirect(url_for("review", incident_id=incident_id))
    ref = doc["files"].get("approved_form.pdf")
    if not ref:
        flash("No approved PDF on file for this incident — cannot send.")
        return redirect(url_for("review", incident_id=incident_id))
    pdf = storage.get_files().get(ref)
    extra = []
    if request.form.get("include_attachments"):
        total = 0
        for name in _attachment_names(doc):
            data = storage.get_files().get(doc["files"][name])
            total += len(data)
            if total > 20 * 1024 * 1024:   # keep the claim email deliverable
                flash("Some attachments were left off to keep the email under 20 MB.")
                break
            disp = name.split("_", 2)[2] if name.count("_") >= 2 else name
            extra.append((disp, data))
    sent = mailer.send_to_carrier(doc, pdf, extra_attachments=extra)
    if sent:
        doc["carrier_sent_at"] = storage.now_iso()
        storage.audit(doc, "sent_to_carrier", actor=config.approver_email(),
                      detail=f"{config.carrier_email()} (+{len(extra)} attachment(s)); CC {config.approver_email()}")
        flash(f"Claim sent to {config.carrier_email()} (you were CC'd).")
    else:
        storage.audit(doc, "carrier_send_failed", actor=config.approver_email(),
                      detail="SMTP not configured or send error")
        flash("Send failed (or SMTP not configured) — download the PDF and send manually.")
    storage.get_store().save(doc)
    return redirect(url_for("review", incident_id=incident_id))


@app.get("/register.xlsx")
@full_reviewer_required
def register_download():
    rows = storage.get_store().register_rows()
    return send_file(io.BytesIO(outputs.register_xlsx(rows)),
                     download_name="incident_register.xlsx", as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
