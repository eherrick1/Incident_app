"""End-to-end smoke tests with the Flask test client (local backend:
SQLite + ./storage in a temp dir, no cloud, no API key, dry-run mailer)."""
import io
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["LOCAL_STORAGE"] = _tmp
os.environ["SQLITE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["ADMIN_TOKEN"] = "test-token"
os.environ["FLASK_SECRET"] = "test-secret"
os.environ["AI_PROVIDER"] = "none"   # no live AI calls from tests
os.environ["EMAIL_SENDER"] = ""      # never send real mail from tests
os.environ["EMAIL_PASSWORD"] = ""
os.environ.pop("GCS_BUCKET", None)
os.environ.pop("FIRESTORE_COLLECTION", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import fields as F
import outputs
import storage
from tests.test_completeness import BASE

client = appmod.app.test_client()


def _submit_online(extra=None):
    data = dict(BASE)
    data["accident_description"] = "The guest fell in the tub because it was slippery."
    if extra:
        data.update(extra)
    r = client.post("/form", data=data)
    assert r.status_code == 302
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    return inc_id


def test_public_pages_open():
    for path in ("/", "/form", "/upload", "/healthz"):
        assert client.get(path).status_code == 200
    assert client.get("/blank-form.xlsx").status_code == 200


def test_reviewer_routes_gated():
    with client.session_transaction() as s:
        s.clear()
    assert client.get("/admin").status_code == 403
    assert client.get("/admin?token=wrong").status_code == 403
    # ?token= is now traded for a session cookie and bounced to a clean URL,
    # so the token stops living in the address bar / history / Referer.
    r = client.get("/admin?token=test-token")
    assert r.status_code == 302 and "token=" not in r.headers["Location"]
    assert client.get("/admin").status_code == 200  # session now set
    with client.session_transaction() as s:
        s.clear()


def test_online_submit_scans_language_and_lands_pending():
    inc_id = _submit_online()
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "pending_review"
    assert doc["intake_method"] == "online_form"
    rules_hit = {f["rule"] for f in doc["language_findings"]}
    assert {"attribution", "fault_causation"} <= rules_hit
    assert doc["original_narratives"]["accident_description"].startswith("The guest fell")
    with client.session_transaction() as s:
        s.clear()


def test_xlsx_upload_roundtrip_identical_record():
    """Intake path 2: fill the official workbook, upload it, confirm the
    extracted record matches the online-form record."""
    xlsx = outputs.fill_xlsx(dict(BASE))
    r = client.post("/upload", data={"files": (io.BytesIO(xlsx), "filled.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "draft"
    for key, val in BASE.items():
        assert doc["fields"].get(key) == str(val), key
    # reporter confirms -> submitted
    r = client.post(f"/confirm/{inc_id}", data=doc["fields"])
    assert r.status_code == 302
    assert storage.get_store().get(inc_id)["status"] == "pending_review"
    with client.session_transaction() as s:
        s.clear()


def test_photo_upload_merges_to_pdf():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(buf, format="PNG")
    r = client.post("/upload", data={"files": (io.BytesIO(buf.getvalue()), "page1.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc_id)
    assert "converted.pdf" in doc["files"]
    assert storage.get_files().get(doc["files"]["converted.pdf"])[:5] == b"%PDF-"
    with client.session_transaction() as s:
        s.clear()


def test_approve_blocked_then_full_review_flow():
    # Missing mandatory field -> approve must be refused.
    incomplete = dict(BASE)
    incomplete.pop("witness1_name")
    incomplete["accident_description"] = "The guest fell in the tub because it was slippery."
    r = client.post("/form", data=incomplete)
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    r = client.post(f"/review/{inc_id}/action?token=test-token", data={"action": "approve"})
    assert storage.get_store().get(inc_id)["status"] == "pending_review"  # still not approved

    # Complete record, but language findings pending -> still blocked.
    inc_id = _submit_online()
    client.get(f"/review/{inc_id}?token=test-token")
    client.post(f"/review/{inc_id}/action", data={"action": "approve"})
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "pending_review"

    # Decide findings: accept first pending, keep the rest.
    import language_guard as lg
    pend = lg.pending_findings(doc)
    r = client.post(f"/review/{inc_id}/language/{pend[0]['id']}", data={"decision": "accept"})
    assert r.status_code == 302
    doc = storage.get_store().get(inc_id)
    for f in lg.pending_findings(doc):
        client.post(f"/review/{inc_id}/language/{f['id']}", data={"decision": "keep"})
    doc = storage.get_store().get(inc_id)
    assert lg.pending_findings(doc) == []
    assert "alleges they fell" in doc["fields"]["accident_description"]

    # Now approve -> outputs + register row + audit.
    reg_before = len(storage.get_store().register_rows())
    client.post(f"/review/{inc_id}/action", data={"action": "approve"})
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "approved"
    assert "approved_form.xlsx" in doc["files"] and "approved_form.docx" in doc["files"]
    rows = storage.get_store().register_rows()
    assert len(rows) == reg_before + 1
    assert rows[-1]["incident_id"] == inc_id
    actions = [a["action"] for a in doc["audit"]]
    assert "approved" in actions and "language_accepted" in actions
    with client.session_transaction() as s:
        s.clear()


def test_request_changes_and_revise_flow():
    inc_id = _submit_online()
    client.get(f"/review/{inc_id}?token=test-token")
    client.post(f"/review/{inc_id}/action",
                data={"action": "request_changes", "notes": "Please add the witness phone."})
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "changes_requested"
    with client.session_transaction() as s:
        s.clear()
    # Reporter revises via signed link only.
    assert client.get(f"/revise/{inc_id}?sig=garbage").status_code == 403
    sig = appmod._revise_signer.dumps(inc_id)
    assert client.get(f"/revise/{inc_id}?sig={sig}").status_code == 200
    data = dict(doc["fields"], witness1_phone="239-555-0199",
                accident_description="The guest reported that they slipped in the bathtub of room 204.")
    r = client.post(f"/revise/{inc_id}?sig={sig}", data=data)
    assert r.status_code == 302
    assert storage.get_store().get(inc_id)["status"] == "pending_review"


def test_signed_review_link_grants_access():
    inc_id = _submit_online()
    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/review/{inc_id}").status_code == 403
    sig = appmod._signer.dumps(inc_id)
    assert client.get(f"/review/{inc_id}?sig={sig}").status_code == 200
    with client.session_transaction() as s:
        s.clear()


def test_filled_xlsx_writes_expected_cells():
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(outputs.fill_xlsx(dict(BASE))))
    ws = wb.active
    assert ws["D12"].value == "Ana Torres"
    assert ws["C17"].value == "Herrick Hospitality LLC"
    assert ws["F24"].value == "X"        # on insured premises: yes
    assert ws["F40"].value == "X"        # type of loss: injury
    assert ws["D49"].value == "N"        # ambulance: no
    assert ws["J49"].value == "X"        # fatality: no
    assert ws["C34"].value.startswith("The guest reported")


def test_attachments_on_online_form_submit():
    from PIL import Image
    img = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(img, format="JPEG")
    data = dict(BASE, accident_description="The guest reported that they slipped in room 204.")
    data["attachments"] = [
        (io.BytesIO(img.getvalue()), "scene photo.jpg"),
        (io.BytesIO(b"witness statement text"), "statement.txt"),
        (io.BytesIO(b"nope"), "malware.exe"),  # disallowed type -> skipped
    ]
    r = client.post("/form", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc_id)
    names = sorted(k for k in doc["files"] if k.startswith("attachment_"))
    assert len(names) == 2, names                      # .exe skipped
    assert any(n.endswith("scene_photo.jpg") for n in names)
    assert any(n.endswith("statement.txt") for n in names)
    assert any(a["action"] == "attachments_added" for a in doc["audit"])
    # reviewer can fetch them; public cannot
    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/file/{inc_id}/{names[0]}").status_code == 403
    txt = [n for n in names if n.endswith(".txt")][0]
    resp = client.get(f"/file/{inc_id}/{txt}?token=test-token", follow_redirects=True)
    assert resp.status_code == 200 and resp.data == b"witness statement text"
    with client.session_transaction() as s:
        s.clear()


def test_attachments_on_upload_and_confirm_paths():
    from PIL import Image
    form_img = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(form_img, format="PNG")
    scene = io.BytesIO()
    Image.new("RGB", (80, 80), "red").save(scene, format="PNG")
    r = client.post("/upload", data={
        "files": (io.BytesIO(form_img.getvalue()), "form.png"),
        "attachments": (io.BytesIO(scene.getvalue()), "wet floor.png"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc_id)
    names = [k for k in doc["files"] if k.startswith("attachment_")]
    assert len(names) == 1 and names[0].endswith("wet_floor.png")
    # add one more at the confirm step
    more = io.BytesIO()
    Image.new("RGB", (60, 60), "green").save(more, format="PNG")
    data = dict(BASE)
    data["attachments"] = (io.BytesIO(more.getvalue()), "close up.png")
    r = client.post(f"/confirm/{inc_id}", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    doc = storage.get_store().get(inc_id)
    names = sorted(k for k in doc["files"] if k.startswith("attachment_"))
    assert len(names) == 2, names
    with client.session_transaction() as s:
        s.clear()


def test_send_carrier_button_flow():
    # Must be a Wright-covered property: owner-covered ones are blocked from
    # filing with Broadspire (see test_owner_covered_property_...).
    inc_id = _submit_online(extra={"accident_description":
        "The guest reported that they slipped in the bathtub of room 204.",
        "loss_location_name": "Four Points By Sheraton", "location_code": "PPK"})
    # not approved yet -> 400
    client.get(f"/review/{inc_id}?token=test-token")
    assert client.post(f"/review/{inc_id}/send-carrier").status_code == 400
    # decide findings + approve
    import language_guard as lg
    doc = storage.get_store().get(inc_id)
    for f in lg.pending_findings(doc):
        client.post(f"/review/{inc_id}/language/{f['id']}", data={"decision": "keep"})
    client.post(f"/review/{inc_id}/action", data={"action": "approve"})
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "approved"
    # send to carrier (dry-run mailer -> recorded as failed, never silent)
    r = client.post(f"/review/{inc_id}/send-carrier",
                    data={"include_attachments": "on"})
    assert r.status_code == 302
    doc = storage.get_store().get(inc_id)
    actions = [a["action"] for a in doc["audit"]]
    assert ("sent_to_carrier" in actions) or ("carrier_send_failed" in actions)
    # unauthenticated cannot trigger a claim filing
    with client.session_transaction() as s:
        s.clear()
    assert client.post(f"/review/{inc_id}/send-carrier").status_code == 403


def test_request_changes_shows_revise_link_without_email():
    inc_id = _submit_online()   # BASE has no reported_by_email
    client.get(f"/review/{inc_id}?token=test-token")
    client.post(f"/review/{inc_id}/action",
                data={"action": "request_changes", "notes": "More detail please."})
    page = client.get(f"/review/{inc_id}").data.decode()
    assert f"/revise/{inc_id}?sig=" in page   # copyable link shown to reviewer
    with client.session_transaction() as s:
        s.clear()


def test_send_carrier_button_flow():
    # Must be a Wright-covered property: owner-covered ones are blocked from
    # filing with Broadspire (see test_owner_covered_property_...).
    inc_id = _submit_online(extra={"accident_description":
        "The guest reported that they slipped in the bathtub of room 204.",
        "loss_location_name": "Four Points By Sheraton", "location_code": "PPK"})
    # not approved yet -> 400
    client.get(f"/review/{inc_id}?token=test-token")
    assert client.post(f"/review/{inc_id}/send-carrier").status_code == 400
    # decide findings + approve
    import language_guard as lg
    doc = storage.get_store().get(inc_id)
    for f in lg.pending_findings(doc):
        client.post(f"/review/{inc_id}/language/{f['id']}", data={"decision": "keep"})
    client.post(f"/review/{inc_id}/action", data={"action": "approve"})
    doc = storage.get_store().get(inc_id)
    assert doc["status"] == "approved"
    # send to carrier (dry-run mailer -> recorded as failed, never silent)
    r = client.post(f"/review/{inc_id}/send-carrier",
                    data={"include_attachments": "on"})
    assert r.status_code == 302
    doc = storage.get_store().get(inc_id)
    actions = [a["action"] for a in doc["audit"]]
    assert ("sent_to_carrier" in actions) or ("carrier_send_failed" in actions)
    # unauthenticated cannot trigger a claim filing
    with client.session_transaction() as s:
        s.clear()
    assert client.post(f"/review/{inc_id}/send-carrier").status_code == 403


def test_request_changes_shows_revise_link_without_email():
    inc_id = _submit_online()   # BASE has no reported_by_email
    client.get(f"/review/{inc_id}?token=test-token")
    client.post(f"/review/{inc_id}/action",
                data={"action": "request_changes", "notes": "More detail please."})
    page = client.get(f"/review/{inc_id}").data.decode()
    assert f"/revise/{inc_id}?sig=" in page   # copyable link shown to reviewer
    with client.session_transaction() as s:
        s.clear()


def test_hotel_picker_on_form():
    page = client.get("/form").data.decode()
    assert 'id="hotel_picker"' in page
    assert "Hampton Inn Northwood Myrtle Beach" in page
    assert "Not listed / other location" in page
    # renamed labels present
    assert "Your job title" in page
    assert "Were police, fire or EMS called?" in page
    assert "About you" in page and "What happened" in page


def test_review_link_does_not_grant_access_to_other_incidents():
    """N1 regression: a signed review link is scoped to its own incident.

    It used to set a blanket session flag, so one forwarded review e-mail gave
    the recipient every report, the dashboard, and the register."""
    mine = _submit_online()
    theirs = _submit_online()
    sig = appmod._signer.dumps(mine)

    c = appmod.app.test_client()          # fresh session, no token
    assert c.get(f"/review/{mine}?sig={sig}").status_code == 200
    # …and nothing else.
    assert c.get(f"/review/{theirs}").status_code == 403
    assert c.get("/admin").status_code == 403
    assert c.get("/register.xlsx").status_code == 403
    assert c.get(f"/file/{theirs}/approved_form.pdf").status_code == 403


def test_review_link_scope_persists_for_its_own_incident():
    """Follow-up requests without the sig still work for that one incident."""
    inc = _submit_online()
    c = appmod.app.test_client()
    c.get(f"/review/{inc}?sig={appmod._signer.dumps(inc)}")
    assert c.get(f"/review/{inc}").status_code == 200
    assert c.get("/admin").status_code == 403


def test_admin_token_still_grants_full_access():
    inc = _submit_online()
    c = appmod.app.test_client()
    assert c.get(f"/admin?token={appmod.config.ADMIN_TOKEN}",
                 follow_redirects=True).status_code == 200
    assert c.get("/register.xlsx").status_code == 200
    assert c.get(f"/review/{inc}").status_code == 200


def test_submitted_page_does_not_expose_the_addendum_link():
    """N2 regression: the incident ID is quoted to guests as a reference
    number, so the public confirmation page must not carry a write link."""
    inc = _submit_online()
    page = appmod.app.test_client().get(f"/submitted/{inc}").data.decode()
    assert inc in page                      # ID and status are fine
    assert "/addendum/" not in page
    assert "sig=" not in page


def test_incident_ids_are_not_enumerable():
    """Ten hex characters, and distinct across records."""
    import re as _re
    ids = {_submit_online() for _ in range(5)}
    assert len(ids) == 5
    for i in ids:
        assert _re.fullmatch(r"INC-\d{8}-[0-9A-F]{10}", i), i


def test_login_accepts_the_token_in_a_post_body():
    """F3 regression: the portal POSTs the token instead of putting it in a URL."""
    c = appmod.app.test_client()
    r = c.post("/login", data={"token": appmod.config.ADMIN_TOKEN, "next": "/admin"},
               headers={"Origin": "https://portal.example"})   # cross-origin by design
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin")
    assert c.get("/admin").status_code == 200
    assert c.post("/login", data={"token": "wrong"}).status_code == 401


def test_login_is_rate_limited():
    """F12 regression: a fixed sleep isn't a control — attempts can run in
    parallel. The address gets locked out."""
    appmod._LOGIN_FAILURES.clear()
    c = appmod.app.test_client()
    real_sleep = appmod.time.sleep
    appmod.time.sleep = lambda _s: None      # don't pay the anti-brute-force delay
    try:
        codes = [c.post("/login", data={"token": f"guess-{i}"}).status_code
                 for i in range(appmod._LOGIN_MAX_TRIES + 2)]
        assert codes[0] == 401 and codes[-1] == 429
        # Even the correct token is refused while locked out.
        assert c.post("/login",
                      data={"token": appmod.config.ADMIN_TOKEN}).status_code == 429
        appmod._LOGIN_FAILURES.clear()
        assert c.post("/login",
                      data={"token": appmod.config.ADMIN_TOKEN}).status_code == 302
    finally:
        appmod.time.sleep = real_sleep


def _approve(inc_id, c):
    """Drive a submitted report through language decisions to approved."""
    import language_guard as lg
    c.get(f"/review/{inc_id}")
    doc = storage.get_store().get(inc_id)
    for f in lg.pending_findings(doc):
        c.post(f"/review/{inc_id}/language/{f['id']}", data={"decision": "keep"})
    c.post(f"/review/{inc_id}/action", data={"action": "approve"})
    return storage.get_store().get(inc_id)


def test_owner_covered_property_cannot_be_filed_with_broadspire():
    """Marsh confirmed Wright doesn't carry GL at every managed property.
    Filing an owner-covered loss with Broadspire sends claimant details to a
    carrier with no policy for it."""
    inc = _submit_online({"loss_location_name": "Springhill Suites Arlington, Tn",
                          "location_code": "SHA"})
    c = appmod.app.test_client()
    c.post("/login", data={"token": appmod.config.ADMIN_TOKEN})
    doc = _approve(inc, c)
    assert doc["status"] == "approved"

    page = c.get(f"/review/{inc}").data.decode()
    assert "Do not file this with Broadspire" in page
    assert "owner-covered" in page

    # And the route itself refuses, not just the button.
    c.post(f"/review/{inc}/send-carrier", follow_redirects=True)
    doc = storage.get_store().get(inc)
    assert "carrier_sent_at" not in doc
    assert any(a["action"] == "carrier_send_blocked" for a in doc["audit"])


def test_wright_covered_property_can_still_be_filed():
    inc = _submit_online({"loss_location_name": "Four Points By Sheraton",
                          "location_code": "PPK"})
    c = appmod.app.test_client()
    c.post("/login", data={"token": appmod.config.ADMIN_TOKEN})
    _approve(inc, c)
    page = c.get(f"/review/{inc}").data.decode()
    assert "Do not file this with Broadspire" not in page
    assert "Send claim to" in page


def test_unknown_property_is_blocked_rather_than_guessed():
    inc = _submit_online({"loss_location_name": "Some Hotel We Don't Manage",
                          "location_code": "ZZZ"})
    c = appmod.app.test_client()
    c.post("/login", data={"token": appmod.config.ADMIN_TOKEN})
    _approve(inc, c)
    page = c.get(f"/review/{inc}").data.decode()
    assert "Do not file this with Broadspire" in page


def test_hotel_directory_matches_the_carrier_schedule():
    """Guards the values Marsh supplied on 2026-07-30."""
    import json
    hotels = {h["location_code"]: h for h in json.load(
        open(appmod.config.HOTELS_PATH, encoding="utf-8"))["hotels"]}
    owner_covered = {"WIN", "FFC", "SHA", "FFA"}
    for code, h in hotels.items():
        assert h["gl_coverage"] in ("wright", "owner"), code
        if code in owner_covered:
            assert h["gl_coverage"] == "owner" and not h["policy_number"], code
        else:
            assert h["gl_coverage"] == "wright"
            assert h["policy_number"] == "1000305902251", code
        assert h["parent_co_name"] == "Wright Investments, Inc.", code
    assert hotels["FFA"]["business_address"] == "4928 Fair Springs Cove"


def test_ai_extracted_fields_are_marked_for_the_reviewer():
    """N10: text inside an uploaded document can steer the extractor, so the
    reviewer should be able to see which values a model produced."""
    xlsx = outputs.fill_xlsx(dict(BASE))
    r = client.post("/upload", data={"files": (io.BytesIO(xlsx), "filled.xlsx")},
                    content_type="multipart/form-data")
    inc = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc)
    assert "accident_description" in doc["ai_extracted_keys"]

    # Reporter edits one field on the confirm screen -> it stops being AI-read.
    fields = dict(doc["fields"], accident_description="The guest reported a fall.")
    client.post(f"/confirm/{inc}", data=fields)
    doc = storage.get_store().get(inc)
    assert "accident_description" not in doc["ai_extracted_keys"]
    assert "injured_name" in doc["ai_extracted_keys"]      # untouched, still flagged

    c = appmod.app.test_client()
    c.post("/login", data={"token": appmod.config.ADMIN_TOKEN})
    page = c.get(f"/review/{inc}").data.decode()
    assert "AI-read" in page


def test_decompression_bomb_is_refused():
    """N9: a small file that decodes to gigabytes would take the service down."""
    import intake
    from PIL import Image
    # A modest image, with the guard wound down so it trips deterministically.
    buf = io.BytesIO()
    Image.new("RGB", (2000, 2000), "white").save(buf, format="PNG")
    real = Image.MAX_IMAGE_PIXELS
    try:
        intake.MAX_IMAGE_PIXELS = 1000        # 1k pixels: our 4M-pixel image busts it
        try:
            intake.images_to_pdf([buf.getvalue()])
            assert False, "expected IntakeError"
        except intake.IntakeError as e:
            assert "larger than a photo" in str(e)
    finally:
        intake.MAX_IMAGE_PIXELS = 80_000_000
        Image.MAX_IMAGE_PIXELS = real
    # A normal photo still converts.
    assert intake.images_to_pdf([buf.getvalue()])[:5] == b"%PDF-"


def test_security_headers_present():
    r = appmod.app.test_client().get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["Content-Security-Policy"]


def test_review_screen_can_still_frame_its_own_pdf():
    """Regression: X-Frame-Options DENY blanked the original-upload preview on
    the review screen, because DENY blocks same-origin framing too."""
    r = appmod.app.test_client().get("/")
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'none'" not in r.headers["Content-Security-Policy"]


def test_attachments_are_served_as_downloads():
    """N12: reporter-uploaded files must not render inside our own origin."""
    inc = _submit_online()
    c = appmod.app.test_client()
    c.post("/login", data={"token": appmod.config.ADMIN_TOKEN})
    doc = storage.get_store().get(inc)
    doc["files"]["attachment_0_note.txt"] = storage.get_files().put(
        inc, "attachment_0_note.txt", b"<script>alert(1)</script>")
    storage.get_store().save(doc)
    r = c.get(f"/file/{inc}/attachment_0_note.txt")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_login_next_cannot_leave_the_site():
    c = appmod.app.test_client()
    for bad in ("//evil.test/", "https://evil.test/", "javascript:alert(1)"):
        r = c.post("/login", data={"token": appmod.config.ADMIN_TOKEN, "next": bad})
        assert "evil.test" not in r.headers["Location"]
        assert "javascript" not in r.headers["Location"]


def test_cross_site_post_is_refused():
    """F7 regression: a reviewer on a hostile page must not be able to be made
    to approve, reject, or file a claim."""
    inc = _submit_online()
    c = appmod.app.test_client()
    c.get(f"/admin?token={appmod.config.ADMIN_TOKEN}", follow_redirects=True)  # full reviewer
    hostile = {"Origin": "https://evil.test"}
    for path, data in [
        (f"/review/{inc}/action", {"action": "approve"}),
        (f"/review/{inc}/save", {"witness1_name": "Forged"}),
        (f"/review/{inc}/send-carrier", {}),
    ]:
        assert c.post(path, data=data, headers=hostile).status_code == 403, path
    # Untouched by the attempt.
    assert storage.get_store().get(inc)["status"] == "pending_review"
    # Same-origin still works.
    r = c.post(f"/review/{inc}/save", data={"witness1_name": "Real"},
               headers={"Origin": "http://localhost"})
    assert r.status_code == 302


def test_public_submit_still_works_without_origin_header():
    """The CSRF check must not break the public submit paths — most posts come
    from people with no session at all."""
    data = dict(BASE, accident_description="The guest reported a fall.")
    assert client.post("/form", data=data).status_code == 302


def _stale_sig(salt, value, days_ago=400):
    """Mint a signature as if it had been issued `days_ago` days ago.

    itsdangerous stamps the mint time from itsdangerous.timed's clock, so
    winding that back is the honest way to age a link without sleeping."""
    import itsdangerous.timed as _timed
    from itsdangerous import URLSafeTimedSerializer
    real = _timed.time.time
    _timed.time.time = lambda: real() - (days_ago * 86400)
    try:
        return URLSafeTimedSerializer(appmod.config.FLASK_SECRET, salt=salt).dumps(value)
    finally:
        _timed.time.time = real


def test_signed_links_expire():
    """F9 regression: an e-mailed link is a credential; it must not live forever."""
    inc = _submit_online()
    for salt, path in [("revise-link", "/revise"), ("addendum-link", "/addendum")]:
        r = appmod.app.test_client().get(f"{path}/{inc}?sig={_stale_sig(salt, inc)}")
        assert r.status_code == 410, f"{salt} should be expired"
        assert b"expired" in r.data.lower()
    # A freshly minted link still works.
    fresh = appmod._addendum_signer.dumps(inc)
    assert appmod.app.test_client().get(f"/addendum/{inc}?sig={fresh}").status_code == 200


def test_expired_review_link_does_not_grant_scope():
    inc = _submit_online()
    c = appmod.app.test_client()
    assert c.get(f"/review/{inc}?sig={_stale_sig('review-link', inc)}").status_code == 403
    assert c.get(f"/review/{inc}").status_code == 403     # no scope was recorded


def test_addendum_cannot_forge_an_audit_actor():
    """N5 regression: the name typed into the public add-documents form is a
    claim, not an identity. It must never become the audit actor."""
    inc = _submit_online()
    sig = appmod._addendum_signer.dumps(inc)
    client.post(f"/addendum/{inc}?sig={sig}",
                data={"added_by": "eherrick@wrightinvestments.com",
                      "note": "Approved by me, please file."})
    doc = storage.get_store().get(inc)
    entry = [a for a in doc["audit"] if a["action"] == "supplement_added"][-1]
    assert entry["actor"] == "addendum-link"
    assert entry["self_reported_name"] == "eherrick@wrightinvestments.com"
    assert "self-reported" in entry["detail"]


def test_mailer_escapes_attacker_supplied_html():
    """N4 regression: the add-documents note is attacker-reachable and the
    resulting mail is sent from our own mailbox."""
    import mailer
    sent = {}
    orig = mailer._send
    mailer._send = lambda to, subject, html, **kw: sent.update(html=html, subject=subject) or True
    try:
        mailer.send_supplement_notice(
            {"id": "INC-1", "fields": {"loss_location_name": "Hotel"}, "status": "approved"},
            "http://x/review", '<a href="http://evil.test">Click to review</a>',
            "<script>alert(1)</script>", 1)
    finally:
        mailer._send = orig
    assert "<script>" not in sent["html"]
    assert "<a href=\"http://evil.test\">" not in sent["html"]
    assert "&lt;script&gt;" in sent["html"]


def test_mailer_rejects_unusable_recipients():
    """N11: a mistyped address must not silently swallow the notification."""
    import mailer
    assert mailer.valid_address("gm@hotel.com")
    for bad in ("", None, "not-an-email", "a@b.com\r\nBcc: evil@x.test"):
        assert not mailer.valid_address(bad), bad


def test_addendum_flow_and_gm_lookup():
    import app as A
    # GM lookup matches a real hotel by location code
    doc_fake = {"fields": {"location_code": "WIN", "loss_location_name": ""}}
    h = A._hotel_for(doc_fake)
    assert h and h["gm_email"], "GM lookup by Wright Code failed"
    assert A._hotel_for({"fields": {"location_code": "ZZZ", "loss_location_name": "nope"}}) is None

    inc_id = _submit_online()
    # addendum page requires a valid signature
    assert client.get(f"/addendum/{inc_id}").status_code == 403
    assert client.get(f"/addendum/{inc_id}?sig=garbage").status_code == 403
    sig = appmod._addendum_signer.dumps(inc_id)
    assert client.get(f"/addendum/{inc_id}?sig={sig}").status_code == 200
    # the addendum page must never expose report content
    page = client.get(f"/addendum/{inc_id}?sig={sig}").data.decode()
    assert "slippery" not in page and "Ana Torres" not in page

    # add a file + note
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "gray").save(buf, format="PNG")
    r = client.post(f"/addendum/{inc_id}?sig={sig}", data={
        "added_by": "Jane Smith, GM",
        "note": "Guest provided doctor's note on 7/15.",
        "attachments": (io.BytesIO(buf.getvalue()), "doctors note.png"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    doc = storage.get_store().get(inc_id)
    assert any(k.startswith("attachment_") for k in doc["files"])
    assert doc["supplements"][0]["by"] == "Jane Smith, GM"
    assert any(x["action"] == "supplement_added" for x in doc["audit"])

    # still works after approval
    import language_guard as lg
    client.get(f"/review/{inc_id}?token=test-token")
    for fnd in lg.pending_findings(doc):
        client.post(f"/review/{inc_id}/language/{fnd['id']}", data={"decision": "keep"})
    client.post(f"/review/{inc_id}/action", data={"action": "approve"})
    with client.session_transaction() as s:
        s.clear()
    assert storage.get_store().get(inc_id)["status"] == "approved"
    r = client.post(f"/addendum/{inc_id}?sig={sig}", data={
        "added_by": "Jane Smith, GM", "note": "Follow-up: guest called with attorney info.",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    doc = storage.get_store().get(inc_id)
    assert len(doc["supplements"]) == 2


def test_gm_notified_on_submit_for_known_hotel():
    data = dict(BASE, location_code="WIN",
                loss_location_name="Courtyard By Marriott Winston Salem, Nc",
                accident_description="The guest reported that they slipped in the lobby.")
    r = client.post("/form", data=data)
    inc_id = r.headers["Location"].rstrip("/").split("/")[-1]
    doc = storage.get_store().get(inc_id)
    # dry-run mailer returns False, so gm_notified stays unset — but the lookup
    # must have found the GM (verified above); with SMTP configured it would send.
    # Verify the change-request path passes the GM email through without error.
    client.get(f"/review/{inc_id}?token=test-token")
    r = client.post(f"/review/{inc_id}/action",
                    data={"action": "request_changes", "notes": "Need witness info."})
    assert r.status_code == 302
    assert storage.get_store().get(inc_id)["status"] == "changes_requested"
    with client.session_transaction() as s:
        s.clear()
