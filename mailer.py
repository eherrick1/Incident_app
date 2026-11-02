"""Email notifications — mirrors the capital/expense apps' mailer process:
SMTP settings from the environment (works locally via .env, on Cloud Run via
service env vars); Office365/Gmail via STARTTLS (587) or implicit SSL (465);
optional EMAIL_USERNAME (defaults to EMAIL_SENDER) and MAIL_CC. If SMTP isn't
configured, sends are skipped gracefully (logged, never raised) so the rest
of the workflow keeps working in dev.
"""
import re
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from markupsafe import escape

import config


def h(value, default="—"):
    """HTML-escape a value for interpolation into a message body.

    These bodies are built with f-strings, so anything interpolated raw is
    live HTML. Several of the values below are attacker-reachable — the
    add-documents link accepts a free-text note and name from anyone holding
    it — and the resulting mail is sent from our own mailbox, passing SPF and
    DKIM, into the reviewer's inbox. That is a ready-made phishing primitive;
    escaping removes it.
    """
    if value in (None, ""):
        return default
    return escape(str(value))


def is_configured() -> bool:
    return bool(config.EMAIL_SENDER and config.EMAIL_PASSWORD
                and config.APPROVER_EMAIL and config.SMTP_SERVER)


_ADDR_RE = re.compile(r"^[^@\s,;:<>\"]+@[^@\s,;:<>\"]+\.[^@\s,;:<>\"]+$")


def _redact_addr(addr):
    """'jane.doe@marriott.com' -> 'j***@marriott.com'.

    Enough to debug a delivery problem, not enough to identify a claimant or
    harvest an address from a log line."""
    s = _header_safe(addr)
    if "@" not in s:
        return "<no address>" if not s else "<malformed>"
    local, _, domain = s.partition("@")
    return f"{local[:1]}***@{domain}"


def _header_safe(value):
    """Collapse CR/LF out of a header value.

    Python's email library raises on embedded headers rather than emitting
    them, so this was never an injection hole — but the exception is caught by
    the broad handler below, which turns a stray newline in a hotel name or a
    reporter's address into a silently unsent notification. Stripping first
    means the mail goes out.
    """
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


def valid_address(addr):
    """A recipient we're willing to hand to the SMTP server."""
    return bool(addr) and bool(_ADDR_RE.match(_header_safe(addr)))


def _send(to, subject, html, attachments=None, cc=None):
    """Send one HTML email. Returns True on send, False if SMTP isn't configured."""
    subject = _header_safe(subject)
    if not valid_address(to):
        # No address, no subject: incident reports concern injuries and named
        # claimants, and Cloud Run request logs are readable by anyone with
        # Logs Viewer on the project. Log the shape of the problem, not the PII.
        print(f"[mailer] refusing to send: recipient failed validation "
              f"({_redact_addr(to)})")
        return False
    to = _header_safe(to)
    cc = [_header_safe(c) for c in (cc or []) if valid_address(c)]
    if not is_configured():
        print(f"[mailer] (dry-run, SMTP not configured) to={_redact_addr(to)}")
        return False
    msg = MIMEMultipart()
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = to
    recipients = [to]
    all_cc = list(cc or []) + config.MAIL_CC
    if all_cc:
        msg["Cc"] = ", ".join(all_cc)
        recipients += all_cc
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))
    for name, data in (attachments or []):
        part = MIMEApplication(data, Name=name)
        part["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(part)
    username = config.EMAIL_USERNAME or config.EMAIL_SENDER
    try:
        if config.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(config.SMTP_SERVER, config.SMTP_PORT,
                                  context=ssl.create_default_context()) as s:
                s.login(username, config.EMAIL_PASSWORD)
                s.sendmail(config.EMAIL_SENDER, recipients, msg.as_string())
        else:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as s:
                if config.SMTP_STARTTLS:
                    s.starttls(context=ssl.create_default_context())
                s.login(username, config.EMAIL_PASSWORD)
                s.sendmail(config.EMAIL_SENDER, recipients, msg.as_string())
        return True
    except Exception as e:  # never break the workflow over email
        # Exception text can echo the recipient — log the type, not the message.
        print(f"[mailer] send failed to {_redact_addr(to)}: {type(e).__name__}")
        return False


def send_approval_request(doc, review_url):
    f = doc["fields"]
    subject = (f"New incident report — {f.get('loss_location_name', 'unknown location')}, "
               f"{f.get('date_of_accident', 'date n/a')} — ready for review")
    html = f"""
    <p>Hi {h(config.APPROVER_NAME)},</p>
    <p>A new general-liability incident report was submitted and is waiting for
    your review. Nothing has been filed or sent to the carrier.</p>
    <ul>
      <li><b>Incident ID:</b> {h(doc['id'])}</li>
      <li><b>Location:</b> {h(f.get('loss_location_name'))}</li>
      <li><b>Date of accident:</b> {h(f.get('date_of_accident'))}</li>
      <li><b>Reported by:</b> {h(f.get('reported_by_name'))} ({h(f.get('reported_by_title'))})</li>
      <li><b>Intake:</b> {h(doc['intake_method'])}</li>
    </ul>
    <p><a href="{review_url}">Open the review screen</a></p>
    """
    return _send(config.APPROVER_EMAIL, subject, html)


def send_changes_request(doc, notes, revise_url, gm_email=None):
    """Bounce to the reporter, CC the property GM (they own the follow-up)."""
    # Pick the first address that is actually usable. A reporter who mistypes
    # their address (or pastes something with a newline in it) should fall
    # through to the GM, not silently kill the notification.
    candidates = [doc["fields"].get("reported_by_email"),
                  doc.get("submitter", {}).get("email")]
    to = next((a for a in candidates if valid_address(a)), None)
    cc = [gm_email] if (gm_email and valid_address(gm_email)) else []
    if not to and cc:
        to, cc = cc[0], []       # no usable reporter email: straight to the GM
    if not to:
        print(f"[mailer] no usable reporter/GM email on {doc['id']}; "
              f"changes note not sent (reporter value: "
              f"{_redact_addr(doc['fields'].get('reported_by_email'))})")
        return False
    html = f"""
    <p>Incident report <b>{h(doc['id'])}</b> needs changes before it can be approved:</p>
    <blockquote>{h(notes, default='(no notes provided)')}</blockquote>
    <p><a href="{revise_url}">Open the report to make the changes</a></p>
    """
    return _send(to, f"Changes requested - incident report {doc['id']}", html, cc=cc)


def send_gm_notice(doc, gm_name, gm_email, addendum_url):
    """Tell the property GM a report was filed at their hotel; they own follow-up."""
    f = doc["fields"]
    html = f"""
    <p>Hi {h(gm_name, default='there')},</p>
    <p>An incident report was just filed for <b>{h(f.get('loss_location_name'), default='your property')}</b>
    (date of accident {h(f.get('date_of_accident'), default='-')}), reported by
    {h(f.get('reported_by_name'), default='-')}. Reference: <b>{h(doc['id'])}</b>.</p>
    <p>It is now with {h(config.APPROVER_NAME)} for review. <b>As GM, you own the
    follow-up on this incident</b>: if more information is needed you may receive
    a change-request email, and any documents that come in later (doctor's
    paperwork, photos, receipts) should be added to the report.</p>
    <p><a href="{addendum_url}">Add documents or notes to this report</a>
    &mdash; keep this link; it works even after the report is approved.</p>
    <p>Reminder: never give the guest a copy of the report. If they want
    confirmation, give them the reference number above only.</p>
    """
    return _send(gm_email, f"Incident report filed at {f.get('loss_location_name', 'your property')} - {doc['id']}", html)


def send_supplement_notice(doc, review_url, added_by, note, n_files):
    """Tell the reviewer that documents/notes were added after submission."""
    f = doc["fields"]
    # added_by and note come from the public add-documents form — anyone with
    # the link can type anything here, so both are escaped and the name is
    # labelled as unverified rather than presented as the sender.
    html = f"""
    <p>Material was added to incident report <b>{h(doc['id'])}</b>
    ({h(f.get('loss_location_name'), default='-')},
    status: {h(doc['status'].replace('_', ' '))}) via the add-documents link.</p>
    <ul><li><b>Added by (self-reported, unverified):</b> {h(added_by, default='name not given')}</li>
    <li><b>Files added:</b> {h(n_files, default='0')}</li>
    {f'<li><b>Note:</b> {h(note)}</li>' if note else ''}</ul>
    <p><a href="{review_url}">Open the review screen</a></p>
    """
    return _send(config.APPROVER_EMAIL, f"New documents on incident report {doc['id']}", html)


def send_approved_notice(doc, attachments=None):
    to = doc["fields"].get("reported_by_email") or doc.get("submitter", {}).get("email")
    if not to:
        return False
    return _send(to, f"Incident report {doc['id']} approved",
                 f"<p>Your incident report <b>{h(doc['id'])}</b> has been reviewed and approved.</p>",
                 attachments=attachments)


def send_to_carrier(doc, pdf_bytes, extra_attachments=None):
    """File the claim with the carrier. Used by the reviewer's explicit
    "Send to Broadspire" button (and by AUTO_SEND_TO_CARRIER if enabled).
    CCs the approver so there is always a copy of exactly what was filed."""
    f = doc["fields"]
    atts = [(f"{doc['id']}_broadspire_gl_form.pdf", pdf_bytes)]
    atts += list(extra_attachments or [])
    return _send(
        config.CARRIER_EMAIL,
        f"General Liability Notice of Loss — {f.get('parent_co_name', '')} — DOA {f.get('date_of_accident', '')}",
        f"""<p>Please find attached a Broadspire General Liability Reporting Form
        (incident reference {h(doc['id'])}) for {h(f.get('loss_location_name'), default='')},
        date of accident {h(f.get('date_of_accident'), default='')}.</p>
        <p>Contact: {h(f.get('reported_by_name'), default='')}, {h(f.get('business_phone'), default='')}.</p>""",
        attachments=atts, cc=[config.APPROVER_EMAIL])
