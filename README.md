# Incident Reporting Portal (General Liability)

Hotel GL incident reporting built around the Broadspire General Liability
Reporting Form (Starr). Same skeleton as the capital-authorization and
expense-approval apps: Flask + gunicorn on Cloud Run, Firestore + GCS durable /
SQLite + `./storage` local, provider-switchable AI extraction, SMTP mailer,
`ADMIN_TOKEN`-gated reviewer. The expense app's code-validator is replaced by
the **language guard** (`language_guard.py` + `language_rules.json`).

## Flow

1. **Intake (public):** online form, Excel upload (→ PDF via LibreOffice), or
   PDF/photos (→ merged PDF). Uploads are auto-read into the same structured
   record (official workbook: deterministic cell read; PDFs/photos: AI vision).
   Reporter confirms the parsed record before submitting. Every path also
   accepts optional supporting attachments (incident photos, CCTV stills,
   receipts, documents) — stored verbatim with the record, shown to the
   reviewer, never AI-read or merged into the form PDF.
2. **Checks:** completeness on all mandatory `*` fields (`fields.py`), and the
   language guard on the four narrative fields. Approval is blocked until the
   checklist is green and every language finding has an explicit
   Accept / Edit / Keep decision. Original wording is preserved in
   `original_narratives`; every decision and field edit is audited.
3. **Review:** email to `APPROVER_EMAIL` with a signed link. Review screen:
   original on the left, editable record on the right, completeness +
   language panels, Approve / Request changes / Reject.
4. **Approval outputs:** filled Broadspire PDF (official artifact — send to
   nol@choosebroadspire.com, fax 1-800-245-9927, or FileAClaim), filled Excel
   and Word convenience exports, and one row appended to the incident register
   (Excel + Firestore/SQLite). Auto-send to carrier is `AUTO_SEND_TO_CARRIER`
   (off by default).

## Local dev

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit; leave GCS_BUCKET/FIRESTORE_COLLECTION unset for SQLite
set -a; . ./.env; set +a
python app.py          # http://localhost:8080  (reviewer: /admin?token=$ADMIN_TOKEN)
```

Without LibreOffice installed locally, Excel→PDF and the filled-PDF output are
skipped (the filled Excel/Word still generate); everything works in the container.
Without `ANTHROPIC_API_KEY`, uploads skip AI extraction and the reporter fills
the confirm screen manually. Without `EMAIL_SENDER`, mail is logged, not sent.

Tests: `pip install pytest && pytest tests/ -q`

## Deploy (matches capital/expense)

```bash
gcloud run deploy incidents \
  --source . --project capital-processor --region us-central1 \
  --allow-unauthenticated --max-instances 1 --memory 1Gi --timeout 300
```

Then set env vars on the service (see `.env.example`): `ADMIN_TOKEN`,
`FLASK_SECRET`, `ANTHROPIC_API_KEY`, `APPROVER_EMAIL`/`APPROVER_NAME`,
`EMAIL_SENDER`/`EMAIL_PASSWORD`, `SMTP_SERVER`/`SMTP_PORT`, `BASE_URL`,
`GCS_BUCKET=incidents-capital-processor`, `FIRESTORE_COLLECTION=incident_reports`.
`--allow-unauthenticated` is correct: submit pages are public; reviewer routes
require `ADMIN_TOKEN` or a signed review link.

One-time GCP setup: create the GCS bucket and enable Firestore in project
`capital-processor` (both already enabled if the expense app runs there).

## Tuning without deploys

- **Language rulebook:** `language_rules.json` — attribution markers,
  fault/causation vocabulary, characterization terms, apology phrases, form
  hints. Editorial risk-management practice, not legal advice; have risk/counsel
  confirm the house rules.
- **Field map / mandatory rules / xlsx fill cells:** `fields.py`
  (`FIELDS` + `CONDITIONS`). The xlsx cell coordinates target
  `assets/general_liability_reporting_form_Starr.xlsx`; verify one filled PDF
  against the blank form after any carrier form revision.

## Smoke test after deploy

1. Submit one report per intake path (form / xlsx / photo).
2. Confirm the reviewer email arrives and the link opens `/review/<id>`.
3. Check `/admin` is 403 without the token.
4. Approve one report; verify the filled PDF and a new register row.
5. Redeploy; confirm the record and files survive (Firestore + GCS).

## Portal page (later, optional)

Ship this service standalone first. Then add a small landing page linking
Capital / Expense / Incident. Merging into one Flask app (blueprints, shared
queue) is a separate phase, only if a unified "waiting on me" queue is needed.
